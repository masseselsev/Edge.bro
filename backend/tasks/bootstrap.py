import os
import subprocess
import json
from typing import Dict, Any
from sqlalchemy.orm import Session
from models import Node, TaskLog, Settings
from celery_app import celery_app
from core import ssh_keys
import tasks


def sync_node_key(task_id: str, node, new_pubkey: str) -> None:
    """Point the orchestrator's authorized_keys at the node's current key.

    A node that was re-imaged presents a new key; the grant for its previous
    key is revoked first, so a re-image cannot leave a live entry behind.
    """
    if not new_pubkey:
        tasks.log_to_task(
            task_id, "WARNING: node returned no SSH public key; nothing to authorize"
        )
        return

    new_fp = ssh_keys.fingerprint(new_pubkey)
    old_pubkey = node.ssh_pub_key

    if old_pubkey:
        try:
            if ssh_keys.fingerprint(old_pubkey) != new_fp:
                action = ssh_keys.revoke(ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS, old_pubkey)
                tasks.log_to_task(
                    task_id,
                    f"Node presented a new SSH key; previous grant {action.value} "
                    f"({ssh_keys.fingerprint(old_pubkey)})",
                )
        except ValueError:
            tasks.log_to_task(
                task_id, "WARNING: stored SSH key is unparseable; skipping revoke"
            )

    action = ssh_keys.authorize(
        ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS,
        new_pubkey,
        options=ssh_keys.BORG_SERVE_OPTIONS,
        tag=ssh_keys.node_tag(node.id),
    )
    tasks.fix_ssh_permissions()
    tasks.log_to_task(
        task_id,
        f"Borg access for {node.hostname}: {action.value} {new_fp} "
        f"tag={ssh_keys.node_tag(node.id)}",
        status="SUCCESS",
    )


@celery_app.task(bind=True, name="tasks.run_bootstrap_task")
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
        parsed_orch = ssh_keys.parse_line(orchestrator_pub_key)
        if parsed_orch is None:
            raise ValueError("orchestrator public key is unparseable")
        # Pass the key already normalized so the playbook's raw shell block
        # never has to cope with a comment containing shell metacharacters.
        orchestrator_pub_key = f"{parsed_orch.keytype} {parsed_orch.blob}"
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
        try:
            sync_node_key(task_id, node, ssh_pub_key)
        except Exception as e:
            tasks.log_to_task(
                task_id, f"WARNING: Failed to sync node SSH key: {str(e)}", status="FAILED"
            )
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

@celery_app.task(name="tasks.auto_retry_bootstrap_task")
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


@celery_app.task(bind=True, name="tasks.revoke_node_access_task")
def revoke_node_access_task(self, hostname: str, ip_address: str, ssh_port: int) -> Dict[str, Any]:
    """Best-effort removal of the orchestrator's key from a deleted node.

    A decommissioned box is often already switched off, so an unreachable host
    is a logged outcome, not an error.
    """
    task_id = self.request.id
    db: Session = tasks.SessionLocal()
    try:
        task_log = TaskLog(
            id=task_id, task_type="REVOKE_ACCESS", status="RUNNING", log_output=""
        )
        db.add(task_log)
        db.commit()
    finally:
        db.close()

    tasks.log_to_task(task_id, f"Revoking orchestrator access from {hostname} ({ip_address})")
    try:
        res = tasks.run_ansible_playbook(
            task_id=task_id,
            playbook_name="revoke_access.yml",
            host_ip=ip_address,
            ssh_port=ssh_port,
            extra_vars={},
            ssh_key_path="/root/.ssh/id_ed25519",
        )
    except Exception as e:
        tasks.log_to_task(
            task_id,
            f"Could not reach {hostname}; orchestrator key may remain on that host: {str(e)}",
            status="FAILED",
        )
        return {"status": "FAILED", "error": str(e)}

    if res["status"] == "SUCCESS":
        tasks.log_to_task(
            task_id, f"Orchestrator access revoked from {hostname}", status="SUCCESS"
        )
    else:
        tasks.log_to_task(
            task_id,
            f"Host {hostname} unreachable; orchestrator key may remain on it",
            status="FAILED",
        )
    return res
