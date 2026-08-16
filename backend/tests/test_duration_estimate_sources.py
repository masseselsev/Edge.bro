"""Where a backup-duration estimate comes from, and in which order.

The scheduler, the load calendar and the running-backup lock are all sized from
one estimate. It used to be computed as bytes over the group's configured rate
limit, which returned nothing at all when no limit was set — so the constant
took over and a node doing thirty-second increments was priced identically to
one doing six-hour transfers.

Measured wall time has been recorded on every run since the duration column
landed, and it answers the question directly. These tests pin the order of the
four sources, and in particular the two cases that are easy to get backwards:
a node whose history is one long first run and many short increments, and a
node with no history at all.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import schedule_estimate as se
from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_duration_estimate_sources_db.db"

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
        if os.path.exists("./test_duration_estimate_sources_db.db"):
            os.remove("./test_duration_estimate_sources_db.db")


def _node(db, hostname="n1"):
    # ip_address is unique, so it has to follow the row count rather than be
    # a constant -- several of these tests need more than one node.
    ordinal = db.query(models.Node).count() + 1
    node = models.Node(
        hostname=hostname, ip_address=f"10.0.0.{ordinal}", status="READY"
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _run(db, node, seconds, dedup=1_000_000, status="SUCCESS", suffix=""):
    """One recorded backup. `archive_name` is unique, so it carries a counter."""
    existing = db.query(models.BackupHistory).count()
    db.add(models.BackupHistory(
        node_id=node.id,
        archive_name=f"{node.hostname}-{existing}{suffix}",
        original_size=dedup * 3,
        deduplicated_size=dedup,
        status=status,
        duration_seconds=seconds,
    ))
    db.commit()


# ─────────────────────────── measured time wins ───────────────────────────


def test_measured_time_is_used_even_without_a_rate_limit(db_session):
    """The case the old code could not reach at all.

    No rate limit means the previous implementation returned the constant
    without so much as looking at history.
    """
    node = _node(db_session)
    for _ in range(3):
        _run(db_session, node, seconds=90.0)

    estimator = se.DurationEstimator(db_session, [node.id])
    minutes, source = estimator.resolve(node.id, rate_kib_s=None)

    assert source == se.SOURCE_MEASURED
    assert minutes == pytest.approx(1.5)
    assert minutes != se.DEFAULT_BACKUP_MINUTES


def test_a_first_full_backup_does_not_drag_the_increments_up(db_session):
    """Median, not mean.

    A node's first run is a whole disk image and every one after is an
    increment. A mean of one six-hour run and four three-minute ones prices
    the node at an hour and a quarter, which is true of no run it has made.
    """
    node = _node(db_session)
    _run(db_session, node, seconds=6 * HOUR)
    for _ in range(4):
        _run(db_session, node, seconds=180.0)

    estimator = se.DurationEstimator(db_session, [node.id])
    assert estimator.minutes(node.id, None) == pytest.approx(3.0)


def test_failed_runs_are_not_evidence_of_how_long_success_takes(db_session):
    node = _node(db_session)
    _run(db_session, node, seconds=120.0)
    _run(db_session, node, seconds=5.0, status="FAILED")
    _run(db_session, node, seconds=120.0)

    estimator = se.DurationEstimator(db_session, [node.id])
    assert estimator.minutes(node.id, None) == pytest.approx(2.0)


def test_only_the_recent_sample_counts(db_session):
    """Old runs stop mattering once there are enough newer ones."""
    node = _node(db_session)
    for _ in range(se.HISTORY_SAMPLE_SIZE + 3):
        _run(db_session, node, seconds=60.0)

    estimator = se.DurationEstimator(db_session, [node.id])
    assert estimator.minutes(node.id, None) == pytest.approx(1.0)


# ─────────────────────────── a node that has never run ───────────────────────────


def test_a_new_node_is_priced_from_the_fleet_full_backup_not_its_increments(db_session):
    """The first backup is the expensive one, and has no history of its own.

    Borrowing the fleet's *typical* run would price a whole disk image as an
    increment and understate it by two orders of magnitude.
    """
    veteran = _node(db_session, "veteran")
    _run(db_session, veteran, seconds=4 * HOUR)       # its full backup
    for _ in range(4):
        _run(db_session, veteran, seconds=60.0)       # its increments

    fresh = _node(db_session, "fresh")

    estimator = se.DurationEstimator(db_session, [veteran.id, fresh.id])
    minutes, source = estimator.resolve(fresh.id, rate_kib_s=None)

    assert source == se.SOURCE_FLEET_FIRST
    assert minutes == pytest.approx(240.0), (
        "a node's first full backup was priced as somebody else's increment"
    )
    # And the veteran itself is still priced on its own increments.
    assert estimator.minutes(veteran.id, None) == pytest.approx(1.0)


def test_the_fleet_figure_survives_retention_pruning_old_rows(db_session):
    """Length identifies a full backup; age does not.

    Retention prunes old archives and deletes the matching history rows, so a
    node's earliest surviving record is frequently an increment. Picking the
    longest run rather than the earliest is what makes this stable.
    """
    node = _node(db_session, "pruned")
    # Its true first backup is gone; what survives is an increment, then the
    # re-full after a disk change, then more increments.
    _run(db_session, node, seconds=60.0)
    _run(db_session, node, seconds=3 * HOUR)
    _run(db_session, node, seconds=60.0)

    assert se.fleet_full_backup_minutes(db_session) == pytest.approx(180.0)


def test_one_pathological_run_does_not_set_the_figure_for_everybody(db_session):
    """Median across nodes, so a link that dropped mid-transfer stays local."""
    for i in range(4):
        node = _node(db_session, f"normal{i}")
        _run(db_session, node, seconds=2 * HOUR)

    stuck = _node(db_session, "stuck")
    _run(db_session, stuck, seconds=40 * HOUR)

    assert se.fleet_full_backup_minutes(db_session) == pytest.approx(120.0)


# ─────────────────────────── the remaining tiers ───────────────────────────


def test_bytes_over_the_rate_limit_still_applies_when_nothing_has_run(db_session):
    node = _node(db_session)
    # 6 MiB at 1024 KiB/s is 6 seconds shy of 0.1 hours.
    _run(db_session, node, seconds=None, dedup=6 * 1024 * 1024)

    estimator = se.DurationEstimator(db_session, [node.id])
    minutes, source = estimator.resolve(node.id, rate_kib_s=1024)

    assert source == se.SOURCE_RATE_LIMIT
    assert minutes == pytest.approx(0.1, abs=0.01)


def test_the_constant_is_the_last_resort_only(db_session):
    node = _node(db_session)

    estimator = se.DurationEstimator(db_session, [node.id])
    minutes, source = estimator.resolve(node.id, rate_kib_s=None)

    assert source == se.SOURCE_DEFAULT
    assert minutes == pytest.approx(se.DEFAULT_BACKUP_MINUTES)


def test_is_measured_distinguishes_a_figure_from_a_guess(db_session):
    measured = _node(db_session, "measured")
    _run(db_session, measured, seconds=120.0)
    guessed = _node(db_session, "guessed")

    estimator = se.DurationEstimator(db_session, [measured.id, guessed.id])
    assert estimator.is_measured(measured.id) is True
    assert estimator.is_measured(guessed.id) is False


# ─────────────────────────── the lock the estimate sizes ───────────────────────────


def test_the_backup_lock_is_sized_from_measured_time(db_session):
    """The TTL has to outlive the backup, and measured time is what that means."""
    node = _node(db_session)
    for _ in range(3):
        _run(db_session, node, seconds=6 * HOUR)

    ttl = se.backup_lock_ttl_seconds(db_session, node.id, rate_kib_s=None)

    assert ttl == pytest.approx(6 * HOUR * se.LOCK_TTL_SAFETY_FACTOR)
    assert ttl > se.MIN_LOCK_TTL_SECONDS


def test_the_lock_stays_within_its_bounds_on_the_new_basis(db_session):
    """A quick node keeps the floor; a pathological one keeps the ceiling."""
    quick = _node(db_session, "quick")
    _run(db_session, quick, seconds=30.0)
    assert se.backup_lock_ttl_seconds(db_session, quick.id, None) == se.MIN_LOCK_TTL_SECONDS

    endless = _node(db_session, "endless")
    _run(db_session, endless, seconds=40 * HOUR)
    assert se.backup_lock_ttl_seconds(db_session, endless.id, None) == se.MAX_LOCK_TTL_SECONDS


def test_a_node_that_has_never_run_still_gets_the_floor(db_session):
    """No history means no measurement, and the floor is what it fell back to
    before this change too."""
    node = _node(db_session)
    assert se.backup_lock_ttl_seconds(db_session, node.id, None) == se.MIN_LOCK_TTL_SECONDS


# ─────────────────────────── group averages ───────────────────────────


def test_a_group_without_a_rate_limit_is_no_longer_priced_at_the_constant(db_session):
    group = models.BackupGroup(
        name="g", interval="weekly", start_time="02:00", end_time="05:00",
        timezone="UTC", upload_rate_limit=None,
    )
    db_session.add(group)
    db_session.commit()

    nodes = []
    for i in range(3):
        node = _node(db_session, f"g{i}")
        _run(db_session, node, seconds=120.0)
        nodes.append(node)

    minutes = se.estimate_group_backup_minutes(db_session, group, nodes)
    assert minutes == pytest.approx(2.0)
    assert minutes != se.DEFAULT_BACKUP_MINUTES


def test_an_empty_group_falls_back_rather_than_dividing_by_zero(db_session):
    group = models.BackupGroup(
        name="empty", interval="weekly", start_time="02:00", end_time="05:00",
        timezone="UTC",
    )
    db_session.add(group)
    db_session.commit()

    assert se.estimate_group_backup_minutes(db_session, group, []) == se.DEFAULT_BACKUP_MINUTES
