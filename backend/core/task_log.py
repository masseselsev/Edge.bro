"""Appending to a background task's log.

Lives here rather than in `tasks/__init__.py` because of what importing that
package costs. `tasks` pulls in Celery, the beat schedule, and every task
module — `backup_tasks`, `iso_tasks`, `restore_tasks` — several of which need
to log. Anything importing `log_to_task` at module scope from there closed a
cycle, so nine modules deferred the import inside the function body instead
and one of them left a comment saying why.

A function that appends a line to a row has no business being that expensive
to reach.
"""
import logging
from datetime import datetime
from typing import Optional

from core.db_session import session_scope
from models import TaskLog

logger = logging.getLogger(__name__)


def log_to_task(task_id: str, message: str, status: Optional[str] = None) -> None:
    """Append one timestamped line to a TaskLog, in its own short session.

    Never raises. This is called from inside provisioning, backup and restore
    tasks, often from an exception handler that is already reporting a
    failure — a database problem here must not replace the error the caller
    was trying to record.

    A status of SUCCESS or FAILED is terminal: once set, later lines can still
    be appended but will not silently move the task back to RUNNING.
    """
    try:
        with session_scope() as db:
            task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
            if not task:
                return
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            task.log_output = (task.log_output or "") + f"[{timestamp}] {message}\n"
            if status:
                task.status = status
            elif task.status not in ("SUCCESS", "FAILED"):
                task.status = "RUNNING"
    except Exception as e:
        logger.error(f"Error logging to task {task_id}: {str(e)}")
