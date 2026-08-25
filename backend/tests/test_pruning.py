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


@pytest.fixture(autouse=True)
def one_initialized_shard(monkeypatch):
    """Confine these to a single repository.

    The prune iterates every shard, but each of these tests is about what it
    does to one repository — invocation count, batching, dry run, standing
    down. Letting them see five would multiply every assertion by five without
    testing anything the shard-loop tests below do not already cover.
    """
    monkeypatch.setattr(
        "backup_tasks.repo_paths.all_shard_paths", lambda: ["/data/borg/fleet"]
    )
    monkeypatch.setattr(
        "backup_tasks.repo_paths.is_initialized", lambda path: True
    )


@pytest.fixture(autouse=True)
def maintenance_claim_succeeds(monkeypatch):
    """The prune gets the repository, so these tests can watch what it does.

    `repository_maintenance` is a Redis claim, and it yields None when the
    claim cannot be taken — including when Redis cannot be reached at all,
    which is correct: an unclaimable repository is one to leave alone, and the
    next nightly run picks it up.

    Correct, and fatal to a test about pruning: every assertion below is on
    borg invocations that never happen, and the prune reports SKIPPED for a
    reason unrelated to anything under test. Granting the claim here keeps
    these tests about retention, batching and the shard loop.

    `tests/test_repo_lock.py` covers the claim itself, contention included.
    """
    from contextlib import contextmanager

    @contextmanager
    def _granted(owner, ttl=None, repo_path=None):
        yield lambda: None  # the heartbeat callable; nothing to keep alive here

    monkeypatch.setattr("backup_tasks.repository_maintenance", _granted)


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
    # Matched by content, not position: the argv carries --lock-wait too, and
    # an index here would break the moment another flag is added.
    assert "/data/borg/fleet" in delete_cmd
    assert len([a for a in delete_cmd if a.startswith("node-")]) == 75


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
    def already_held(owner, ttl=None, repo_path=None):
        yield None

    with patch('backup_tasks.repository_maintenance', already_held):
        res = global_daily_prune()

    assert res["shards"]["/data/borg/fleet"]["status"] == "SKIPPED"
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

    shard = res["shards"]["/data/borg/fleet"]
    assert shard["status"] == "DRY_RUN"
    assert shard["would_delete"] == 4
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


# --- the shard loop ---

@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
def test_every_initialized_shard_is_pruned(mock_run, mock_session, session_factory, monkeypatch):
    monkeypatch.setattr(
        "backup_tasks.repo_paths.all_shard_paths",
        lambda: ["/data/borg/fleet", "/data/borg/shard-1"],
    )
    monkeypatch.setattr("backup_tasks.repo_paths.is_initialized", lambda path: True)
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    mock_run.side_effect = _borg_responses(archives_for("alpha", 3))
    res = global_daily_prune()

    pruned = {
        c[0][0][-1] for c in mock_run.call_args_list if c[0][0][:2] == ["borg", "compact"]
    }
    assert pruned == {"/data/borg/fleet", "/data/borg/shard-1"}
    assert set(res["shards"]) == {"/data/borg/fleet", "/data/borg/shard-1"}


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
def test_a_shard_with_no_repository_yet_is_skipped_not_failed(
    mock_run, mock_session, session_factory, monkeypatch
):
    """Shards past 0 do not exist until the first node routed to one backs up.
    An empty directory is 'nothing to do', not a failed nightly run."""
    monkeypatch.setattr(
        "backup_tasks.repo_paths.all_shard_paths",
        lambda: ["/data/borg/fleet", "/data/borg/shard-1"],
    )
    monkeypatch.setattr(
        "backup_tasks.repo_paths.is_initialized",
        lambda path: path == "/data/borg/fleet",
    )
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 1}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.commit()
    db.close()

    mock_run.side_effect = _borg_responses(archives_for("alpha", 3))
    res = global_daily_prune()

    assert res["shards"]["/data/borg/shard-1"] == "SKIPPED: not initialized"
    touched = {c[0][0][-1] for c in mock_run.call_args_list if c[0][0][:2] == ["borg", "list"]}
    assert touched == {"/data/borg/fleet"}


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
def test_history_is_reconciled_against_every_shard_at_once(
    mock_run, mock_session, session_factory, monkeypatch
):
    """The dangerous case. Reconciliation deletes history rows whose archive is
    absent, so running it per shard would wipe the history of every node living
    in the other shards."""
    monkeypatch.setattr(
        "backup_tasks.repo_paths.all_shard_paths",
        lambda: ["/data/borg/fleet", "/data/borg/shard-1"],
    )
    monkeypatch.setattr("backup_tasks.repo_paths.is_initialized", lambda path: True)
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 5}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.add(models.Node(hostname="beta", ip_address="192.168.1.11"))
    alpha = archives_for("alpha", 1)[0]
    beta = archives_for("beta", 1)[0]
    db.add(models.BackupHistory(node_id=1, archive_name=alpha.name, status="SUCCESS",
                                original_size=1, deduplicated_size=1))
    db.add(models.BackupHistory(node_id=2, archive_name=beta.name, status="SUCCESS",
                                original_size=1, deduplicated_size=1))
    db.commit()
    db.close()

    # Each shard reports only its own node's archive, as real shards would.
    import json as _json

    def per_shard(cmd, *args, **kwargs):
        if cmd[:2] == ["borg", "list"]:
            owned = alpha if cmd[-1] == "/data/borg/fleet" else beta
            return MagicMock(
                returncode=0,
                stdout=_json.dumps({"archives": [
                    {"name": owned.name, "start": owned.ts.isoformat()}
                ]}),
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = per_shard
    global_daily_prune()

    db = session_factory()
    surviving = {r.archive_name for r in db.query(models.BackupHistory).all()}
    db.close()
    assert surviving == {alpha.name, beta.name}, (
        "reconciliation ran against one shard's listing and deleted the other's history"
    )


@patch('database.SessionLocal')
@patch('backup_tasks.subprocess.run')
def test_an_unreadable_shard_suppresses_reconciliation_entirely(
    mock_run, mock_session, session_factory, monkeypatch
):
    """A partial view of the fleet's archives must not be used to decide which
    history rows are stale."""
    monkeypatch.setattr(
        "backup_tasks.repo_paths.all_shard_paths",
        lambda: ["/data/borg/fleet", "/data/borg/shard-1"],
    )
    monkeypatch.setattr("backup_tasks.repo_paths.is_initialized", lambda path: True)
    mock_session.side_effect = session_factory

    db = session_factory()
    db.add(models.Settings(retention_policy={"type": "count", "keep_last": 5}))
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10"))
    db.add(models.BackupHistory(node_id=1, archive_name="ghost-20260101000000", status="SUCCESS",
                                original_size=1, deduplicated_size=1))
    db.commit()
    db.close()

    def one_shard_unreadable(cmd, *args, **kwargs):
        if cmd[:2] == ["borg", "list"]:
            if cmd[-1] == "/data/borg/shard-1":
                return MagicMock(returncode=2, stdout="", stderr="repository unreadable")
            return MagicMock(returncode=0, stdout='{"archives": []}', stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = one_shard_unreadable
    global_daily_prune()

    db = session_factory()
    still_there = db.query(models.BackupHistory).count()
    db.close()
    assert still_there == 1, (
        "history was reconciled despite a shard that could not be listed"
    )


# --- the node's own last-backup marker ---
#
# Node.last_backup is written once, when a backup succeeds, and read by the
# fleet list. Reconciliation removes the history rows of archives that have
# left the repository, but used to leave that marker untouched — so a node
# whose archives were deleted outside the app went on advertising a backup
# date for an archive nobody could restore from, and the fleet and the archive
# list disagreed about the same node. Seen in production.

def _history(db, node, name, when, status="SUCCESS"):
    db.add(models.BackupHistory(
        node_id=node.id, archive_name=name, timestamp=when,
        original_size=0, deduplicated_size=0, status=status,
    ))


@patch('database.SessionLocal')
def test_a_node_that_lost_every_archive_stops_advertising_a_backup(
    mock_session, session_factory
):
    from backup_tasks import _reconcile_history_with_repo

    mock_session.side_effect = session_factory
    db = session_factory()
    node = models.Node(hostname="alpha", ip_address="192.168.1.10", last_backup=NOW)
    db.add(node)
    db.commit()
    _history(db, node, "alpha-1", NOW - timedelta(days=2))
    _history(db, node, "alpha-2", NOW)
    db.commit()
    db.close()

    _reconcile_history_with_repo(set())

    db = session_factory()
    assert db.query(models.Node).one().last_backup is None
    db.close()


@patch('database.SessionLocal')
def test_a_node_that_kept_an_archive_reports_the_newest_survivor(
    mock_session, session_factory
):
    """Not a blanket clear: pruning three of five archives leaves the node
    with a real backup, and the date must follow it rather than vanish."""
    from backup_tasks import _reconcile_history_with_repo

    mock_session.side_effect = session_factory
    db = session_factory()
    node = models.Node(hostname="alpha", ip_address="192.168.1.10", last_backup=NOW)
    db.add(node)
    db.commit()
    survivor_ts = NOW - timedelta(days=2)
    _history(db, node, "alpha-old", NOW - timedelta(days=9))
    _history(db, node, "alpha-mid", survivor_ts)
    _history(db, node, "alpha-new", NOW)
    db.commit()
    db.close()

    _reconcile_history_with_repo({"alpha-mid"})

    db = session_factory()
    assert db.query(models.Node).one().last_backup == survivor_ts
    db.close()


@patch('database.SessionLocal')
def test_a_node_whose_archives_are_all_present_is_left_alone(
    mock_session, session_factory
):
    from backup_tasks import _reconcile_history_with_repo

    mock_session.side_effect = session_factory
    db = session_factory()
    node = models.Node(hostname="alpha", ip_address="192.168.1.10", last_backup=NOW)
    db.add(node)
    db.commit()
    _history(db, node, "alpha-1", NOW)
    db.commit()
    db.close()

    _reconcile_history_with_repo({"alpha-1"})

    db = session_factory()
    assert db.query(models.Node).one().last_backup == NOW
    db.close()


@patch('database.SessionLocal')
def test_a_failed_run_does_not_resurrect_a_backup_date(
    mock_session, session_factory
):
    """Only successes ever set last_backup, so only successes may restore it —
    a node left with nothing but failures has no backup to point at."""
    from backup_tasks import _reconcile_history_with_repo

    mock_session.side_effect = session_factory
    db = session_factory()
    node = models.Node(hostname="alpha", ip_address="192.168.1.10", last_backup=NOW)
    db.add(node)
    db.commit()
    _history(db, node, "alpha-1", NOW)
    _history(db, node, "alpha-bad", NOW, status="FAILED")
    db.commit()
    db.close()

    _reconcile_history_with_repo(set())

    db = session_factory()
    assert db.query(models.Node).one().last_backup is None
    assert db.query(models.BackupHistory).count() == 1, "failures are not reconciled"
    db.close()


@patch('database.SessionLocal')
def test_a_marker_left_over_from_an_earlier_run_is_healed(
    mock_session, session_factory
):
    """The damage predates the fix: these nodes' history rows were already
    removed by an earlier reconciliation, so there is nothing stale left to
    notice them by. Every node is checked against what it actually has, not
    only the ones touched this run, or the fleet keeps its wrong dates for
    good."""
    from backup_tasks import _reconcile_history_with_repo

    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Node(hostname="alpha", ip_address="192.168.1.10", last_backup=NOW))
    db.commit()
    db.close()

    removed = _reconcile_history_with_repo(set())

    db = session_factory()
    assert removed == 0, "there was no history row to remove"
    assert db.query(models.Node).one().last_backup is None
    db.close()
