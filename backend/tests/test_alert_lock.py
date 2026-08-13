"""Mutual exclusion for the hourly alert sweep.

Two bugs lived in the original lock and both only appear under the condition
that matters — a sweep running longer than its own TTL:

  1. `exists()` then `setex()` is not atomic, so two beats firing together
     could both see no lock and both proceed.
  2. `finally: delete(LOCK_KEY)` released whatever lock happened to be there.
     Once sweep A overran its TTL and sweep B took the lock, A's cleanup
     deleted *B's* lock, letting C start alongside B — one overrun turning
     into an unbounded chain of concurrent sweeps.
"""
from unittest.mock import MagicMock, patch

import pytest

import tasks
from tasks.alerts import LOCK_KEY, LOCK_TTL_SECONDS, evaluate_alerts_task


class FakeRedis:
    """Enough of Redis to exercise SET NX EX and the compare-and-delete script."""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    def set(self, key, value, nx=False, ex=None):
        self.set_calls.append({"key": key, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def eval(self, script, numkeys, key, arg):
        # Mirrors the Lua compare-and-delete.
        if self.store.get(key) == arg:
            del self.store[key]
            return 1
        return 0


@pytest.fixture
def fake_redis():
    fake = FakeRedis()
    with patch.object(tasks, "redis_client", fake):
        yield fake


@pytest.fixture
def quiet_db():
    """Make the sweep body a no-op so these tests only exercise locking."""
    with patch.object(tasks, "SessionLocal", MagicMock()), \
         patch("tasks.alerts.SOURCES", {}), \
         patch("tasks.alerts.sync_alerts") as sync, \
         patch("tasks.alerts.dispatch") as dispatch:
        sync.return_value = MagicMock(opened=[], reopened=[], resolved=[])
        dispatch.notify.return_value = None
        yield


def test_lock_is_acquired_atomically_with_a_ttl(fake_redis, quiet_db):
    res = evaluate_alerts_task()
    assert res["status"] == "SUCCESS"

    assert len(fake_redis.set_calls) == 1
    call = fake_redis.set_calls[0]
    assert call["key"] == LOCK_KEY
    assert call["nx"] is True, "lock must be taken with NX or two sweeps can race"
    assert call["ex"] == LOCK_TTL_SECONDS, "lock must carry a TTL or a dead worker wedges it"


def test_ttl_exceeds_a_realistic_sweep(fake_redis, quiet_db):
    """A 2000-node sweep took minutes; 300s was below that and caused overlap."""
    assert LOCK_TTL_SECONDS >= 1800, (
        f"TTL is {LOCK_TTL_SECONDS}s — must exceed the worst-case sweep, not the "
        "typical one, or an overrun starts a second concurrent sweep"
    )


def test_a_second_sweep_is_skipped_while_one_holds_the_lock(fake_redis, quiet_db):
    fake_redis.store[LOCK_KEY] = "someone-elses-token"
    res = evaluate_alerts_task()
    assert res["status"] == "SKIPPED"
    assert fake_redis.store[LOCK_KEY] == "someone-elses-token", (
        "the skipped run must not disturb the holder's lock"
    )


def test_lock_is_released_on_success(fake_redis, quiet_db):
    evaluate_alerts_task()
    assert LOCK_KEY not in fake_redis.store


def test_lock_is_released_when_the_sweep_raises(fake_redis):
    with patch.object(tasks, "SessionLocal", MagicMock()), \
         patch("tasks.alerts.SOURCES", {}), \
         patch("tasks.alerts.sync_alerts", side_effect=RuntimeError("boom")):
        res = evaluate_alerts_task()
    assert res["status"] == "FAILED"
    assert LOCK_KEY not in fake_redis.store, "a failed sweep must not wedge the lock"


def test_an_overrunning_sweep_does_not_delete_a_successors_lock(fake_redis, quiet_db):
    """The chain-reaction bug.

    Simulate: our sweep's lock expires mid-run and a later sweep takes a fresh
    one. When ours finishes, its cleanup must leave the newer lock alone.
    """
    successor_token = "successor-token"

    real_sync = None

    def expire_and_replace(*a, **kw):
        # While we are "working", our lock expires and someone else takes it.
        fake_redis.store[LOCK_KEY] = successor_token
        return MagicMock(opened=[], reopened=[], resolved=[])

    with patch("tasks.alerts.sync_alerts", side_effect=expire_and_replace):
        evaluate_alerts_task()

    assert fake_redis.store.get(LOCK_KEY) == successor_token, (
        "the overrunning sweep deleted the successor's lock; that is what turns "
        "a single overrun into unbounded concurrent sweeps"
    )


def test_redis_outage_does_not_run_the_sweep_unguarded(quiet_db):
    broken = MagicMock()
    broken.set.side_effect = ConnectionError("redis down")
    with patch.object(tasks, "redis_client", broken):
        res = evaluate_alerts_task()
    assert res["status"] == "FAILED"
    assert res["reason"] == "lock unavailable", (
        "with no lock available the sweep must decline to run, not run unguarded"
    )
