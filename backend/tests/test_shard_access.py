"""Raising the shard count re-grants access; lowering it must not.

The forced command in every authorized_keys entry names the repositories a key
may reach and is derived from BORG_SHARD_COUNT, so raising the count is only
half a change until the grants are rewritten. Doing that on startup removes a
manual step people forgot.

It is safe in one direction only. The rewrite makes every grant say exactly
what the current count says, so running it while the count is too low narrows
the grants and removes access that currently works — a mistyped variable
becoming an outage, caused by the mechanism meant to prevent one. These pin the
ordering that stops it.
"""
import pytest

from core import repo_paths, shard_access


@pytest.fixture
def counts(monkeypatch):
    """Set the configured and effective counts independently, the way the
    on-disk floor does at import time."""
    def _set(configured, effective=None):
        monkeypatch.setattr(repo_paths, "CONFIGURED_SHARD_COUNT", configured)
        monkeypatch.setattr(repo_paths, "SHARD_COUNT", effective or configured)
    return _set


class _Spy:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def test_grants_are_rewritten_when_nothing_is_stranded(counts):
    counts(5)
    spy = _Spy()
    outcome = shard_access.reconcile([0, 1, 2], reauthorize=spy)

    assert spy.calls == 1
    assert outcome.rewrote


def test_a_stranded_node_blocks_the_rewrite(counts):
    """The case the ordering exists for. Those nodes reach their repository
    only because the grant still names it; rewriting would take that away and
    turn a recoverable misconfiguration into failed backups."""
    counts(2)
    spy = _Spy()
    outcome = shard_access.reconcile([0, 1, 4], reauthorize=spy)

    assert spy.calls == 0, "narrowed the grants of nodes that still depend on them"
    assert outcome.stranded == [4]
    assert not outcome.rewrote


def test_the_floor_is_reported_when_it_overrides_the_setting(counts):
    counts(configured=1, effective=3)
    outcome = shard_access.reconcile([0, 1, 2], reauthorize=_Spy())

    assert outcome.floored


def test_a_floored_count_still_re_grants(counts):
    """Being overridden is not an error — the fleet is simply running at the
    count its repositories require, and the grants should match that."""
    counts(configured=1, effective=3)
    spy = _Spy()
    shard_access.reconcile([0, 1, 2], reauthorize=spy)

    assert spy.calls == 1


def test_an_honest_setting_is_not_reported_as_overridden(counts):
    counts(3)
    outcome = shard_access.reconcile([0, 1], reauthorize=_Spy())

    assert not outcome.floored


def test_a_fleet_with_no_nodes_is_still_re_granted(counts):
    """A fresh install raising the count before enrolling anything is the
    recommended order, and it is the case where the grant that matters is the
    orchestrator's own kiosk one."""
    counts(5)
    spy = _Spy()
    shard_access.reconcile([], reauthorize=spy)

    assert spy.calls == 1


def test_nodes_with_no_shard_recorded_do_not_block_anything(counts):
    """Null means pre-sharding, which is shard 0."""
    counts(2)
    spy = _Spy()
    outcome = shard_access.reconcile([None, 0, 1], reauthorize=spy)

    assert outcome.stranded == []
    assert spy.calls == 1
