import os
import subprocess
import json
import logging
from typing import Dict, Any, List, Optional, Union, Callable
from datetime import datetime
from sqlalchemy.orm import Session
from database import SessionLocal
from models import TaskLog, Node, BackupHistory, Settings
from ansible_utils import run_ansible_playbook

from celery import Celery
from celery.schedules import crontab

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from database import setup_db_logging
    setup_db_logging()
except Exception as e:
    logger.error(f"Failed to setup DB logging for Celery worker: {str(e)}")

from celery.signals import after_setup_logger, after_setup_task_logger

@after_setup_logger.connect
def setup_celery_logging(logger, **kwargs):
    try:
        from database import setup_db_logging
        setup_db_logging()
    except Exception as e:
        logger.error(f"Failed to setup DB logging after celery logger setup: {str(e)}")

@after_setup_task_logger.connect
def setup_celery_task_logging(logger, **kwargs):
    try:
        from database import setup_db_logging
        setup_db_logging()
    except Exception as e:
        logger.error(f"Failed to setup DB logging after celery task logger setup: {str(e)}")


# Initialize Celery
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

import redis
redis_client = redis.Redis.from_url(REDIS_URL)

# Configure Celery Beat for global daily prune, auto retry, and scheduler tick
celery_app.conf.beat_schedule = {
    'global-daily-prune-task': {
        'task': 'backup_tasks.global_daily_prune',
        'schedule': crontab(hour=3, minute=0), # Run at 3:00 AM daily
    },
    'auto-retry-bootstrap-task': {
        'task': 'tasks.auto_retry_bootstrap_task',
        'schedule': 300.0, # Run every 5 minutes (300 seconds)
    },
    'scheduler-tick-task': {
        'task': 'tasks.scheduler_tick',
        'schedule': 60.0, # Run every minute
    },
}
celery_app.conf.timezone = 'UTC'

def log_to_task(task_id: str, message: str, status: Optional[str] = None) -> None:
    """
    Appends a log line to the specified TaskLog record in the database.
    """
    db: Session = SessionLocal()
    try:
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            task.log_output += f"[{timestamp}] {message}\n"
            if status:
                task.status = status
            elif task.status not in ("SUCCESS", "FAILED"):
                task.status = "RUNNING"
            db.commit()
    except Exception as e:
        logger.error(f"Error logging to task {task_id}: {str(e)}")
    finally:
        db.close()

def run_command_with_logging(
    task_id: str,
    cmd: Union[str, List[str]],
    shell: bool = False,
    on_log_line: Optional[Callable[[str], None]] = None
) -> None:
    """
    Runs a subprocess command and streams its stdout/stderr line-by-line
    to the TaskLog record via log_to_task.
    """
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    log_to_task(task_id, f"[EXEC] {cmd_str}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=shell,
        bufsize=1
    )

    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            log_line = line.rstrip("\r\n")
            log_to_task(task_id, log_line)
            if on_log_line:
                try:
                    on_log_line(log_line)
                except Exception as ex:
                    logger.error(f"Error in on_log_line callback: {str(ex)}")
        process.stdout.close()

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd)

def fix_ssh_permissions() -> None:
    """
    Ensures that the orchestrator SSH keys and authorized_keys file
    have correct permissions and ownership.
    """
    ssh_dir = "/root/.ssh"
    auth_keys = os.path.join(ssh_dir, "authorized_keys")
    try:
        if os.path.exists(ssh_dir):
            subprocess.run(["chown", "-R", "1000:1000", ssh_dir], check=True)
            os.chmod(ssh_dir, 0o700)
            if os.path.exists(auth_keys):
                os.chmod(auth_keys, 0o600)
            priv_key = os.path.join(ssh_dir, "id_ed25519")
            if os.path.exists(priv_key):
                os.chmod(priv_key, 0o600)
    except Exception as e:
        logger.error(f"Failed to fix SSH permissions: {str(e)}")

def ensure_orchestrator_ssh_key() -> str:
    """
    Ensures that the Orchestrator's SSH private/public keypair exists in /root/.ssh.
    """
    ssh_dir = "/root/.ssh"
    priv_key = os.path.join(ssh_dir, "id_ed25519")
    pub_key = os.path.join(ssh_dir, "id_ed25519.pub")
    
    os.makedirs(ssh_dir, exist_ok=True)
    
    if not os.path.exists(priv_key):
        logger.info("Generating new Ed25519 keypair for Orchestrator...")
        try:
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", priv_key], check=True, capture_output=True)
            os.chmod(ssh_dir, 0o700)
            os.chmod(priv_key, 0o600)
        except Exception as e:
            logger.error(f"Failed to generate SSH keypair: {str(e)}")
            raise e
    try:
        with open(pub_key, "r") as f:
            pub_key_content = f.read().strip()
        fix_ssh_permissions()
        return pub_key_content
    except Exception as e:
        logger.error(f"Failed to read SSH public key: {str(e)}")
        raise e

def fix_repo_permissions(repo_path: str) -> None:
    """Ensures repository files and their parent directories are owned by user borg (1000:1000)."""
    try:
        parent_dir = os.path.dirname(repo_path)
        if os.path.exists(parent_dir):
            subprocess.run(["chown", "1000:1000", parent_dir], check=True)
            subprocess.run(["chmod", "755", parent_dir], check=True)
            
        if os.path.exists(repo_path):
            subprocess.run(["chown", "-R", "1000:1000", repo_path], check=True)
    except Exception as e:
        logger.error(f"Failed to chown repo {repo_path}: {str(e)}")


@celery_app.task(bind=True)
def run_bootstrap_task(self, node_id: int, ssh_password: str, bootstrap_user: str) -> Dict[str, Any]:
    """
    Celery task to run the Node bootstrapping process using Ansible.
    """
    task_id = self.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="BOOTSTRAP", status="RUNNING", node_id=node_id, log_output="")
    db.add(task_log)
    db.commit()
    log_to_task(task_id, f"Starting bootstrap for {node.hostname} ({node.ip_address})")
    try:
        orchestrator_pub_key = ensure_orchestrator_ssh_key()
    except Exception as e:
        log_to_task(task_id, f"WARNING: Failed to ensure orchestrator SSH key: {str(e)}")
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

    res = run_ansible_playbook(
        task_id=task_id,
        playbook_name="bootstrap.yml",
        host_ip=node.ip_address,
        ssh_port=node.ssh_port,
        extra_vars={
            "bootstrap_user": bootstrap_user,
            "orchestrator_ssh_pub_key": orchestrator_pub_key,
            "orchestrator_ip": orchestrator_ip
        },
        ssh_password=ssh_password
    )

    if res["status"] == "SUCCESS":
        ssh_pub_key = res["parsed_data"].get("ssh_pub_key")
        node.ssh_pub_key = ssh_pub_key
        
        # Save os_version
        os_ver = res["parsed_data"].get("os_version")
        if os_ver:
            node.os_version = os_ver

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
            redis_client.delete(f"bootstrap_creds:{node.id}")
        except Exception as e:
            logger.error(f"Error deleting Redis credentials: {str(e)}")

        is_prep = res["parsed_data"].get("prepared") == "true"
        if is_prep:
            node.disk_type = res["parsed_data"].get("disk_type", "UNKNOWN")
            node.network_iface = res["parsed_data"].get("network_iface")
            node.efi_uuid = res["parsed_data"].get("efi_uuid")
            if "partition_layout" in res["parsed_data"]:
                node.partition_layout = res["parsed_data"]["partition_layout"]
        node.status = "READY" if is_prep else "NEEDS_FIX"
        db.commit()
        log_to_task(task_id, f"Bootstrap completed. {'Already prepared.' if is_prep else 'Key fetched.'}")

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
                    
            fix_ssh_permissions()
            log_to_task(task_id, "Borg SSH authorized_keys updated with forced command restriction.", status="SUCCESS")
        except Exception as e:
            log_to_task(task_id, f"WARNING: Failed to append key to authorized_keys: {str(e)}", status="FAILED")
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
                redis_client.set(f"node_next_retry:{node.id}", int(time.time() + 300), ex=300)
            except Exception as e:
                logger.error(f"Error setting node_next_retry: {str(e)}")
        else:
            node.status = "NEEDS_BOOTSTRAP"
        db.commit()
        error_msg = "Bootstrap task failed."
        if task_log_obj and task_log_obj.log_output and "OS_UNSUPPORTED" in task_log_obj.log_output:
            for line in task_log_obj.log_output.splitlines():
                if "OS_UNSUPPORTED" in line:
                    error_msg = f"Bootstrap rejected: {line.strip()}"
                    break
        log_to_task(task_id, error_msg, status="FAILED")

    db.close()
    return res


@celery_app.task
def auto_retry_bootstrap_task() -> Dict[str, Any]:
    """
    Periodic task to check for OFFLINE nodes, retrieve credentials from Redis,
    and trigger bootstrap tasks for them.
    """
    db: Session = SessionLocal()
    try:
        offline_nodes = db.query(Node).filter(Node.status == "OFFLINE").all()
        triggered = []
        for node in offline_nodes:
            creds_json = redis_client.get(f"bootstrap_creds:{node.id}")
            if creds_json:
                creds = json.loads(creds_json)
                node.status = "NEEDS_BOOTSTRAP"
                db.commit()
                try:
                    redis_client.delete(f"node_next_retry:{node.id}")
                except Exception:
                    pass
                run_bootstrap_task.delay(node.id, creds["bootstrap_password"], creds["bootstrap_user"])
                triggered.append(node.id)
        return {"status": "SUCCESS", "triggered_node_ids": triggered}
    except Exception as e:
        logger.error(f"Error in auto_retry_bootstrap_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()


@celery_app.task
def scheduler_tick() -> Dict[str, Any]:
    """
    Periodic task running every minute to evaluate node scheduling rules
    and trigger automated backups within defined group windows.
    """
    from core.scheduler import check_and_trigger_backups
    db: Session = SessionLocal()
    try:
        check_and_trigger_backups(db)
        return {"status": "SUCCESS"}
    except Exception as e:
        logger.error(f"Error in scheduler_tick: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()


# Import other tasks so they register with Celery automatically when this file is loaded
from backup_tasks import run_prepare_task, run_backup_task, global_daily_prune
from restore_tasks import flash_restore_device, purge_node_archives
import iso_tasks
