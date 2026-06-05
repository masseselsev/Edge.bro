import os
import json
import subprocess
from sqlalchemy.orm import Session
from database import SessionLocal

def get_archive_total_files(db: Session, archive_name: str) -> int:
    """Reads backup history for the archive and extracts the total file count from its JSON log."""
    from models import BackupHistory
    history = db.query(BackupHistory).filter(BackupHistory.archive_name == archive_name).first()
    if history and history.log_output:
        try:
            data = json.loads(history.log_output)
            return int(data.get("archive", {}).get("stats", {}).get("nfiles", 0))
        except Exception:
            pass
    return 0

def recreate_postgres_log_dirs(task_id: str, target_mnt: str) -> None:
    """Recreates custom PostgreSQL log directories inside target if they are symlinked."""
    from tasks import log_to_task
    log_to_task(task_id, "Checking for custom PostgreSQL log directories to recreate...")
    log_to_task(task_id, "[PROGRESS] 92:Checking database configuration...")
    try:
        pg_etc_dir = os.path.join(target_mnt, "etc/postgresql")
        if os.path.exists(pg_etc_dir):
            for version in os.listdir(pg_etc_dir):
                version_path = os.path.join(pg_etc_dir, version)
                if os.path.isdir(version_path):
                    for cluster in os.listdir(version_path):
                        cluster_path = os.path.join(version_path, cluster)
                        log_symlink = os.path.join(cluster_path, "log")
                        if os.path.islink(log_symlink):
                            target_log_path = os.readlink(log_symlink)
                            log_dir_in_chroot = os.path.dirname(target_log_path)
                            log_dir_host = os.path.join(target_mnt, log_dir_in_chroot.lstrip("/"))
                            if not os.path.exists(log_dir_host):
                                log_to_task(task_id, f"Recreating custom PostgreSQL log directory: {log_dir_in_chroot}")
                                os.makedirs(log_dir_host, exist_ok=True)
                                subprocess.run(["chroot", target_mnt, "chown", "postgres:postgres", log_dir_in_chroot], check=True)
                                subprocess.run(["chroot", target_mnt, "chmod", "775", log_dir_in_chroot], check=True)
    except Exception as e:
        log_to_task(task_id, f"WARNING: Failed to recreate custom PostgreSQL log directories: {str(e)}")
