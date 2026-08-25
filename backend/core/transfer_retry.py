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


class BackupConnectionLost(Exception):
    """The link to the node dropped mid-transfer.

    Carries what recording the failure will need, because nothing is written
    until the retries are spent and the values are gone by then: the function
    that observed the drop has returned.
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
