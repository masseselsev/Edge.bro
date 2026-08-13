"""Cost of one scheduler tick.

`tasks.scheduler_tick` fires every 60 seconds against the whole fleet, so its
per-node cost is paid 1440 times a day. It used to issue, for each node it
considered: a Redis GET for the running lock (up to three times, from different
branches), a BackupHistory query for "did this already succeed in the window",
another for the retry cooldown, and a third inside the duration estimator. At
2000 nodes that is thousands of SQL statements and thousands of Redis round
trips per minute, whether or not anything was due.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from core.scheduler import check_and_trigger_backups
from database import Base


@pytest.fixture
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    yield db, engine
    db.close()


def _seed(db, node_count, with_history=True):
    group = models.BackupGroup(
        name="G", interval="weekly", start_time="02:00", end_time="05:00",
        concurrency_limit=5, randomize_days=True, timezone="UTC",
        upload_rate_limit=2000,   # forces the duration estimator to engage
    )
    db.add(group)
    db.commit()
    db.refresh(group)

    now = datetime(2026, 6, 15, 3, 0)
    for i in range(node_count):
        node = models.Node(
            hostname=f"sched-{i:04d}", ip_address=f"10.70.{i // 256}.{i % 256}",
            group_id=group.id, backup_paused=False, status="READY",
            backup_today=True,          # every node wants to run
            last_ping_status=True,
        )
        db.add(node)
        db.flush()
        if with_history:
            for d in (1, 8, 15):
                db.add(models.BackupHistory(
                    node_id=node.id, archive_name=f"a{i}-{d}",
                    timestamp=now - timedelta(days=d),
                    original_size=3_000_000_000, deduplicated_size=50_000_000,
                    status="SUCCESS",
                ))
    db.commit()
    return now


class Counter:
    def __init__(self, engine):
        self.engine = engine
        self.selects = 0

    def __enter__(self):
        @event.listens_for(self.engine, "before_cursor_execute")
        def _c(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                self.selects += 1

        self._h = _c
        return self

    def __exit__(self, *e):
        event.remove(self.engine, "before_cursor_execute", self._h)
        return False


def _run(db, engine, now, redis_mock):
    redis_mock.get.return_value = None
    redis_mock.mget.side_effect = lambda keys: [None] * len(keys)
    with Counter(engine) as counter:
        with patch("core.scheduler.datetime") as dt:
            dt.utcnow.return_value = now
            check_and_trigger_backups(db)
    return counter.selects


@patch("core.scheduler.redis_client")
@patch("core.scheduler.run_backup_task")
def test_tick_query_count_does_not_scale_with_fleet_size(_task, redis_mock, env):
    db, engine = env
    now = _seed(db, 10)
    small = _run(db, engine, now, redis_mock)

    # Grow the fleet tenfold and tick again.
    group = db.query(models.BackupGroup).one()
    base = db.query(models.Node).count()
    for i in range(base, base + 90):
        node = models.Node(
            hostname=f"sched-{i:04d}", ip_address=f"10.71.{i // 256}.{i % 256}",
            group_id=group.id, backup_paused=False, status="READY",
            backup_today=True, last_ping_status=True,
        )
        db.add(node)
        db.flush()
        for d in (1, 8, 15):
            db.add(models.BackupHistory(
                node_id=node.id, archive_name=f"b{i}-{d}",
                timestamp=now - timedelta(days=d),
                original_size=3_000_000_000, deduplicated_size=50_000_000,
                status="SUCCESS",
            ))
    db.commit()

    large = _run(db, engine, now, redis_mock)

    assert large <= small + 2, (
        f"a tick issued {small} SELECTs for 10 nodes and {large} for 100. "
        "The per-node queries are still there."
    )


@patch("core.scheduler.redis_client")
@patch("core.scheduler.run_backup_task")
def test_tick_reads_locks_in_one_round_trip(_task, redis_mock, env):
    db, engine = env
    now = _seed(db, 60)
    redis_mock.get.return_value = None
    redis_mock.mget.side_effect = lambda keys: [None] * len(keys)

    with patch("core.scheduler.datetime") as dt:
        dt.utcnow.return_value = now
        check_and_trigger_backups(db)

    assert redis_mock.get.call_count == 0, (
        f"{redis_mock.get.call_count} per-node Redis GETs; the tick should "
        "read every lock with a single MGET"
    )
    assert redis_mock.mget.call_count == 1, (
        f"expected exactly one MGET, saw {redis_mock.mget.call_count}"
    )


@patch("core.scheduler.redis_client")
@patch("core.scheduler.run_backup_task")
def test_absolute_query_budget_for_a_single_group(_task, redis_mock, env):
    """A one-group tick should be a handful of statements, not dozens."""
    db, engine = env
    now = _seed(db, 200)
    count = _run(db, engine, now, redis_mock)
    assert count <= 10, (
        f"{count} SELECTs for a single-group tick over 200 nodes; expected a "
        "fixed handful (nodes, groups, window successes, failures, estimates)"
    )
