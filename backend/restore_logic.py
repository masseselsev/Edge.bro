import os
import shutil
import subprocess
import json
from typing import Dict, Any
from sqlalchemy.orm import Session
from database import SessionLocal
from models import TaskLog, Node
from restore_utils import get_archive_total_files, recreate_postgres_log_dirs


def execute_restore(task_obj: Any, node_id: int, archive_name: str, target_dev: str, keep_network_configs: bool = True, wipe_mac_bindings: bool = False) -> Dict[str, Any]:
    """
    Executes the bare-metal restore partition flashing, filesystem formatting,
    Borg backup extraction, and network wildcard injection options.
    """
    from tasks import log_to_task
    from core.disk_ops import format_and_restore

    task_id = task_obj.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
    if not task_log:
        task_log = TaskLog(id=task_id, task_type="RESTORE", status="RUNNING", log_output="")
        db.add(task_log)
        db.commit()

    # Double check if EFI UUID is collected
    if not node.efi_uuid:
        log_to_task(task_id, "ERROR: EFI partition UUID is missing from database. Aborting restore to prevent data loss.", status="FAILED")
        db.close()
        return {"status": "FAILED", "error": "Missing EFI UUID"}

    try:
        # Reconstruct default 5-partition layout
        partitions = node.partition_layout
        if not partitions:
            partitions = [
                {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": node.efi_uuid or "458C-37BB", "size_bytes": 512 * 1024 * 1024},
                {"name": "boot", "mount": "/boot", "fstype": "ext2", "label": "edgeboot", "uuid": "", "size_bytes": 1024 * 1024 * 1024},
                {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 30 * 1024 * 1024 * 1024},
                {"name": "log", "mount": "/var/log/edge", "fstype": "ext4", "label": "edgelog", "uuid": "", "size_bytes": 5 * 1024 * 1024 * 1024},
                {"name": "storage", "mount": "/var/opt/edge", "fstype": "ext4", "label": "edgestor", "uuid": "", "size_bytes": 0} # 0 means remaining
            ]

        total_files = get_archive_total_files(db, archive_name)
        repo_path = "/data/borg/fleet"

        def logger_callback(msg: str, prog: int = None, status: str = None):
            if prog is not None:
                log_to_task(task_id, f"[PROGRESS] {prog}:{msg}", status=status)
            else:
                log_to_task(task_id, msg, status=status)

        result = format_and_restore(
            target_dev=target_dev,
            partitions=partitions,
            efi_uuid=node.efi_uuid or "458C-37BB",
            archive_name=archive_name,
            repo_path=repo_path,
            keep_network_configs=keep_network_configs,
            wipe_mac_bindings=wipe_mac_bindings,
            network_iface=node.network_iface,
            total_files=total_files,
            log_callback=logger_callback
        )

        if result["status"] == "SUCCESS":
            # 6b. Recreate PostgreSQL log directories if they point to custom locations
            recreate_postgres_log_dirs(task_id, "/mnt/target")
            return {"status": "SUCCESS"}
        else:
            return result

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        log_to_task(task_id, error_msg, status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
