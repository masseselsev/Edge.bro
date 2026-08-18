"""Serializing pkill/cleanup/init/create on the node itself.

A node can be enrolled with more than one orchestrator (an on-site and an
off-site server, say), and each is a fully separate install with its own DB,
Redis and Celery workers. The only thing two orchestrators backing up the
same node share is SSH/filesystem access to that node — there is no shared
registry either could consult to ask "is someone else already backing this
node up".

The pre-flight cleanup used to answer that with `pkill -f '[b]org create'`
and a blanket cache-lock delete, unconditionally, before every backup. That is
safe against this orchestrator's own dead processes, and it is exactly the
wrong thing to run against a second orchestrator's live backup — it kills the
process outright instead of queuing behind it. Wrapping only `borg create` in
a lock does not fix this: the cleanup step and `borg init` are separate SSH
calls that ran before it, unguarded.

So cleanup, `borg init` and `borg create` now run as one shell script under
one exclusive, non-blocking flock on the node's filesystem, in a single SSH
call: the flock is only actually held for as long as one process keeps its
file descriptor open, and two separate SSH calls cannot share one hold. A
second orchestrator (or a second concurrent trigger from this same one) that
finds the lock held gets a distinct exit code back and retries later via
Celery instead of racing straight through the cleanup step.
"""
import os

#: Guards the node's own filesystem, not any repository — every orchestrator
#: enrolled on a node contends for this same path.
NODE_LOCK_PATH = "/run/edge-bro-node.lock"

#: The exit code the locked script uses to signal "flock could not be
#: acquired" back through borg's/ssh's own exit codes (0/1/2 and friends),
#: which this must never collide with.
LOCK_BUSY_EXIT_CODE = 75

#: Printed to stderr right before LOCK_BUSY_EXIT_CODE, so a caller reading the
#: stream can recognise it too, not just the exit code.
LOCK_BUSY_MARKER = "EDGEBRO_NODE_LOCK_BUSY"

#: Printed to stderr right after `borg init`, carrying its exit code — init
#: runs inside the same script as create now, so its own exit status would
#: otherwise be overwritten by create's before Python ever sees it.
INIT_RC_MARKER = "EDGEBRO_INIT_RC"

#: Delay between retries while waiting for another orchestrator's backup to
#: release the node. Deliberately much shorter than the scheduler's own
#: failure-cooldowns (core/scheduler.py) — those are sized for a backup that
#: actually failed; this is sized for a backup proceeding normally, just on a
#: different orchestrator.
NODE_LOCK_RETRY_COUNTDOWN_SECONDS = int(os.getenv("NODE_LOCK_RETRY_COUNTDOWN_SECONDS", "120"))

#: Bounds the total wait so a permanently stuck node fails outright instead of
#: retrying forever. 60 * 120s ~= 2 hours.
NODE_LOCK_MAX_RETRIES = int(os.getenv("NODE_LOCK_MAX_RETRIES", "60"))


class NodeLockBusy(Exception):
    """Another orchestrator (or another trigger from this one) holds the node lock."""


def build_locked_remote_script(cleanup_cmd: str, init_cmd: str, create_cmd: str) -> str:
    """One shell script: acquire the node lock, then clean up, init, create.

    All three inner commands run only if `flock -n` succeeds, inside the same
    process that holds it — the lock is released the instant this shell
    exits, whether that is because `create_cmd` finished or because the whole
    thing was killed. `create_cmd` must be the last statement, since its exit
    code becomes the script's own (no `set -e`, so `init_cmd`'s status has to
    be captured with its own marker if a caller needs it — see
    `INIT_RC_MARKER`).

    Not wrapped in its own `bash -c "..."`: `create_cmd` already comes wrapped
    in one (see `backup_tasks.build_borg_create_inner_cmd`), and nesting a
    second layer around it is exactly the kind of double-quoting this
    codebase has been bitten by before (see `core.ssh.borg_rsh`'s history).
    The remote's default shell parses this string directly, the same way
    `cleanup_locks_and_resolve_ip`'s pre-flight probe always has.
    """
    return (
        f"exec 9>{NODE_LOCK_PATH}; "
        f"flock -n 9 || {{ echo {LOCK_BUSY_MARKER} >&2; exit {LOCK_BUSY_EXIT_CODE}; }}; "
        f"{cleanup_cmd} "
        f"{init_cmd} "
        f"{create_cmd}"
    )
