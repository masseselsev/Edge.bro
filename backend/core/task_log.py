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
import subprocess
from datetime import datetime
from typing import Callable, List, Optional, Union

from core.db_session import session_scope
from models import TaskLog
from core.clock import utcnow

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
            timestamp = utcnow().strftime("%Y-%m-%d %H:%M:%S")
            task.log_output = (task.log_output or "") + f"[{timestamp}] {message}\n"
            if status:
                task.status = status
            elif task.status not in ("SUCCESS", "FAILED"):
                task.status = "RUNNING"
    except Exception as e:
        logger.error(f"Error logging to task {task_id}: {str(e)}")


def run_command_with_logging(
    task_id: str,
    cmd: Union[str, List[str]],
    shell: bool = False,
    on_log_line: Optional[Callable[[str], None]] = None
) -> None:
    """Run a subprocess, streaming each output line into the task's log.

    Here rather than in `tasks/__init__.py` for the same reason as
    `log_to_task`: `tasks` imports `iso_tasks`, so `iso_tasks` importing
    `tasks` back closes a cycle, and the ISO build is this function's heaviest
    caller. Both halves of "run something slow and show the operator what it is
    doing" now live in one importable place.

    stderr is folded into stdout so the operator reading the log sees the
    ordering the command actually produced, and the read is line-buffered so a
    forty-minute xorriso run reports progress rather than arriving at the end.

    Raises CalledProcessError on a non-zero exit — the caller decides whether
    that fails the task.
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
                    # A broken progress parser must not abort the build it is
                    # only narrating.
                    logger.error(f"Error in on_log_line callback: {str(ex)}")
        process.stdout.close()

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd)
