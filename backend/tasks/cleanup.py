import os
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from models import TaskLog
from celery_app import celery_app
import tasks
from core.clock import utcnow

@celery_app.task(name="tasks.docker_system_cleanup_task")
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

@celery_app.task(name="tasks.db_task_log_prune_task")
def db_task_log_prune_task() -> Dict[str, Any]:
    """
    Daily database log pruning task. Clears completed TaskLog records older than 30 days.
    """
    db: Session = tasks.SessionLocal()
    try:
        limit_date = utcnow() - timedelta(days=30)
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


#: How many uncategorised failures one pass will classify. Bounded so this
#: never turns into an unbounded scan of every failed log in the fleet.
_BACKFILL_LIMIT = 500


@celery_app.task(name="tasks.backfill_error_categories_task")
def backfill_error_categories_task() -> Dict[str, Any]:
    """Work out the failure category of older backup rows.

    Backups written since the feature landed classify themselves as they
    finish; anything older has a NULL category. This used to run inside
    GET /api/stats/insights, which meant a read endpoint wrote up to 500 rows
    — reading each one's log_output — and concurrent dashboard loads raced
    over overlapping sets of them. It belongs on a schedule.
    """
    from core import backup_stats
    from models import BackupHistory

    db: Session = tasks.SessionLocal()
    try:
        stale = (
            db.query(BackupHistory)
            .filter(
                BackupHistory.status != "SUCCESS",
                BackupHistory.error_category.is_(None),
            )
            .order_by(BackupHistory.timestamp.desc())
            .limit(_BACKFILL_LIMIT)
            .all()
        )
        if not stale:
            return {"status": "SUCCESS", "classified": 0}

        for row in stale:
            row.error_category = backup_stats.classify_failure(row.log_output)
        db.commit()
        tasks.logger.info(f"Classified {len(stale)} previously uncategorised failures.")
        return {"status": "SUCCESS", "classified": len(stale)}
    except Exception as e:
        db.rollback()
        tasks.logger.error(f"Error in backfill_error_categories_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()


#: How long application logs are kept. These are diagnostic breadcrumbs, not
#: an audit trail — the audit trail is audit_logs, which is kept far longer.
_SYSTEM_LOG_RETENTION_DAYS = int(os.getenv("SYSTEM_LOG_RETENTION_DAYS", "14"))

#: Audit records answer "who did what" and are worth keeping, but not
#: forever. A year covers any realistic review window.
_AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "365"))


@celery_app.task(name="tasks.prune_log_tables_task")
def prune_log_tables_task() -> Dict[str, Any]:
    """Trim system_logs and audit_logs.

    Neither table had any retention at all. task_logs was pruned daily and the
    telemetry tables had their own sweep, but these two — the ones every log
    record and every user action land in — grew without limit. On a fleet
    generating a few hundred thousand rows a day they are what fills the disk
    first.
    """
    from models import AuditLog, SystemLog

    db: Session = tasks.SessionLocal()
    deleted = {}
    try:
        for model, days, label in (
            (SystemLog, _SYSTEM_LOG_RETENTION_DAYS, "system_logs"),
            (AuditLog, _AUDIT_LOG_RETENTION_DAYS, "audit_logs"),
        ):
            cutoff = utcnow() - timedelta(days=days)
            deleted[label] = (
                db.query(model)
                .filter(model.created_at < cutoff)
                .delete(synchronize_session=False)
            )
        db.commit()
        tasks.logger.info(
            "Log prune removed %s system_logs and %s audit_logs rows.",
            deleted.get("system_logs", 0), deleted.get("audit_logs", 0),
        )
        return {"status": "SUCCESS", **deleted}
    except Exception as e:
        db.rollback()
        tasks.logger.error(f"Error in prune_log_tables_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
