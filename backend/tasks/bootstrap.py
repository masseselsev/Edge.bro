import os
import subprocess
import json
from typing import Dict, Any
from sqlalchemy.orm import Session
from models import Node, TaskLog, Settings
from celery_app import celery_app
import tasks

@celery_app.task(bind=True)
def run_bootstrap_task(self, node_id: int, ssh_password: str, bootstrap_user: str, force_orchestrator_proxy: bool = False) -> Dict[str, Any]:
    """
    Celery task to run the Node bootstrapping process using Ansible.
    """
    task_id = self.request.id
    db: Session = tasks.SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="BOOTSTRAP", status="RUNNING", node_id=node_id, log_output="")
    db.add(task_log)
    db.commit()
    tasks.log_to_task(task_id, f"Starting bootstrap for {node.hostname} ({node.ip_address})")
    try:
        orchestrator_pub_key = tasks.ensure_orchestrator_ssh_key()
    except Exception as e:
        tasks.log_to_task(task_id, f"WARNING: Failed to ensure orchestrator SSH key: {str(e)}")
        orchestrator_pub_key = ""
    settings = db.query(Settings).first()
    orchestrator_ip = settings.orchestrator_ip if settings else None
    if not orchestrator_ip:
        orchestrator_ip = os.getenv("ORCHESTRATOR_IP")
    if not orchestrator_ip:
        try:
            route_cmd = f"ip route get {node.ip_address}"
            route_out = subprocess.check_output(route_cmd, shell=True, text=True)
            orchestrator_ip = route_out.split("src")[1].split()[0]
        except Exception:
            orchestrator_ip = "127.0.0.1"

    res = tasks.run_ansible_playbook(
        task_id=task_id,
        playbook_name="bootstrap.yml",
        host_ip=node.ip_address,
        ssh_port=node.ssh_port,
        extra_vars={
            "bootstrap_user": bootstrap_user,
            "orchestrator_ssh_pub_key": orchestrator_pub_key,
            "orchestrator_ip": orchestrator_ip,
            "force_orchestrator_proxy": force_orchestrator_proxy
        },
        ssh_password=ssh_password
    )

    if res["status"] == "SUCCESS":
        ssh_pub_key = res["parsed_data"].get("ssh_pub_key")
        node.ssh_pub_key = ssh_pub_key
        
        # Save os_version and hardware details
        os_ver = res["parsed_data"].get("os_version")
        if os_ver:
            node.os_version = os_ver
        
        cpu_info = res["parsed_data"].get("cpu_info")
        if cpu_info:
            node.cpu_info = cpu_info
            
        mem_info = res["parsed_data"].get("memory_info")
        if mem_info:
            node.memory_info = mem_info
            
        edge_ver = res["parsed_data"].get("edge_version")
        if edge_ver:
            node.edge_version = edge_ver

        hasp_ver = res["parsed_data"].get("hasp_runtime_version")
        if hasp_ver:
            node.hasp_runtime_version = hasp_ver

        # Update hostname if detected
        detected_hostname = res["parsed_data"].get("hostname")
        if detected_hostname:
            existing_host = db.query(Node).filter(Node.hostname == detected_hostname, Node.id != node.id).first()
            if existing_host:
                node.hostname = f"{detected_hostname}-{node.id}"
            else:
                node.hostname = detected_hostname

        # Remove temporary credentials from Redis
        try:
            tasks.redis_client.delete(f"bootstrap_creds:{node.id}")
        except Exception as e:
            tasks.logger.error(f"Error deleting Redis credentials: {str(e)}")

        is_prep = res["parsed_data"].get("prepared") == "true"
        if is_prep:
            node.disk_type = res["parsed_data"].get("disk_type", "UNKNOWN")
            node.network_iface = res["parsed_data"].get("network_iface")
            node.efi_uuid = res["parsed_data"].get("efi_uuid")
            if "partition_layout" in res["parsed_data"]:
                node.partition_layout = res["parsed_data"]["partition_layout"]
        node.status = "READY" if is_prep else "NEEDS_FIX"
        db.commit()
        tasks.log_to_task(task_id, f"Bootstrap completed. {'Already prepared.' if is_prep else 'Key fetched.'}")

        # Append key to Borg Server authorized_keys
        try:
            authorized_keys_path = "/root/.ssh/authorized_keys"
            os.makedirs(os.path.dirname(authorized_keys_path), exist_ok=True)
            command_restriction = (
                f'command="borg serve --restrict-to-path /data/borg/fleet",'
                f'no-port-forwarding,no-X11-forwarding,no-pty '
            )
            entry = f"{command_restriction}{ssh_pub_key}\n"
            
            # Prevent duplicate key entries
            if os.path.exists(authorized_keys_path):
                with open(authorized_keys_path, "r") as f:
                    content = f.read()
            else:
                content = ""
                
            if ssh_pub_key not in content:
                with open(authorized_keys_path, "a") as f:
                    f.write(entry)
                    
            tasks.fix_ssh_permissions()
            tasks.log_to_task(task_id, "Borg SSH authorized_keys updated with forced command restriction.", status="SUCCESS")
        except Exception as e:
            tasks.log_to_task(task_id, f"WARNING: Failed to append key to authorized_keys: {str(e)}", status="FAILED")
    else:
        is_offline = False
        task_log_obj = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task_log_obj and task_log_obj.log_output:
            log_out_upper = task_log_obj.log_output.upper()
            if "UNREACHABLE" in log_out_upper or "COULD NOT RESOLVE" in log_out_upper or "CONNECTION TIMEOUT" in log_out_upper or "CONNECT TO HOST" in log_out_upper:
                is_offline = True
        
        if is_offline:
            node.status = "OFFLINE"
            try:
                import time
                tasks.redis_client.set(f"node_next_retry:{node.id}", int(time.time() + 300), ex=300)
            except Exception as e:
                tasks.logger.error(f"Error setting node_next_retry: {str(e)}")
        else:
            node.status = "NEEDS_BOOTSTRAP"
        db.commit()
        error_msg = "Bootstrap task failed."
        if task_log_obj and task_log_obj.log_output and "OS_UNSUPPORTED" in task_log_obj.log_output:
            for line in task_log_obj.log_output.splitlines():
                if "OS_UNSUPPORTED" in line:
                    error_msg = f"Bootstrap rejected: {line.strip()}"
                    break
        tasks.log_to_task(task_id, error_msg, status="FAILED")

    db.close()
    return res

@celery_app.task
def auto_retry_bootstrap_task() -> Dict[str, Any]:
    """
    Periodic task to check for OFFLINE nodes, retrieve credentials from Redis,
    and trigger bootstrap tasks for them.
    """
    db: Session = tasks.SessionLocal()
    try:
        offline_nodes = db.query(Node).filter(Node.status == "OFFLINE").all()
        triggered = []
        for node in offline_nodes:
            creds_json = tasks.redis_client.get(f"bootstrap_creds:{node.id}")
            if creds_json:
                creds = json.loads(creds_json)
                node.status = "NEEDS_BOOTSTRAP"
                db.commit()
                try:
                    tasks.redis_client.delete(f"node_next_retry:{node.id}")
                except Exception:
                    pass
                run_bootstrap_task.delay(
                    node.id,
                    creds["bootstrap_password"],
                    creds["bootstrap_user"],
                    creds.get("force_orchestrator_proxy", False)
                )
                triggered.append(node.id)
        return {"status": "SUCCESS", "triggered_node_ids": triggered}
    except Exception as e:
        tasks.logger.error(f"Error in auto_retry_bootstrap_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
