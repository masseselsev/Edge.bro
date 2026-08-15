"""Diagnostic tests documenting scheduler defects found during the schedule audit.

These are written to FAIL against current behaviour where a defect exists, so
the defect is demonstrated rather than asserted. Each test states the expected
correct behaviour for the low-bandwidth / unstable-node deployment scenario.
"""
import os
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

import models
from database import Base
from core.scheduler import check_and_trigger_backups, deterministic_hash

TEST_DATABASE_URL = "sqlite:///./test_scheduler_audit_db.db"


@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_scheduler_audit_db.db"):
            os.remove("./test_scheduler_audit_db.db")


@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_bandwidth_cap_must_not_be_overridden_by_dynamic_concurrency(mock_run_backup_task, mock_redis, test_db):
    """A group's bandwidth-derived concurrency cap must be an upper bound.

    scheduler.py caps base_concurrency by upload_rate_limit (line ~128), then
    immediately discards that cap with
        effective_concurrency = max(base_concurrency, required_concurrency)
    so a link deliberately limited to 2 Mbit can still be handed 10 parallel
    backups whenever the window is tight. On the links this product targets
    that saturates the uplink and makes every backup slower, not faster.
    """
    mock_redis.get.return_value = None

    # 2 Mbit ~= 256 KiB/s. bandwidth_concurrency = max(1, 256 // 2048) = 1
    group = models.BackupGroup(
        name="SlowLinkGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=2,
        randomize_days=False,   # all nodes land on day_index 0 (Monday)
        upload_rate_limit=256,
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)

    for i in range(20):
        test_db.add(models.Node(
            hostname=f"slow-node-{i:02d}",
            ip_address=f"192.168.50.{i + 10}",
            group_id=group.id,
            backup_paused=False,
            backup_today=False,
            missed_window=False,
            status="READY",
        ))
    test_db.commit()

    # Monday 2026-06-15, 04:00 UTC — inside the window, 60 min left.
    # pending=20 -> required_concurrency = ceil(20*30/60) = 10
    now = datetime(2026, 6, 15, 4, 0)
    with patch('core.scheduler.utcnow', return_value=now):
        check_and_trigger_backups(test_db)

    triggered = mock_run_backup_task.delay.call_count
    assert triggered <= 1, (
        f"Bandwidth cap says at most 1 concurrent backup on a 256 KiB/s link, "
        f"but the scheduler started {triggered}."
    )


@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_missed_window_uses_each_groups_own_local_time(mock_run_backup_task, mock_redis, test_db):
    """missed_window must be judged against the group's OWN local clock.

    `local_mins` is assigned inside the group_cache loop (scheduler.py:80) but
    read in the trigger loop (scheduler.py:218,220), where it is neither
    recomputed nor read back from group_cache. Python leaks for-loop variables
    to function scope, so every group is judged using the LAST group's local
    time. With groups in different timezones this marks nodes as having missed
    a window that has not even started yet — which then shows a false "missed"
    badge in the Fleet UI.
    """
    mock_redis.get.return_value = None

    # Group A: UTC. At 01:00 UTC its 02:00-05:00 window has NOT started.
    group_utc = models.BackupGroup(
        name="GroupUTC",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=False,
        timezone="UTC",
    )
    # Group B: UTC+5. At 01:00 UTC it is 06:00 locally — its window IS past.
    # Created second so it is iterated last and its local_mins is the one that leaks.
    group_east = models.BackupGroup(
        name="GroupEast",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=False,
        timezone="Asia/Tashkent",
    )
    test_db.add(group_utc)
    test_db.add(group_east)
    test_db.commit()
    test_db.refresh(group_utc)
    test_db.refresh(group_east)

    node_utc = models.Node(
        hostname="utc-node",
        ip_address="192.168.60.10",
        group_id=group_utc.id,
        backup_paused=False,
        backup_today=False,
        missed_window=False,
        status="READY",
    )
    test_db.add(node_utc)
    test_db.commit()
    test_db.refresh(node_utc)

    # Monday 01:00 UTC — before GroupUTC's window opens.
    now = datetime(2026, 6, 15, 1, 0)
    with patch('core.scheduler.utcnow', return_value=now):
        check_and_trigger_backups(test_db)

    test_db.refresh(node_utc)
    assert node_utc.missed_window is False, (
        "Node was marked as having missed its window at 01:00 UTC, but its "
        "02:00-05:00 UTC window had not started yet."
    )


# --- point 3: the load projection must agree with the real scheduler ---

def test_quarterly_node_runs_exactly_once_per_quarter():
    """Each quarterly node must land on exactly one day per quarter.

    The old load map assumed every quarterly node ran in the first month of the
    quarter on group.target_week, while the scheduler spread them across all
    three months with a per-node week/day. Both now derive from the same slot
    module, so a node's run days are well-defined and unique.
    """
    from core.schedule_slots import is_scheduled_on, parse_window

    class _Group:
        interval = "quarterly"
        randomize_days = True
        target_week = 1
        start_time = "02:00"
        end_time = "05:00"

    window = parse_window(_Group.start_time, _Group.end_time)

    for i in range(8):
        hostname = f"edge-node-{i:02d}"
        run_days = []
        # Q1 2026: January 1 .. March 31
        for month, days in ((1, 31), (2, 28), (3, 31)):
            for day in range(1, days + 1):
                dt = datetime(2026, month, day, 2, 0)
                if is_scheduled_on(_Group, hostname, dt, window):
                    run_days.append((month, day))
        assert len(run_days) == 1, f"{hostname} runs {len(run_days)} times per quarter: {run_days}"


def test_quarterly_load_is_spread_across_the_whole_quarter():
    """Across enough nodes, quarterly runs must appear in all three months.

    This is what the old projection got wrong: it reported zero load for
    months 2 and 3 of every quarter.
    """
    from core.schedule_slots import is_scheduled_on, parse_window

    class _Group:
        interval = "quarterly"
        randomize_days = True
        target_week = 1
        start_time = "02:00"
        end_time = "05:00"

    window = parse_window(_Group.start_time, _Group.end_time)
    months_used = set()
    for i in range(40):
        hostname = f"edge-node-{i:02d}"
        for month, days in ((1, 31), (2, 28), (3, 31)):
            for day in range(1, days + 1):
                if is_scheduled_on(_Group, hostname, datetime(2026, month, day, 2, 0), window):
                    months_used.add(month)

    assert months_used == {1, 2, 3}, f"quarterly load only lands in months {sorted(months_used)}"


# --- point 7: don't waste a slot and a cooldown on a node that is down ---

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_offline_node_is_skipped(mock_run_backup_task, mock_redis, test_db, monkeypatch):
    mock_redis.get.return_value = None

    # Enough repositories that the shard ceiling is not what limits dispatch —
    # this test is about which nodes are eligible, not how many run at once.
    from core import repo_paths
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 5)

    group = models.BackupGroup(
        name="PingGroup", interval="weekly", start_time="02:00", end_time="05:00",
        concurrency_limit=5, randomize_days=False,
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)

    offline = models.Node(
        hostname="offline-node", ip_address="192.168.70.10", group_id=group.id,
        backup_paused=False, status="READY", last_ping_status=False,
    )
    online = models.Node(
        hostname="online-node", ip_address="192.168.70.11", group_id=group.id,
        backup_paused=False, status="READY", last_ping_status=True,
    )
    never_pinged = models.Node(
        hostname="unknown-node", ip_address="192.168.70.12", group_id=group.id,
        backup_paused=False, status="READY", last_ping_status=None,
    )
    test_db.add_all([offline, online, never_pinged])
    test_db.commit()
    for n in (offline, online, never_pinged):
        test_db.refresh(n)

    with patch('core.scheduler.utcnow', return_value=datetime(2026, 6, 15, 3, 0)):
        check_and_trigger_backups(test_db)

    triggered_ids = {c.args[0] for c in mock_run_backup_task.delay.call_args_list}
    assert offline.id not in triggered_ids, "backup was started on a node whose last ping failed"
    # Unknown ping state must still be attempted — absence of data is not absence of a node.
    assert online.id in triggered_ids
    assert never_pinged.id in triggered_ids


# --- point 6: the running-lock must outlive a slow backup ---

def test_lock_ttl_scales_with_measured_backup_size(test_db):
    from core.schedule_estimate import backup_lock_ttl_seconds, MIN_LOCK_TTL_SECONDS

    node = models.Node(hostname="big-node", ip_address="192.168.80.10", status="READY")
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)

    # No history yet -> unchanged 4h floor.
    assert backup_lock_ttl_seconds(test_db, node.id, 256) == MIN_LOCK_TTL_SECONDS

    # 8 GiB of measured transfer at 256 KiB/s ~= 9.1 h, so a 4h lock would
    # expire mid-backup and the next tick would kill the running borg.
    eight_gib = 8 * 1024 * 1024 * 1024
    for i in range(3):
        test_db.add(models.BackupHistory(
            node_id=node.id, archive_name=f"a{i}", original_size=eight_gib,
            deduplicated_size=eight_gib, status="SUCCESS",
        ))
    test_db.commit()

    ttl = backup_lock_ttl_seconds(test_db, node.id, 256)
    assert ttl > MIN_LOCK_TTL_SECONDS, "TTL did not grow for a backup that takes ~9 hours"
    assert ttl >= 9 * 3600, f"TTL {ttl}s is shorter than the expected ~9h transfer"
