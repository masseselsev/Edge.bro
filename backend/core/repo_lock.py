"""Who is legitimately using a borg repository right now.

Borg allows one writer at a time, so the nightly prune and every backup bound
for the same repository contend for one lock. That contention was being
resolved in the worst possible way: `cleanup_locks_and_resolve_ip` ran
`borg break-lock` **unconditionally** before every backup, and if that returned
non-zero it walked the repository removing every file named `lock.*`.

Neither step asks whether the lock it is destroying belongs to something alive.
Taking it away does not queue the newcomer behind the holder — it lets both
write to the same segments and manifest at once, which is the corruption case
borg's lock exists to prevent, not a slowdown.

So the two things that legitimately hold a repository announce themselves here,
and the pre-flight leaves the lock alone while either is registered:

* **Maintenance** — the nightly prune. Exclusive: a second prune of the same
  repository is the corruption case, so `repository_maintenance` yields None
  rather than letting it start.
* **Writers** — backups. Deliberately *not* exclusive. Several backups may be
  bound for one repository at once; borg serialises them on its own lock and
  `--lock-wait` makes the losers queue instead of erroring. Registering them is
  only how the pre-flight learns that the lock it is about to break belongs to
  a live transfer. Enforcing exclusivity here instead would re-serialise what
  sharding exists to parallelise.

A lock with nobody registered against it is still broken, because that is the
case this recovery is actually for: a worker killed mid-transfer really does
leave a lock behind with nobody to release it, and borg cannot always clear it
itself — it only reclaims a stale lock whose owner is a dead PID on the same
host, and a recreated container reports a different hostname.

Redis rather than a file beside the repository: these have to be visible to
workers in other containers, and they have to expire on their own if the
process holding them is killed — a flag that outlives its owner would stop
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


WRITERS_KEY = "borg_repo_writers"


def writers_key(repo_path: str) -> str:
    return f"{WRITERS_KEY}:{repo_path}"


#: Fallback lifetime for a writer registration. Callers pass the per-node value
#: `core.schedule_estimate.backup_lock_ttl_seconds` derives from that node's own
#: history; this only covers a caller that has none to offer.
DEFAULT_WRITER_TTL_SECONDS = int(os.getenv("BORG_WRITER_TTL", "14400"))


def writer_in_progress(repo_path: str) -> bool:
    """Whether a live backup is registered against this repository.

    Registrations are a sorted set scored by their expiry, so a worker killed
    mid-transfer ages out of it without anyone having to notice.

    Unlike `maintenance_owner`, a Redis failure answers **True**. The two
    answers are not symmetric: guessing "nobody" wrongly breaks a live lock and
    corrupts the repository, while guessing "somebody" wrongly leaves a stale
    lock in place and the backup waits out `--lock-wait` and retries. And a
    Redis that cannot be read is one the workers could not have received this
    task through in the first place, so this is close to unreachable.
    """
    key = writers_key(repo_path)
    try:
        redis_client.zremrangebyscore(key, "-inf", time.time())
        return redis_client.zcard(key) > 0
    except Exception as e:
        logger.warning(
            f"Could not read the repository writer registry: {e}. "
            "Assuming a backup is in progress and leaving the repo lock alone."
        )
        return True


@contextmanager
def repository_writer(
    owner: str, repo_path: str, ttl: int = DEFAULT_WRITER_TTL_SECONDS
):
    """Announce that this task is about to write to `repo_path`.

    Always enters — this records a writer, it does not admit one. Yields a
    `heartbeat()` to call as the transfer makes progress, so the registration
    tracks a backup that runs longer than its estimate rather than expiring
    underneath it and exposing its lock to the next pre-flight.
    """
    key = writers_key(repo_path)
    token = f"{owner}:{uuid.uuid4().hex}"

    def heartbeat() -> None:
        try:
            redis_client.zadd(key, {token: time.time() + ttl})
            # So an abandoned registry cannot outlive every writer in it.
            redis_client.expire(key, ttl * 2)
        except Exception as e:
            logger.warning(f"Could not refresh the repository writer registration: {e}")

    heartbeat()
    try:
        yield heartbeat
    finally:
        try:
            redis_client.zrem(key, token)
        except Exception as e:
            logger.warning(f"Could not clear the repository writer registration: {e}")


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
