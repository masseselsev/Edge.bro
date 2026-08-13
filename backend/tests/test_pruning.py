"""The nightly fleet prune.

It used to run one `borg prune --prefix <host>` per node. Each took the
repository's exclusive lock and re-read a manifest containing every archive of
every node, so at 2000 nodes it was hours of serialised work that started at
03:00 and was still going when backup windows opened — during which no backup
could run at all, because they need the same lock.

It is now three borg invocations: list, batched deletes, compact. The retention
decision moved into core/retention.py, so the tests that used to assert on
`borg prune` flags now assert on the decisions those flags encoded, which is
what actually mattered about them.
"""
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from backup_tasks import _retention_by_hostname, global_daily_prune, plan_deletions
from core.retention import Archive
from database import Base

TEST_DATABASE_URL = "sqlite:///./test_pruning_db.db"
NOW = datetime(2026, 6, 15, 3, 0, 0)


@pytest.fixture(scope="function")
def session_factory():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    yield TestingSessionLocal
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("./test_pruning_db.db"):
        try:
            os.remove("./test_pruning_db.db")
        except Exception:
            pass


def archives_for(host, count, start=NOW, step=timedelta(days=1)):
    return [
        Archive(f"{host}-{(start - step * i).strftime('%Y%m%d%H%M%S')}", start - step * i)
        for i in range(count)
    ]


# --- policy resolution: the four cases the old flag assertions covered ---

@patch('database.SessionLocal')
def test_legacy_flat_columns_are_used_when_no_policy_is_set(mock_session, session_factory):
    """Deployments that never opened the retention UI still run on these."""
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Settings(keep_daily=5, keep_weekly=3, keep_monthly=2, retention_policy=None))
    db.add(models.Node(hostname="node-legacy", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    rules = _retention_by_hostname()["node-legacy"]
    assert (rules.daily, rules.weekly, rules.monthly) == (5, 3, 2)
    assert rules.secondly is None


@patch('database.SessionLocal')
def test_a_global_count_policy_becomes_keep_last(mock_session, session_factory):
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 10}))
    db.add(models.Node(hostname="node-count", ip_address="192.168.1.11"))
    db.commit()
    db.close()

    rules = _retention_by_hostname()["node-count"]
    assert rules.secondly == 10
    assert rules.daily is None


@patch('database.SessionLocal')
def test_a_global_timeframe_policy_becomes_a_window_plus_one(mock_session, session_factory):
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Settings(
        retention_policy={"type": "timeframe", "within_value": 6, "within_unit": "w"}
    ))
    db.add(models.Node(hostname="node-frame", ip_address="192.168.1.12"))
    db.commit()
    db.close()

    rules = _retention_by_hostname()["node-frame"]
    assert rules.within_hours == 6 * 7 * 24
    # keep-last 1 so a node that stopped backing up keeps its final archive.
    assert rules.secondly == 1


@patch('database.SessionLocal')
def test_a_group_override_beats_the_global_policy(mock_session, session_factory):
    """And only when override_retention is actually set."""
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 10}))
    overriding = models.BackupGroup(
        name="Override", interval="weekly", override_retention=True,
        retention_policy={"type": "count", "keep_last": 2},
    )
    inheriting = models.BackupGroup(
        name="Inherit", interval="weekly", override_retention=False,
        retention_policy={"type": "count", "keep_last": 99},
    )
    db.add_all([overriding, inheriting])
    db.commit()
    db.refresh(overriding)
    db.refresh(inheriting)
    db.add(models.Node(hostname="node-override", ip_address="192.168.1.13", group_id=overriding.id))
    db.add(models.Node(hostname="node-inherit", ip_address="192.168.1.14", group_id=inheriting.id))
    db.commit()
    db.close()

    resolved = _retention_by_hostname()
    assert resolved["node-override"].secondly == 2
    # A policy on a group that does not claim the override is ignored entirely.
    assert resolved["node-inherit"].secondly == 10


# --- fleet-wide planning ---

def test_each_node_is_pruned_against_its_own_policy():
    from core.retention import RetentionRules

    archives = archives_for("alpha", 10) + archives_for("beta", 10)
    retention = {"alpha": RetentionRules(secondly=2), "beta": RetentionRules(secondly=5)}

    to_delete, report = plan_deletions(archives, retention, now=NOW)

    assert report["alpha"] == {"kept": 2, "deleted": 8}
    assert report["beta"] == {"kept": 5, "deleted": 5}
    assert len(to_delete) == 13


def test_archives_belonging_to_no_current_node_are_left_alone():
    """A renamed or deleted node's history is not this task's to throw away.

    Deleting on a failure to match would turn a hostname change into silent
    destruction of every archive that node ever produced.
    """
    from core.retention import RetentionRules

    archives = archives_for("alpha", 3) + archives_for("ghost", 3)
    to_delete, report = plan_deletions(archives, {"alpha": RetentionRules(secondly=1)}, now=NOW)

    assert "ghost" not in report
    assert not any("ghost" in name for name in to_delete)


def test_a_hostname_that_prefixes_another_does_not_steal_its_archives():
    """`node-1` must not claim `node-10`'s archives.

    The match is on `{hostname}-`, and archive names end in a timestamp, so
    "node-1-2026..." and "node-10-2026..." are distinguished by the hyphen.
    """
    from core.retention import RetentionRules

    archives = archives_for("node-1", 5) + archives_for("node-10", 5)
    retention = {"node-1": RetentionRules(secondly=1), "node-10": RetentionRules(secondly=1)}

    _, report = plan_deletions(archives, retention, now=NOW)
    assert report["node-1"] == {"kept": 1, "deleted": 4}
    assert report["node-10"] == {"kept": 1, "deleted": 4}


def test_the_newest_archive_is_never_in_the_delete_list():
    """A backstop over borg's rules, in case a policy resolves to nonsense."""
    from core.retention import RetentionRules

    archives = archives_for("alpha", 5)
    to_delete, _ = plan_deletions(archives, {"alpha": RetentionRules(daily=0)}, now=NOW)
    assert archives[0].name not in to_delete
    assert len(to_delete) == 4


# --- the run itself ---

def _borg_responses(archives):
    listing = {"archives": [
        {"name": a.name, "start": a.ts.isoformat()} for a in archives
    ]}
    import json

    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["borg", "list"]:
            return MagicMock(returncode=0, stdout=json.dumps(listing), stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return run


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_the_whole_fleet_is_pruned_in_three_borg_invocations(
    mock_exists, mock_run, mock_session, session_factory
):
    """The point of the rewrite: invocations do not scale with the fleet."""
    mock_exists.return_value = True
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    for i in range(25):
        db.add(models.Node(hostname=f"node-{i:02d}", ip_address=f"192.168.1.{i + 10}"))
    db.commit()
    db.close()

    archives = []
    for i in range(25):
        archives += archives_for(f"node-{i:02d}", 4)
    mock_run.side_effect = _borg_responses(archives)

    res = global_daily_prune()

    verbs = [c[0][0][1] for c in mock_run.call_args_list if c[0][0][0] == "borg"]
    # list, delete, compact, list again for the history reconciliation.
    assert verbs == ["list", "delete", "compact", "list"]
    # 25 nodes x 3 superseded archives, in one delete.
    assert res["deleted"] == 75

    delete_cmd = next(c[0][0] for c in mock_run.call_args_list if c[0][0][:2] == ["borg", "delete"])
    assert delete_cmd[2] == "/data/borg/fleet"
    assert len(delete_cmd) == 3 + 75


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_deletes_are_batched_so_argv_stays_sane(mock_exists, mock_run, mock_session, session_factory):
    """A fleet-wide delete list would otherwise blow past ARG_MAX."""
    mock_exists.return_value = True
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    db.add(models.Node(hostname="chatty", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    archives = archives_for("chatty", 501, step=timedelta(hours=1))
    mock_run.side_effect = _borg_responses(archives)

    with patch('backup_tasks.DELETE_BATCH', 200):
        res = global_daily_prune()

    delete_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][:2] == ["borg", "delete"]]
    assert len(delete_calls) == 3          # 500 deletions in batches of 200
    assert res["deleted"] == 500


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_a_second_prune_stands_down_rather_than_running_concurrently(
    mock_exists, mock_run, mock_session, session_factory
):
    """Two prunes against one repository is the case the flag exists to stop."""
    mock_exists.return_value = True
    mock_session.side_effect = session_factory
    mock_run.side_effect = _borg_responses([])

    from contextlib import contextmanager

    @contextmanager
    def already_held(owner, ttl=None):
        yield None

    with patch('backup_tasks.repository_maintenance', already_held):
        res = global_daily_prune()

    assert res["status"] == "SKIPPED"
    mock_run.assert_not_called()


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_dry_run_decides_but_deletes_nothing(mock_exists, mock_run, mock_session, session_factory):
    """BORG_PRUNE_DRY_RUN, for a first night on a real fleet."""
    mock_exists.return_value = True
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    mock_run.side_effect = _borg_responses(archives_for("alpha", 5))

    with patch('backup_tasks.PRUNE_DRY_RUN', True):
        res = global_daily_prune()

    assert res["status"] == "DRY_RUN"
    assert res["would_delete"] == 4
    assert not any(c[0][0][:2] == ["borg", "delete"] for c in mock_run.call_args_list)


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_history_is_reconciled_against_a_fresh_listing_not_the_delete_list(
    mock_exists, mock_run, mock_session, session_factory
):
    """A delete that silently failed must not orphan its history row.

    Deriving "what is left" by subtracting the intended deletions would remove
    the database record of an archive that is in fact still in the repository.
    """
    mock_exists.return_value = True
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    mock_run.side_effect = _borg_responses(archives_for("alpha", 3))
    global_daily_prune()

    list_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][:2] == ["borg", "list"]]
    assert len(list_calls) == 2
