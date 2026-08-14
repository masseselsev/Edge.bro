"""A backup must not take the repository lock away from a live prune.

`cleanup_locks_and_resolve_ip` ran `borg break-lock` before every backup,
unconditionally, and force-removed every `lock.*` file if that failed. Neither
step asked whether something alive was holding the lock. With the nightly prune
overrunning into the backup windows — which at 2000 nodes it reliably did — the
first backup of the night would take the lock away from it and both would write
to the same segments and manifest.

That is repository corruption, and the repository is the only copy of every
backup in the fleet.
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

def _run_cleanup(monkeypatch, subprocess_run):
    monkeypatch.setattr(backup_tasks.subprocess, "run", subprocess_run)
    monkeypatch.setattr(backup_tasks, "log_to_task", lambda *a, **k: None)
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
