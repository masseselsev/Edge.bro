import os
import subprocess
import json
import logging
import redis
from typing import Dict, Any, List, Optional, Union, Callable
from datetime import datetime
from sqlalchemy.orm import Session
from database import SessionLocal
from models import TaskLog, Node, BackupHistory, Settings
from ansible_utils import run_ansible_playbook

from celery import Celery
from celery.schedules import crontab
from celery_app import celery_app, REDIS_URL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    'ping-nodes-task': {
        'task': 'tasks.ping_all_nodes_task',
        'schedule': 30.0, # Run every 30 seconds
    },
    'docker-system-cleanup-task': {
        'task': 'tasks.docker_system_cleanup_task',
        'schedule': crontab(hour=4, minute=0, day_of_week=0), # Run at 4:00 AM on Sunday
    },
    'db-task-log-prune-task': {
        'task': 'tasks.db_task_log_prune_task',
        'schedule': crontab(hour=3, minute=30), # Run at 3:30 AM daily
    },
    'ssh-key-audit-task': {
        'task': 'tasks.ssh_key_audit_task',
        'schedule': crontab(hour=3, minute=45), # Run at 3:45 AM daily
    },
    'monitoring-sweep-task': {
        'task': 'tasks.monitoring_sweep_task',
        # Hourly, picking up whatever is overdue rather than firing at a
        # per-node instant. With intervals measured in weeks an hour of slack
        # is irrelevant, and a sweep that just asks "who is overdue?" needs no
        # state and recovers by itself from an orchestrator that was down.
        'schedule': crontab(minute=20),
    },
    'monitoring-retention-task': {
        'task': 'tasks.monitoring_retention_task',
        'schedule': crontab(hour=4, minute=15), # Run at 4:15 AM daily
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
    parent_dir = os.path.dirname(repo_path)
    if os.path.exists(parent_dir):
        try:
            subprocess.run(["chown", "1000:1000", parent_dir], check=False)
            subprocess.run(["chmod", "755", parent_dir], check=False)
        except Exception as e:
            logger.warning(f"Could not chown parent directory {parent_dir}: {str(e)}")
            
    if os.path.exists(repo_path):
        try:
            subprocess.run(["chown", "-R", "1000:1000", repo_path], check=True)
        except Exception as e:
            logger.error(f"Failed to chown repo {repo_path}: {str(e)}")



# Expose task endpoints directly from tasks
from tasks.bootstrap import run_bootstrap_task, auto_retry_bootstrap_task, revoke_node_access_task
from tasks.ssh_audit import ssh_key_audit_task
from tasks.ping import ping_all_nodes_task, async_ping_ip
from tasks.scheduler import scheduler_tick
from tasks.cleanup import docker_system_cleanup_task, db_task_log_prune_task
from tasks.monitoring import (
    harvest_node_task,
    monitoring_retention_task,
    monitoring_sweep_task,
)

# Import other tasks so they register with Celery automatically when this file is loaded
from backup_tasks import run_prepare_task, run_backup_task, global_daily_prune
from restore_tasks import flash_restore_device, purge_node_archives
import iso_tasks

# Mock compatibility definitions for unit tests
builtins_open = open
open = builtins_open
