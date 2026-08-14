"""A backup must not take the repository lock away from whoever is using it.

`cleanup_locks_and_resolve_ip` ran `borg break-lock` before every backup,
unconditionally, and force-removed every `lock.*` file if that failed. Neither
step asked whether something alive was holding the lock. With the nightly prune
overrunning into the backup windows — which at 2000 nodes it reliably did — the
first backup of the night would take the lock away from it and both would write
to the same segments and manifest.

That is repository corruption, and the repository is the only copy of every
backup in the fleet.

The other holder is another *backup*, and it is the common one: every node in a
shard shares that shard's repository, so at 2000 nodes across five shards this
is routine rather than a nightly overlap. It also made `--lock-wait` inert — a
backup that breaks the lock never reaches the wait that was supposed to queue
it. Verified against real borg 1.4: with the lock held, `borg create
--lock-wait 10` waits the full ten seconds and then reports a timeout; run
`borg break-lock` first and the same create starts immediately, while the
holder later dies with `NotLocked`.
"""
from unittest.mock import MagicMock, patch

import pytest

import backup_tasks
from core import repo_lock


@pytest.fixture
def fake_redis(monkeypatch):
    """A tiny in-memory stand-in, so these run without a Redis."""
    class FakeRedis:
        def __init__(self):
            self.store = {}

        def get(self, key):
            value = self.store.get(key)
            return value.encode() if isinstance(value, str) else value

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        def expire(self, key, ttl):
            return key in self.store

        def delete(self, key):
            return self.store.pop(key, None) is not None

        def eval(self, script, numkeys, key, arg):
            # The compare-and-delete used by _release.
            current = self.store.get(key)
            if current == arg:
                del self.store[key]
                return 1
            return 0

        # The writer registry is a sorted set scored by expiry time.
        def zadd(self, key, mapping):
            self.store.setdefault(key, {}).update(mapping)

        def zrem(self, key, member):
            self.store.get(key, {}).pop(member, None)

        def zremrangebyscore(self, key, low, high):
            members = self.store.get(key)
            if not members:
                return 0
            expired = [m for m, score in members.items() if score <= high]
            for m in expired:
                del members[m]
            return len(expired)

        def zcard(self, key):
            return len(self.store.get(key, {}))

    fake = FakeRedis()
    monkeypatch.setattr(repo_lock, "redis_client", fake)
    return fake


def test_the_flag_is_held_for_the_duration_and_released_after(fake_redis):
    assert not repo_lock.maintenance_in_progress()
    with repo_lock.repository_maintenance("prune") as heartbeat:
        assert heartbeat is not None
        assert repo_lock.maintenance_in_progress()
    assert not repo_lock.maintenance_in_progress()


def test_a_second_claim_is_refused_while_the_first_holds_it(fake_redis):
    with repo_lock.repository_maintenance("prune-a") as first:
        assert first is not None
        with repo_lock.repository_maintenance("prune-b") as second:
            assert second is None, "two prunes must never hold the repository at once"


def test_the_flag_is_released_even_when_the_body_raises(fake_redis):
    with pytest.raises(RuntimeError):
        with repo_lock.repository_maintenance("prune"):
            raise RuntimeError("compact blew up")
    assert not repo_lock.maintenance_in_progress()


def test_releasing_only_removes_our_own_claim(fake_redis):
    """A TTL that lapsed mid-run must not let us delete our successor's flag.

    Same fencing-token reasoning as the alert sweep: a plain DELETE here would
    hand the repository to two prunes at once, which is the thing being
    prevented.
    """
    with repo_lock.repository_maintenance("prune-a"):
        # Simulate the TTL lapsing and someone else claiming it.
        fake_redis.store[repo_lock.MAINTENANCE_KEY] = "someone-else:token"
    assert fake_redis.store[repo_lock.MAINTENANCE_KEY] == "someone-else:token"


def test_an_unreachable_redis_reads_as_no_maintenance(monkeypatch):
    """Failing the other way would stop every backup in the fleet on a blip."""
    class Broken:
        def get(self, key):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(repo_lock, "redis_client", Broken())
    assert repo_lock.maintenance_in_progress() is False


# --- the gate in the backup path ---

def _run_cleanup(monkeypatch, subprocess_run, writer=False):
    monkeypatch.setattr(backup_tasks.subprocess, "run", subprocess_run)
    monkeypatch.setattr(backup_tasks, "log_to_task", lambda *a, **k: None)
    # Stated explicitly in every case: the two holders are independent gates,
    # and a test that left one to the ambient Redis would pass or fail on
    # whatever the last test happened to register.
    monkeypatch.setattr(backup_tasks, "writer_in_progress", lambda repo_path: writer)
    return backup_tasks.cleanup_locks_and_resolve_ip(
        task_id="t1",
        node_ip="10.0.0.9",
        node_ssh_port=22,
        repo_path="/data/borg/fleet",
        borg_passphrase="secret",
        configured_ip="203.0.113.5",
        borg_ssh_port=12345,
    )


def test_a_backup_does_not_break_the_lock_while_maintenance_runs(monkeypatch):
    calls = []

    def spy(cmd, *args, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")

    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda repo_path=None: "global_daily_prune:abc")
    _run_cleanup(monkeypatch, spy)

    assert not any(c[:2] == ["borg", "break-lock"] for c in calls), (
        "the backup broke a lock that the running prune was holding"
    )


def test_the_force_remove_of_lock_files_is_also_skipped(monkeypatch):
    """The fallback is the more destructive of the two paths.

    It walks the repository deleting every file named `lock.*`, so reaching it
    while a prune holds the lock removes the lock of a live writer regardless
    of what borg itself would have refused to do.
    """
    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda repo_path=None: "prune:abc")
    removed = []
    monkeypatch.setattr(
        backup_tasks, "force_cleanup_stale_repo_locks",
        lambda task_id, repo_path: removed.append(repo_path),
    )
    _run_cleanup(
        monkeypatch,
        lambda cmd, *a, **k: MagicMock(returncode=0, stdout="10.0.0.9 5000 x 1\nREACHABLE:yes\nOK\n", stderr=""),
    )
    assert removed == []


def test_a_backup_still_clears_a_genuinely_stale_lock_when_nothing_is_running(monkeypatch):
    """The recovery behaviour has to survive: a worker killed mid-transfer
    really does leave a lock with nobody to release it."""
    calls = []

    def spy(cmd, *args, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")

    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda repo_path=None: None)
    _run_cleanup(monkeypatch, spy)

    assert any(c[:2] == ["borg", "break-lock"] for c in calls)


# --- the other holder: a concurrent backup on the same repository -----------


def test_a_backup_does_not_break_the_lock_of_another_backup(monkeypatch):
    """The case sharding makes routine. Two nodes in one shard write to one
    repository, and the second one's pre-flight used to take the first one's
    lock away mid-transfer."""
    calls = []

    def spy(cmd, *args, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")

    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda repo_path=None: None)
    _run_cleanup(monkeypatch, spy, writer=True)

    assert not any(c[:2] == ["borg", "break-lock"] for c in calls), (
        "the backup broke a lock that another live backup was holding"
    )


def test_the_force_remove_is_skipped_for_a_live_backup_too(monkeypatch):
    """break-lock is the polite path; this is the one that deletes lock files
    off the filesystem regardless of what borg would have refused to do."""
    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda repo_path=None: None)
    removed = []
    monkeypatch.setattr(
        backup_tasks, "force_cleanup_stale_repo_locks",
        lambda task_id, repo_path: removed.append(repo_path),
    )
    _run_cleanup(
        monkeypatch,
        lambda cmd, *a, **k: MagicMock(returncode=1, stdout="10.0.0.9 5000 x 1\nREACHABLE:yes\nOK\n", stderr="failed"),
        writer=True,
    )
    assert removed == []


# --- the writer registry itself ---------------------------------------------


def test_a_repository_with_no_registered_writer_reads_as_free(fake_redis):
    assert repo_lock.writer_in_progress("/data/borg/fleet") is False


def test_a_registered_writer_is_visible_while_it_runs(fake_redis):
    with repo_lock.repository_writer("backup:7", "/data/borg/shard-1"):
        assert repo_lock.writer_in_progress("/data/borg/shard-1") is True
    assert repo_lock.writer_in_progress("/data/borg/shard-1") is False


def test_a_writer_on_one_shard_does_not_shield_another(fake_redis):
    """The point of sharding: a backup on shard 1 must not stop shard 2's
    pre-flight from clearing a genuinely stale lock."""
    with repo_lock.repository_writer("backup:7", "/data/borg/shard-1"):
        assert repo_lock.writer_in_progress("/data/borg/shard-2") is False


def test_several_backups_may_write_to_one_repository(fake_redis):
    """Registration records writers, it does not admit them — borg's own lock
    serialises them and --lock-wait makes the losers queue. Making this
    exclusive would re-serialise what sharding exists to parallelise."""
    with repo_lock.repository_writer("backup:1", "/data/borg/fleet"):
        with repo_lock.repository_writer("backup:2", "/data/borg/fleet") as second:
            assert second is not None
            assert repo_lock.writer_in_progress("/data/borg/fleet") is True
        # The first is still running; the second leaving must not clear it.
        assert repo_lock.writer_in_progress("/data/borg/fleet") is True
    assert repo_lock.writer_in_progress("/data/borg/fleet") is False


def test_a_writer_that_died_ages_out(fake_redis):
    """A worker killed mid-transfer never runs its cleanup. The registration
    has to expire on its own, or that repository's lock could never be
    recovered again."""
    repo_lock.repository_writer("backup:9", "/data/borg/fleet", ttl=-1).__enter__()
    assert repo_lock.writer_in_progress("/data/borg/fleet") is False


def test_an_unreadable_registry_is_treated_as_busy(monkeypatch):
    """Opposite of the maintenance flag, deliberately. Guessing "nobody"
    wrongly breaks a live lock and corrupts the repository; guessing
    "somebody" wrongly costs one delayed backup."""
    class Broken:
        def zremrangebyscore(self, *a, **k):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(repo_lock, "redis_client", Broken())
    assert repo_lock.writer_in_progress("/data/borg/fleet") is True
