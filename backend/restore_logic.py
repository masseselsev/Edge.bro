import os
from typing import Dict, Any
from core.db_session import session_scope
from models import Settings, TaskLog, Node
from restore_utils import get_archive_total_files, recreate_postgres_log_dirs
from core.task_log import log_to_task


def execute_restore(task_obj: Any, node_id: int, archive_name: str, target_dev: str, keep_network_configs: bool = True, wipe_mac_bindings: bool = False) -> Dict[str, Any]:
    """
    Executes the bare-metal restore partition flashing, filesystem formatting,
    Borg backup extraction, and network wildcard injection options.
    """
    from core.disk_ops import format_and_restore

    task_id = task_obj.request.id

    # A bare-metal restore is partitioning, formatting and a full borg extract:
    # tens of minutes of disk I/O. Everything the restore needs is read here,
    # in one scope, so none of it runs with a connection checked out. See
    # core.db_session.
    with session_scope() as db:
        node = db.query(Node).filter(Node.id == node_id).first()
        if not node:
            return {"status": "FAILED", "error": "Node not found"}

        if not db.query(TaskLog).filter(TaskLog.id == task_id).first():
            db.add(TaskLog(id=task_id, task_type="RESTORE", status="RUNNING", log_output=""))

        efi_uuid = node.efi_uuid
        network_iface = node.network_iface
        partitions = node.partition_layout

        total_files = get_archive_total_files(db, archive_name)

        settings_obj = db.query(Settings).first()
        exclusions_list = list(settings_obj.global_exclusions or []) if settings_obj else []
        orchestrator_ip = settings_obj.orchestrator_ip if settings_obj else None
        server_ips = list(settings_obj.server_ips or []) if settings_obj else None

    # Double check if EFI UUID is collected
    if not efi_uuid:
        log_to_task(task_id, "ERROR: EFI partition UUID is missing from database. Aborting restore to prevent data loss.", status="FAILED")
        return {"status": "FAILED", "error": "Missing EFI UUID"}

    try:
        # Reconstruct default 5-partition layout
        if not partitions:
            partitions = [
                {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": efi_uuid or "458C-37BB", "size_bytes": 512 * 1024 * 1024},
                {"name": "boot", "mount": "/boot", "fstype": "ext2", "label": "edgeboot", "uuid": "", "size_bytes": 1024 * 1024 * 1024},
                {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 30 * 1024 * 1024 * 1024},
                {"name": "log", "mount": "/var/log/edge", "fstype": "ext4", "label": "edgelog", "uuid": "", "size_bytes": 5 * 1024 * 1024 * 1024},
                {"name": "storage", "mount": "/var/opt/edge", "fstype": "ext4", "label": "edgestor", "uuid": "", "size_bytes": 0} # 0 means remaining
            ]

        repo_path = "/data/borg/fleet"

        def logger_callback(msg: str, prog: int = None, status: str = None):
            if prog is not None:
                log_to_task(task_id, f"[PROGRESS] {prog}:{msg}", status=status)
            else:
                log_to_task(task_id, msg, status=status)

        if not orchestrator_ip:
            orchestrator_ip = os.getenv("ORCHESTRATOR_IP")
        available_ips = ",".join(server_ips) if server_ips else None

        result = format_and_restore(
            target_dev=target_dev,
            partitions=partitions,
            efi_uuid=efi_uuid or "458C-37BB",
            archive_name=archive_name,
            repo_path=repo_path,
            keep_network_configs=keep_network_configs,
            wipe_mac_bindings=wipe_mac_bindings,
            network_iface=network_iface,
            total_files=total_files,
            log_callback=logger_callback,
            exclusions=exclusions_list,
            orchestrator_ip=orchestrator_ip,
            available_server_ips=available_ips
        )

        if result["status"] != "SUCCESS":
            return result

        # 6b. Recreate PostgreSQL log directories if they point to custom locations
        recreate_postgres_log_dirs(task_id, "/mnt/target")
        with session_scope() as db:
            node = db.query(Node).filter(Node.id == node_id).first()
            if node:
                node.status = "RESTORED"
        return {"status": "SUCCESS"}

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        log_to_task(task_id, error_msg, status="FAILED")
        return {"status": "FAILED", "error": str(e)}
