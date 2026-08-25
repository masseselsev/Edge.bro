"""Retrying a backup whose link died, rather than failing it.

A node reached over a flaky tunnel loses its connection partway through `borg
create`. The node is fine, the repository is fine, the data is fine — the link
went away. borg has been writing checkpoints throughout (see
`backup_tasks.MAX_CHECKPOINT_INTERVAL_SECONDS`), so the chunks already
transferred stay in the repository and the next `borg create` skips them: a
retry resumes rather than starting over, without any explicit resume logic.

Recording the failure and stopping throws that away. For a scheduled node the
next attempt waits out the scheduler's retry cooldown; for a manually
triggered backup there is no next attempt at all. So a connection loss becomes
a Celery retry, and only a run that exhausts its retries is recorded as
failed.

Sibling of `core.node_lock`, which does the same thing for a different reason:
there the node is busy and nothing has been transferred, here the transfer was
under way and is worth resuming.
"""
from __future__ import annotations

import os
from typing import Optional


def _int_env(name: str, default: int) -> int:
    """Read per call rather than at import, so a redeploy can change it
    without a code change — the convention core.ssh's keepalive helpers set."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


#: How long to wait before trying the transfer again. Short: the point is to
#: get back on the link once it returns, not to back off from a busy node.
CONNECTION_LOST_RETRY_COUNTDOWN_SECONDS = _int_env("BACKUP_RECONNECT_COUNTDOWN_SECONDS", 60)

#: How many times. Deliberately small — each attempt can run for the length of
#: a full backup, and a link that drops four times in a row is a fault to
#: report rather than to keep absorbing silently.
CONNECTION_LOST_MAX_RETRIES = _int_env("BACKUP_RECONNECT_MAX_RETRIES", 3)


#: How long a `borg create` waits for the repository lock before giving the
#: worker back. Deliberately short, and much shorter than BORG_LOCK_WAIT_SECONDS
#: which still governs prune and delete: a backup that cannot have the lock now
#: is retried on the schedule below, and parking a Celery worker for ten minutes
#: to find that out occupies a slot that could be running a backup on another
#: shard. Long enough to absorb a prune finishing without a whole retry cycle.
CREATE_LOCK_WAIT_SECONDS = _int_env("BORG_CREATE_LOCK_WAIT_SECONDS", 60)

#: How long to wait before asking a busy repository again. The holder is
#: running a whole backup, not recovering from a blip, so checking back sooner
#: only burns dispatches.
REPO_BUSY_RETRY_COUNTDOWN_SECONDS = _int_env("BACKUP_REPO_BUSY_COUNTDOWN_SECONDS", 600)

#: A day of asking at the default countdown. Not unlimited, though queueing
#: behind other backups is ordinary and the operator can stop a run by hand:
#: a task that retries forever survives every mistake that created it, and a
#: repository still busy after a day is a fault to report rather than to keep
#: absorbing in silence.
REPO_BUSY_MAX_RETRIES = _int_env("BACKUP_REPO_BUSY_MAX_RETRIES", 144)


class BackupTransferInterrupted(Exception):
    """Something outside the backup stopped it, and it is worth another go.

    Carries what recording the failure will need, because nothing is written
    until the retries are spent and the values are gone by then: the function
    that observed the interruption has returned.
    """

    def __init__(
        self,
        message: str,
        *,
        archive_name: str,
        log_output: str,
        duration_seconds: Optional[float],
    ):
        super().__init__(message)
        self.archive_name = archive_name
        self.log_output = log_output
        self.duration_seconds = duration_seconds


class RepositoryBusy(BackupTransferInterrupted):
    """Another backup holds the repository's lock.

    Borg allows one writer per repository and holds the lock for the whole of
    `borg create`, so backups bound for one shard serialise. The loser used to
    sit out `--lock-wait` and then be recorded as a failed backup, which on a
    fleet sharing a single repository turned an ordinary queue into a run of
    REPO_LOCKED failures. Waiting is the correct outcome; failing is not.
    """


class BackupConnectionLost(BackupTransferInterrupted):
    """The link to the node dropped mid-transfer.

    borg has been writing checkpoints throughout, so the next attempt resumes
    from the last one rather than starting over.
    """


#: How long a stop request stands before it is forgotten. Long enough to reach
#: a run waiting out a repository queue, short enough that a flag nobody
#: consumed — the task died before seeing it — cannot sit there indefinitely
#: waiting to abort some unrelated future backup.
CANCEL_TTL_SECONDS = _int_env("BACKUP_CANCEL_TTL_SECONDS", 86400)


def cancel_key(node_id: int) -> str:
    return f"backup_cancel:{node_id}"


def request_cancel(redis_client, node_id: int, task_id: str) -> None:
    """Ask the backup identified by `task_id` to stop at its next opportunity."""
    redis_client.setex(cancel_key(node_id), CANCEL_TTL_SECONDS, task_id)


def cancel_requested(redis_client, node_id: int, task_id: str) -> bool:
    """Whether *this* run has been asked to stop.

    Matched against the task id rather than treated as a bare flag: a run that
    has already ended can leave its flag behind, and the next backup of the
    same node would otherwise abort on someone else's stop request.

    An unreadable flag means "carry on". Guessing "cancelled" wrongly kills a
    healthy backup, which is the worse of the two mistakes.
    """
    try:
        raw = redis_client.get(cancel_key(node_id))
    except Exception:
        return False
    if not raw:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return raw == task_id


def clear_cancel(redis_client, node_id: int) -> None:
    try:
        redis_client.delete(cancel_key(node_id))
    except Exception:
        pass


class BackupCancelled(Exception):
    """An operator stopped this run. Not a failure of the backup."""
