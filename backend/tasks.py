import os
import shutil
import subprocess
import json
import logging
import uuid
from typing import Dict, Any, List, Optional
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

# Initialize Celery
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

# Configure Celery Beat for global daily prune
celery_app.conf.beat_schedule = {
    'global-daily-prune-task': {
        'task': 'tasks.global_daily_prune',
        'schedule': crontab(hour=3, minute=0), # Run at 3:00 AM daily
    },
}
celery_app.conf.timezone = 'UTC'

def log_to_task(task_id: str, message: str, status: Optional[str] = None) -> None:
    """
    Appends a log line to the specified TaskLog record in the database.

    Args:
        task_id: The TaskLog UUID.
        message: The log message to append.
        status: Optional status to explicitly set (e.g. SUCCESS, FAILED).
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

def ensure_orchestrator_ssh_key() -> str:
    """
    Ensures that the Orchestrator's SSH private/public keypair exists in /root/.ssh.
    Generates it if it is missing.
    Returns the public key content.
    """
    ssh_dir = "/root/.ssh"
    priv_key = os.path.join(ssh_dir, "id_ed25519")
    pub_key = os.path.join(ssh_dir, "id_ed25519.pub")
    
    os.makedirs(ssh_dir, exist_ok=True)
    
    if not os.path.exists(priv_key):
        logger.info("Generating new Ed25519 keypair for Orchestrator...")
        try:
            # Generate keypair
            subprocess.run([
                "ssh-keygen", "-t", "ed25519", "-N", "", "-f", priv_key
            ], check=True, capture_output=True)
            # Set permissions
            os.chmod(ssh_dir, 0o700)
            os.chmod(priv_key, 0o600)
        except Exception as e:
            logger.error(f"Failed to generate SSH keypair: {str(e)}")
            raise e
            
    # Read public key
    try:
        with open(pub_key, "r") as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"Failed to read SSH public key: {str(e)}")
        raise e

@celery_app.task(bind=True)
def run_bootstrap_task(self, node_id: int, ssh_password: str, bootstrap_user: str) -> Dict[str, Any]:
    """
    Celery task to run the Node bootstrapping process using Ansible.

    Args:
        node_id: ID of the Node database record.
        ssh_password: Temporary SSH password for bootstrap.
        bootstrap_user: Temporary SSH user for bootstrap.

    Returns:
        Status result dictionary.
    """
    task_id = self.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    # Initialize TaskLog
    task_log = TaskLog(id=task_id, task_type="BOOTSTRAP", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    log_to_task(task_id, f"Starting bootstrap for {node.hostname} ({node.ip_address})")

    # Ensure orchestrator SSH key is generated and get its public key
    try:
        orchestrator_pub_key = ensure_orchestrator_ssh_key()
    except Exception as e:
        log_to_task(task_id, f"WARNING: Failed to ensure orchestrator SSH key: {str(e)}")
        orchestrator_pub_key = ""

    # Run playbook
    res = run_ansible_playbook(
        task_id=task_id,
        playbook_name="bootstrap.yml",
        host_ip=node.ip_address,
        ssh_port=node.ssh_port,
        extra_vars={
            "bootstrap_user": bootstrap_user,
            "orchestrator_ssh_pub_key": orchestrator_pub_key
        },
        ssh_password=ssh_password
    )

    if res["status"] == "SUCCESS":
        ssh_pub_key = res["parsed_data"].get("ssh_pub_key")
        node.ssh_pub_key = ssh_pub_key
        node.status = "NEEDS_FIX" # Proceed to Auto-Prepare stage next
        db.commit()
        log_to_task(task_id, "Bootstrap completed successfully. Public SSH key fetched.")

        # Append key to Borg Server authorized_keys
        try:
            authorized_keys_path = "/root/.ssh/authorized_keys"
            os.makedirs(os.path.dirname(authorized_keys_path), exist_ok=True)
            command_restriction = (
                f'command="borg serve --restrict-to-path /data/borg/{node.hostname}",'
                f'no-port-forwarding,no-X11-forwarding,no-pty '
            )
            entry = f"{command_restriction}{ssh_pub_key}\n"
            with open(authorized_keys_path, "a") as f:
                f.write(entry)
            log_to_task(task_id, "Borg SSH authorized_keys updated with forced command restriction.", status="SUCCESS")
        except Exception as e:
            log_to_task(task_id, f"WARNING: Failed to append key to authorized_keys: {str(e)}", status="FAILED")
    else:
        node.status = "NEEDS_BOOTSTRAP"
        db.commit()
        log_to_task(task_id, "Bootstrap task failed.", status="FAILED")

    db.close()
    return res

@celery_app.task(bind=True)
def run_prepare_task(self, node_id: int) -> Dict[str, Any]:
    """
    Celery task to run the Auto-Prepare disk labels playbook on the node.

    Args:
        node_id: ID of the Node database record.

    Returns:
        Status result dictionary.
    """
    task_id = self.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="PREPARE", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    log_to_task(task_id, f"Starting auto-prepare for {node.hostname} ({node.ip_address})")

    # Run playbook (uses Orchestrator's SSH control key)
    # Orchestrator SSH control key is assumed to be in /root/.ssh/id_ed25519
    res = run_ansible_playbook(
        task_id=task_id,
        playbook_name="prepare.yml",
        host_ip=node.ip_address,
        ssh_port=node.ssh_port,
        extra_vars={},
        ssh_key_path="/root/.ssh/id_ed25519"
    )

    if res["status"] == "SUCCESS":
        node.disk_type = res["parsed_data"].get("disk_type", "UNKNOWN")
        node.network_iface = res["parsed_data"].get("network_iface")
        node.efi_uuid = res["parsed_data"].get("efi_uuid")
        node.status = "READY"
        db.commit()
        log_to_task(task_id, f"Auto-prepare finished. Disk type: {node.disk_type}, EFI UUID: {node.efi_uuid}, Interface: {node.network_iface}", status="SUCCESS")
    else:
        node.status = "NEEDS_FIX"
        db.commit()
        log_to_task(task_id, "Auto-prepare task failed.", status="FAILED")

    db.close()
    return res

@celery_app.task(bind=True)
def run_backup_task(self, node_id: int) -> Dict[str, Any]:
    """
    Triggers remote backup execution on the node pushing to the central Borg server,
    then updates Database history.

    Args:
        node_id: ID of the Node database record.

    Returns:
        Status dictionary.
    """
    task_id = self.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings()
        db.add(settings)
        db.commit()

    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="BACKUP", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    log_to_task(task_id, f"Initiating Borg backup for {node.hostname}...")

    # Determine Orchestrator internal/external IP from context or use host default route IP
    # We can fetch this host's IP that routes to the edge node
    orchestrator_ip = os.getenv("ORCHESTRATOR_IP")
    if not orchestrator_ip:
        try:
            route_cmd = f"ip route get {node.ip_address}"
            route_out = subprocess.check_output(route_cmd, shell=True, text=True)
            orchestrator_ip = route_out.split("src")[1].split()[0]
        except Exception:
            orchestrator_ip = "127.0.0.1"

    archive_name = f"{node.hostname}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    borg_repo_url = f"ssh://borg@{orchestrator_ip}:{settings.borg_ssh_port}/data/borg/{node.hostname}"

    # Connect via SSH to the edge node and execute Borg backup pushing to Central server
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", str(node.ssh_port),
        "-i", "/root/.ssh/id_ed25519",
        f"root@{node.ip_address}",
        f"sudo -u borg BORG_RSH='ssh -o StrictHostKeyChecking=no' BORG_PASSPHRASE='{os.getenv('BORG_PASSPHRASE')}' borg create --json --stats {borg_repo_url}::{archive_name} / --exclude {settings.global_exclusions}"
    ]

    log_to_task(task_id, f"Running remote command on node: {' '.join(ssh_cmd[:6])} [COMMAND MASKED]")

    try:
        process = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()

        log_to_task(task_id, f"Remote execution stdout:\n{stdout}")
        if stderr:
            log_to_task(task_id, f"Remote execution stderr:\n{stderr}")

        if process.returncode == 0:
            # Parse sizes from JSON
            original_size = 0
            deduplicated_size = 0
            try:
                data = json.loads(stdout)
                original_size = data.get("stats", {}).get("original_size", 0)
                deduplicated_size = data.get("stats", {}).get("deduplicated_size", 0)
            except Exception:
                # If stdout is not direct JSON but includes logs, search for lines
                log_to_task(task_id, "Failed to parse JSON directly; estimating size metrics.")

            history = BackupHistory(
                node_id=node.id,
                archive_name=archive_name,
                original_size=original_size,
                deduplicated_size=deduplicated_size,
                status="SUCCESS",
                log_output=stdout + "\n" + stderr
            )
            db.add(history)
            node.last_backup = datetime.utcnow()
            db.commit()

            log_to_task(task_id, "Backup completed successfully.", status="SUCCESS")
            return {"status": "SUCCESS", "archive": archive_name}
        else:
            history = BackupHistory(
                node_id=node.id,
                archive_name=archive_name,
                original_size=0,
                deduplicated_size=0,
                status="FAILED",
                log_output=stdout + "\n" + stderr
            )
            db.add(history)
            db.commit()
            log_to_task(task_id, "Backup execution failed.", status="FAILED")
            return {"status": "FAILED", "error": stderr}
    except Exception as e:
        log_to_task(task_id, f"Exception occurred during backup task: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()

@celery_app.task
def global_daily_prune() -> Dict[str, Any]:
    """
    Celery scheduled cron task running at 3:00 AM daily.
    Executes borg prune on all node repositories locally inside the shared volume.
    """
    db: Session = SessionLocal()
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings()

    nodes = db.query(Node).all()
    results = {}

    for node in nodes:
        repo_path = f"/data/borg/{node.hostname}"
        if not os.path.exists(repo_path):
            continue

        cmd = [
            "borg", "prune",
            "--keep-daily", str(settings.keep_daily),
            "--keep-weekly", str(settings.keep_weekly),
            "--keep-monthly", str(settings.keep_monthly),
            repo_path
        ]
        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

        try:
            res = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if res.returncode == 0:
                results[node.hostname] = "PRUNED"
                logger.info(f"Successfully pruned repository for {node.hostname}")
            else:
                results[node.hostname] = f"FAILED: {res.stderr}"
                logger.error(f"Failed to prune repository for {node.hostname}: {res.stderr}")
        except Exception as e:
            results[node.hostname] = f"EXCEPTION: {str(e)}"
            logger.error(f"Exception pruning {node.hostname}: {str(e)}")

    db.close()
    return results

@celery_app.task(bind=True)
def flash_restore_device(self, node_id: int, archive_name: str, target_dev: str) -> Dict[str, Any]:
    """
    Celery task running locally on the worker in privileged mode.
    Wipes target device, partitions GPT, formats ESP with historical UUID,
    formats Root, extracts Borg backup, injects drift-preventive netconfig,
    and runs grub bootloader inside chroot.

    Args:
        node_id: ID of the Node database record.
        archive_name: The Borg backup archive identifier.
        target_dev: Target block device name (e.g. /dev/sdb).

    Returns:
        Status result dictionary.
    """
    from restore_logic import execute_restore
    return execute_restore(self, node_id, archive_name, target_dev)
