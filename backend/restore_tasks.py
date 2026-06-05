import os
import json
import subprocess
from typing import Dict, Any
from sqlalchemy.orm import Session
from celery import shared_task

from database import SessionLocal
from models import Node, TaskLog, BackupHistory
from restore_logic import execute_restore

@shared_task(bind=True)
def flash_restore_device(self, node_id: int, archive_name: str, target_dev: str, keep_network_configs: bool = True, wipe_mac_bindings: bool = False) -> Dict[str, Any]:
    """
    Celery task running locally on the worker in privileged mode.
    Wipes target device, partitions GPT, formats ESP with historical UUID,
    formats Root, extracts Borg backup, injects drift-preventive netconfig,
    and runs grub bootloader inside chroot.
    """
    return execute_restore(self, node_id, archive_name, target_dev, keep_network_configs, wipe_mac_bindings)

@shared_task(bind=True)
def purge_node_archives(self, node_id: int) -> Dict[str, Any]:
    """
    Celery task to delete all Borg archives for a specific node.
    Preserves the initialized repository — only removes archive snapshots.
    Also cleans up related BackupHistory records from the database.
    """
    from tasks import log_to_task, fix_repo_permissions
    
    task_id = self.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="PURGE", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    repo_path = "/data/borg/fleet"
    log_to_task(task_id, f"Starting archive purge for node {node.hostname}...")

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    try:
        # Check if repo exists and is initialized
        if not os.path.exists(repo_path) or not os.path.exists(os.path.join(repo_path, "config")):
            log_to_task(task_id, "No archives to purge (repository does not exist or is not initialized).", status="SUCCESS")
            # Clean up database history records for this node
            purged_rows = db.query(BackupHistory).filter(
                BackupHistory.node_id == node_id
            ).delete()
            node.last_backup = None
            db.commit()
            db.close()
            return {"status": "SUCCESS", "deleted": 0}

        # List all archives in the repository
        list_cmd = ["borg", "list", "--json", repo_path]
        list_res = subprocess.run(list_cmd, env=env, capture_output=True, text=True)

        if list_res.returncode != 0:
            log_to_task(task_id, f"Failed to list archives: {list_res.stderr}", status="FAILED")
            db.close()
            return {"status": "FAILED", "error": list_res.stderr}

        all_archives = json.loads(list_res.stdout).get("archives", [])
        archives = [a for a in all_archives if a["name"].startswith(f"{node.hostname}-")]
        log_to_task(task_id, f"Found {len(archives)} archive(s) belonging to {node.hostname} to delete.")

        if not archives:
            log_to_task(task_id, "No archives to purge.", status="SUCCESS")
            # Clean up database history records for this node
            purged_rows = db.query(BackupHistory).filter(
                BackupHistory.node_id == node_id
            ).delete()
            node.last_backup = None
            db.commit()
            db.close()
            return {"status": "SUCCESS", "deleted": 0}

        # Delete each archive individually
        deleted_count = 0
        for archive in archives:
            name = archive["name"]
            del_cmd = ["borg", "delete", f"{repo_path}::{name}"]
            del_res = subprocess.run(del_cmd, env=env, capture_output=True, text=True)
            if del_res.returncode == 0:
                deleted_count += 1
                log_to_task(task_id, f"Deleted archive: {name}")
            else:
                log_to_task(task_id, f"Failed to delete archive {name}: {del_res.stderr}")

        # Run compaction to reclaim disk space immediately if any archives were deleted
        if deleted_count > 0:
            log_to_task(task_id, "Compacting Borg repository to reclaim disk space...")
            compact_cmd = ["borg", "compact", repo_path]
            compact_res = subprocess.run(compact_cmd, env=env, capture_output=True, text=True)
            if compact_res.returncode == 0:
                log_to_task(task_id, "Repository compaction completed successfully.")
            else:
                log_to_task(task_id, f"Repository compaction failed: {compact_res.stderr}")

        # Clean up database history records for this node
        purged_rows = db.query(BackupHistory).filter(
            BackupHistory.node_id == node_id
        ).delete()
        node.last_backup = None
        db.commit()

        log_to_task(task_id, f"Purge complete: {deleted_count}/{len(archives)} archives deleted, {purged_rows} DB records removed.", status="SUCCESS")
        return {"status": "SUCCESS", "deleted": deleted_count}
    except Exception as e:
        log_to_task(task_id, f"Exception during purge: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        fix_repo_permissions(repo_path)
        db.close()
