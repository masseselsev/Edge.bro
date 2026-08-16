"""The repository capacity report, end to end from the database.

`core/repo_capacity` is tested on its own as pure arithmetic. What this file
checks is the wiring: that the endpoint reads each node's stored repository
rather than assuming a spread, that it survives a fleet with nothing in it, and
that the two claims an operator would act on come out right — how full the
busiest repository is, and whether adding repositories would change that.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import repo_paths
from database import Base
import models
from routers.stats import get_repository_capacity

TEST_DATABASE_URL = "sqlite:///./test_repository_capacity_db.db"

HOUR = 3600.0


@pytest.fixture
def db_session():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_repository_capacity_db.db"):
            os.remove("./test_repository_capacity_db.db")


def _group(db, name="nightly", start="02:00", end="05:00"):
    group = models.BackupGroup(
        name=name, interval="weekly", target_week=1,
        start_time=start, end_time=end, timezone="UTC",
        concurrency_limit=5, randomize_days=False,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def _node_with_history(db, group, shard_index, hostname, run_seconds, runs=3):
    ordinal = db.query(models.Node).count() + 1
    node = models.Node(
        hostname=hostname, ip_address=f"10.0.0.{ordinal}", status="READY",
        group_id=group.id, borg_shard_index=shard_index,
    )
    db.add(node)
    db.commit()
    db.refresh(node)

    for i in range(runs):
        db.add(models.BackupHistory(
            node_id=node.id, archive_name=f"{hostname}-{i}",
            original_size=3_000_000, deduplicated_size=1_000_000,
            status="SUCCESS", duration_seconds=run_seconds,
        ))
    db.commit()
    return node


def test_an_empty_fleet_reports_nothing_rather_than_failing(db_session, monkeypatch):
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    db_session.add(models.Settings())
    db_session.commit()

    report = get_repository_capacity(db=db_session)

    assert report.shard_count == 1
    assert report.peak.utilization_pct is None
    assert report.capacity.per_night == 0
    assert report.ceiling.sufficient is False


def test_load_lands_on_the_repository_the_node_is_actually_assigned_to(
    db_session, monkeypatch
):
    """Not on an assumed even spread across the configured count."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 3)
    db_session.add(models.Settings())
    group = _group(db_session)

    # Every node on repository 0, which is what `node_id % 3` produces for a
    # fleet enrolled before the count was raised.
    for i in range(3):
        _node_with_history(db_session, group, 0, f"n{i}", run_seconds=HOUR)

    report = get_repository_capacity(db=db_session)
    by_index = {s.index: s for s in report.shards}

    assert by_index[0].nodes == 3
    assert by_index[1].nodes == 0
    assert by_index[2].nodes == 0
    # Three one-hour backups serialised into a three-hour window.
    assert by_index[0].busiest_night_hours == pytest.approx(3.0)
    assert by_index[0].utilization_pct == pytest.approx(100.0)
    assert report.peak.shard_index == 0


def test_the_same_nodes_spread_across_repositories_are_not_crowded(
    db_session, monkeypatch
):
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 3)
    db_session.add(models.Settings())
    group = _group(db_session)

    for i in range(3):
        _node_with_history(db_session, group, i, f"n{i}", run_seconds=HOUR)

    report = get_repository_capacity(db=db_session)

    assert report.peak.utilization_pct == pytest.approx(33.3, abs=0.2)
    assert all(s.nodes == 1 for s in report.shards)


def test_measured_duration_drives_the_load_not_the_thirty_minute_constant(
    db_session, monkeypatch
):
    """A node doing short increments must not be priced as half an hour."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    db_session.add(models.Settings())
    group = _group(db_session)

    for i in range(4):
        _node_with_history(db_session, group, 0, f"quick{i}", run_seconds=60.0)

    report = get_repository_capacity(db=db_session)

    # Four one-minute backups, not four half-hours.
    assert report.peak.hours == pytest.approx(4 / 60.0, abs=0.01)
    assert report.capacity.median_node_hours == pytest.approx(1 / 60.0, abs=0.001)


def test_expansion_never_claims_relief_for_the_existing_fleet(
    db_session, monkeypatch
):
    """The claim an operator would act on, and the one that must not be wrong.

    A node's repository is fixed at enrolment. Raising the count routes new
    enrolments elsewhere and moves nobody, so an overloaded repository stays
    overloaded at every candidate count.
    """
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    db_session.add(models.Settings())
    group = _group(db_session)

    for i in range(6):
        _node_with_history(db_session, group, 0, f"n{i}", run_seconds=HOUR)

    report = get_repository_capacity(db=db_session)

    assert report.peak.utilization_pct == pytest.approx(200.0)
    assert len(report.expansion) > 1
    for outlook in report.expansion:
        assert outlook.relieves_existing is False
        assert outlook.busiest_utilization_pct == pytest.approx(200.0), (
            "adding repositories was reported as relieving nodes that cannot move"
        )
        assert outlook.new_node_headroom == 0


def test_a_floored_count_is_reported_as_floored(db_session, monkeypatch):
    """Repositories on disk override a lowered environment variable, and the
    report has to say the setting is not what is in force."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 4)
    monkeypatch.setattr(repo_paths, "CONFIGURED_SHARD_COUNT", 1)
    db_session.add(models.Settings())
    db_session.commit()

    report = get_repository_capacity(db=db_session)

    assert report.shard_count == 4
    assert report.configured_shard_count == 1
    assert report.count_floored is True


def test_sequential_history_cannot_measure_the_storage(db_session, monkeypatch):
    """The lab case: three nodes, one repository, no backup ever concurrent."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    db_session.add(models.Settings())
    group = _group(db_session)
    node = _node_with_history(db_session, group, 0, "solo", run_seconds=HOUR)

    for row in db_session.query(models.BackupHistory).filter_by(node_id=node.id):
        row.avg_speed_mbps = 40.0
    db_session.commit()

    report = get_repository_capacity(db=db_session)

    assert report.ceiling.sufficient is False
    assert report.ceiling.supported_writers is None
    assert report.binding_constraint == "repositories", (
        "an unmeasured storage path was reported as the binding constraint"
    )


def test_every_repository_appears_even_before_it_holds_anything(
    db_session, monkeypatch
):
    """Repositories past the first are created lazily, on the first backup of
    the first node routed there. An empty one is not a fault."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 5)
    db_session.add(models.Settings())
    db_session.commit()

    report = get_repository_capacity(db=db_session)

    assert [s.index for s in report.shards] == [0, 1, 2, 3, 4]
    assert all(s.initialized is False for s in report.shards)
    assert all(s.size_bytes is None for s in report.shards)
