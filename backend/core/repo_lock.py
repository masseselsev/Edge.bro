"""A flag saying the shared borg repository is being maintained.

Borg allows one writer at a time, and the whole fleet shares one repository, so
the nightly prune and every backup contend for the same lock. That contention
was being resolved in the worst possible way: `cleanup_locks_and_resolve_ip`
ran `borg break-lock` **unconditionally** before every backup, and if that
returned non-zero it walked the repository removing every file named `lock.*`.

Neither step asks whether the lock it is destroying belongs to something alive.
A backup starting while the prune held the lock would take it away and write
concurrently — two processes mutating one repository's segments and manifest,
which is the corruption case borg's lock exists to prevent, not a slowdown.

So maintenance announces itself here first. A backup that sees the flag stands
down instead of breaking in; a backup that does not see it keeps the existing
recovery behaviour, which is still needed because a worker killed mid-transfer
really does leave a lock behind with nobody to release it.

Redis rather than a file beside the repository: the flag has to be visible to
workers in other containers, and it has to expire on its own if the process
holding it is killed — a maintenance flag that outlives its owner would stop
every backup in the fleet indefinitely, trading one failure mode for a worse
one.
"""
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Optional

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)

MAINTENANCE_KEY = "borg_repo_maintenance"


def maintenance_key(repo_path: Optional[str] = None) -> str:
    """The flag for one repository.

    Repositories are independent — each has its own borg lock — so pruning one
    must not stand down backups bound for another. Without a path this is the
    original fleet-wide key, which is what shard 0 keeps using.
    """
    if repo_path is None:
        return MAINTENANCE_KEY
    return f"{MAINTENANCE_KEY}:{repo_path}"

#: Long enough for a prune and compact of a fleet-sized repository, short
#: enough that a worker killed mid-prune does not block backups all day.
#: Refreshed by `heartbeat` while the work is actually running, so the TTL
#: bounds the silence after a crash rather than the length of the job.
DEFAULT_TTL_SECONDS = int(os.getenv("BORG_MAINTENANCE_TTL", "900"))


def maintenance_owner(repo_path: Optional[str] = None) -> Optional[str]:
    """Who holds the repository, or None. Never raises.

    A Redis that cannot be reached returns None — the caller then behaves as it
    did before this existed. Failing the other way would stop every backup in
    the fleet the moment Redis blinked.
    """
    try:
        value = redis_client.get(maintenance_key(repo_path))
    except Exception as e:
        logger.warning(f"Could not read the repository maintenance flag: {e}")
        return None
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def maintenance_in_progress(repo_path: Optional[str] = None) -> bool:
    return maintenance_owner(repo_path) is not None


@contextmanager
def repository_maintenance(
    owner: str, ttl: int = DEFAULT_TTL_SECONDS, repo_path: Optional[str] = None
):
    """Claim the repository for exclusive maintenance.

    Yields a `heartbeat()` callable — call it between long steps so the TTL
    tracks progress rather than being one bet made at the start.

    Yields None instead of raising when the claim cannot be taken, so the
    caller decides whether to proceed. Losing the race means another prune is
    already running, which is a reason to skip, not to crash.
    """
    key = maintenance_key(repo_path)
    token = f"{owner}:{uuid.uuid4().hex}"
    try:
        acquired = redis_client.set(key, token, nx=True, ex=ttl)
    except Exception as e:
        logger.warning(f"Could not claim the repository maintenance flag: {e}")
        acquired = None

    if not acquired:
        yield None
        return

    def heartbeat() -> None:
        try:
            # Only extends our own claim. If the TTL lapsed and someone else
            # took it, silently refreshing theirs would give two owners.
            if maintenance_owner(repo_path) == token:
                redis_client.expire(key, ttl)
        except Exception as e:
            logger.warning(f"Could not refresh the repository maintenance flag: {e}")

    try:
        yield heartbeat
    finally:
        _release(token, key)


def _release(token: str, key: str = MAINTENANCE_KEY) -> None:
    """Delete the flag, but only if it is still ours.

    Compare-and-delete in Lua, for the same reason the alert sweep does it: a
    plain DELETE after our TTL had already lapsed would remove whoever claimed
    it next, and two prunes running against one repository is precisely what
    this module exists to prevent.
    """
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
        redis_client.eval(script, 1, key, token)
    except Exception as e:
        logger.warning(f"Could not release the repository maintenance flag: {e}")
