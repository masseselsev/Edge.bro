import os
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from models import TaskLog
from celery_app import celery_app
import tasks

@celery_app.task
def docker_system_cleanup_task() -> Dict[str, Any]:
    """
    Weekly cleanup task to prune unused Docker build cache and images.
    Requires mounting /var/run/docker.sock into the container.
    """
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        return {"status": "SKIPPED", "reason": "Docker socket not mounted"}
        
    try:
        # Prune builder cache
        res_build = subprocess.run([
            "curl", "--unix-socket", socket_path, "-X", "POST",
            "http://localhost/v1.41/build/prune?all=true"
        ], capture_output=True, text=True)
        
        # Prune images
        res_img = subprocess.run([
            "curl", "--unix-socket", socket_path, "-X", "POST",
            "http://localhost/v1.41/images/prune"
        ], capture_output=True, text=True)
        
        return {
            "status": "SUCCESS",
            "build_prune_status": res_build.returncode,
            "image_prune_status": res_img.returncode,
            "build_prune_output": res_build.stdout,
            "image_prune_output": res_img.stdout
        }
    except Exception as e:
        tasks.logger.error(f"Error in docker_system_cleanup_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}

@celery_app.task
def db_task_log_prune_task() -> Dict[str, Any]:
    """
    Daily database log pruning task. Clears completed TaskLog records older than 30 days.
    """
    db: Session = tasks.SessionLocal()
    try:
        limit_date = datetime.utcnow() - timedelta(days=30)
        deleted = db.query(TaskLog).filter(
            TaskLog.status.in_(["SUCCESS", "FAILED"]),
            TaskLog.created_at < limit_date
        ).delete(synchronize_session=False)
        db.commit()
        tasks.logger.info(f"Database TaskLog prune completed. Deleted {deleted} records older than 30 days.")
        return {"status": "SUCCESS", "deleted_count": deleted}
    except Exception as e:
        tasks.logger.error(f"Error in db_task_log_prune_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
