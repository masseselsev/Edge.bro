import os
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import models
from database import Base
from core.scheduler import check_and_trigger_backups, deterministic_hash

TEST_DATABASE_URL = "sqlite:///./test_scheduler_db.db"

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
        if os.path.exists("./test_scheduler_db.db"):
            os.remove("./test_scheduler_db.db")

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_trigger_normal_window(mock_run_backup_task, mock_redis, test_db):
    mock_redis.get.return_value = None
    mock_redis.mget.side_effect = lambda keys: [None] * len(keys)
    
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=True
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    
    node = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=False,
        backup_today=False,
        status="READY"
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    
    node_hash = deterministic_hash(node.hostname)
    day_index = node_hash % 7
    window_duration_hours = 3
    hour_offset = node_hash % window_duration_hours
    minute_offset = (node_hash // window_duration_hours) % 60
    
    scheduled_hour = (2 + hour_offset) % 24
    scheduled_minute = minute_offset % 60
    
    target_date = datetime(2026, 6, 15) + timedelta(days=day_index)
    target_time = target_date.replace(hour=scheduled_hour, minute=scheduled_minute)
    
    with patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)
        
    mock_run_backup_task.delay.assert_called_once_with(node.id, comment="Automated scheduler execution (Group: NightlyGroup)")
    mock_redis.setex.assert_called_once()

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_paused_node(mock_run_backup_task, mock_redis, test_db):
    mock_redis.get.return_value = None
    mock_redis.mget.side_effect = lambda keys: [None] * len(keys)
    
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=True
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    
    node = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=True,
        backup_today=False,
        status="READY"
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    
    node_hash = deterministic_hash(node.hostname)
    day_index = node_hash % 7
    window_duration_hours = 3
    hour_offset = node_hash % window_duration_hours
    scheduled_hour = (2 + hour_offset) % 24
    scheduled_minute = (node_hash // window_duration_hours) % 60
    
    target_date = datetime(2026, 6, 15) + timedelta(days=day_index)
    target_time = target_date.replace(hour=scheduled_hour, minute=scheduled_minute)
    
    with patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)
        
    mock_run_backup_task.delay.assert_not_called()

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_concurrency_limit(mock_run_backup_task, mock_redis, test_db):
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=1,
        randomize_days=True
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    
    node1 = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=False,
        status="READY"
    )
    node2 = models.Node(
        hostname="node-02",
        ip_address="192.168.1.11",
        group_id=group.id,
        backup_paused=False,
        status="READY"
    )
    test_db.add(node1)
    test_db.add(node2)
    test_db.commit()
    test_db.refresh(node1)
    test_db.refresh(node2)
    
    # node1 is mid-backup. The scheduler reads these locks with one MGET now,
    # so both accessors have to answer from the same rule.
    def lock_for(key):
        return b"1" if str(node1.id) in key else None

    mock_redis.get.side_effect = lock_for
    mock_redis.mget.side_effect = lambda keys: [lock_for(k) for k in keys]
    
    node2_hash = deterministic_hash(node2.hostname)
    day_index = node2_hash % 7
    window_duration_hours = 3
    hour_offset = node2_hash % window_duration_hours
    scheduled_hour = (2 + hour_offset) % 24
    scheduled_minute = (node2_hash // window_duration_hours) % 60
    
    target_date = datetime(2026, 6, 15) + timedelta(days=day_index)
    target_time = target_date.replace(hour=scheduled_hour, minute=scheduled_minute)
    
    with patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)
        
    mock_run_backup_task.delay.assert_not_called()

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_backup_today_outside_schedule_but_in_window(mock_run_backup_task, mock_redis, test_db):
    mock_redis.get.return_value = None
    mock_redis.mget.side_effect = lambda keys: [None] * len(keys)
    
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=True
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    
    node = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=False,
        backup_today=True,
        status="READY"
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    
    node_hash = deterministic_hash(node.hostname)
    scheduled_day = node_hash % 7
    non_scheduled_day = (scheduled_day + 1) % 7
    
    target_date = datetime(2026, 6, 15) + timedelta(days=non_scheduled_day)
    target_time = target_date.replace(hour=2, minute=30)
    
    with patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)
        
    mock_run_backup_task.delay.assert_called_once()

@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_missed_window_marking(mock_run_backup_task, mock_redis, test_db):
    mock_redis.get.return_value = None
    mock_redis.mget.side_effect = lambda keys: [None] * len(keys)
    
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=True
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    
    node = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=False,
        backup_today=True,
        missed_window=False,
        status="READY"
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    
    target_time = datetime(2026, 6, 15, 6, 0)
    
    with patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)
        
    test_db.refresh(node)
    
    assert node.missed_window is True
    assert node.backup_today is False
    mock_run_backup_task.delay.assert_not_called()


def test_check_and_trigger_backups_accepts_now(test_db):
    # Verify that the function runs and accepts a manual datetime object
    fake_time = datetime(2026, 6, 29, 2, 30)
    check_and_trigger_backups(test_db, now=fake_time)


@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_scheduler_retry_delay(mock_run_backup_task, mock_redis, test_db):
    mock_redis.get.return_value = None
    mock_redis.mget.side_effect = lambda keys: [None] * len(keys)
    
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=5,
        randomize_days=False
    )
    test_db.add(group)
    test_db.commit()
    
    node = models.Node(
        hostname="node-01",
        ip_address="192.168.1.10",
        group_id=group.id,
        backup_paused=False,
        backup_today=True,
        status="READY"
    )
    test_db.add(node)
    test_db.commit()
    
    # 1. Add a FAILED backup history entry 30 minutes ago
    fail_time = datetime(2026, 6, 15, 2, 0)
    history = models.BackupHistory(
        node_id=node.id,
        archive_name="node-01-fail-archive",
        timestamp=fail_time,
        original_size=1000,
        deduplicated_size=500,
        status="FAILED",
        log_output="Some error log"
    )
    test_db.add(history)
    test_db.commit()
    
    # Run scheduler 30 minutes after failure (should NOT trigger)
    now_time = datetime(2026, 6, 15, 2, 30)
    check_and_trigger_backups(test_db, now=now_time)
    mock_run_backup_task.delay.assert_not_called()
    
    # Run scheduler 61 minutes after failure (should trigger retry)
    now_time_retry = datetime(2026, 6, 15, 3, 1)
    check_and_trigger_backups(test_db, now=now_time_retry)
    mock_run_backup_task.delay.assert_called_once()


@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_a_stale_lock_does_not_count_against_the_group_limit(mock_run_backup_task, mock_redis, test_db):
    """A dead worker must not throttle its whole group until the TTL expires.

    `backup_running:` outlives the worker that set it. The lock TTL is sized
    from the node's own transfer history, so on a slow link it is hours — and
    the group's concurrency count was reading the bare key, so one crashed
    backup silently held a concurrency_limit=1 group idle for that long with
    nothing in the logs to explain it. Admission control already resolved this
    through is_backup_lock_live; the count did not.
    """
    group = models.BackupGroup(
        name="NightlyGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=1,
        randomize_days=True,
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)

    dead = models.Node(hostname="node-dead", ip_address="192.168.1.10",
                       group_id=group.id, backup_paused=False, status="READY")
    waiting = models.Node(hostname="node-02", ip_address="192.168.1.11",
                          group_id=group.id, backup_paused=False, status="READY")
    test_db.add_all([dead, waiting])
    test_db.commit()
    test_db.refresh(dead)
    test_db.refresh(waiting)

    # The dead node holds a lock naming a Celery task that has already finished.
    def lock_for(key):
        return b"1700000000:finished-task-id" if str(dead.id) in key else None

    mock_redis.get.side_effect = lock_for
    mock_redis.mget.side_effect = lambda keys: [lock_for(k) for k in keys]

    node_hash = deterministic_hash(waiting.hostname)
    day_index = node_hash % 7
    hour_offset = node_hash % 3
    scheduled_hour = (2 + hour_offset) % 24
    scheduled_minute = (node_hash // 3) % 60
    target_time = (datetime(2026, 6, 15) + timedelta(days=day_index)).replace(
        hour=scheduled_hour, minute=scheduled_minute
    )

    class _FinishedResult:
        def ready(self):
            return True

    with patch('celery_app.celery_app.AsyncResult', return_value=_FinishedResult()), \
         patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)

    # The group is no longer frozen. Which node goes first is the scheduler's
    # own slot ordering and not what this test is about — the dead node's own
    # backup failed, so it being retried first is legitimate.
    assert mock_run_backup_task.delay.called, (
        "a finished task's leftover lock still counted against the group limit"
    )
    # And the corpse is cleared rather than left to expire on its own.
    mock_redis.delete.assert_any_call(f"backup_running:{dead.id}")


@patch('core.scheduler.redis_client')
@patch('core.scheduler.run_backup_task')
def test_a_live_lock_still_counts_against_the_group_limit(mock_run_backup_task, mock_redis, test_db):
    """The other half: the fix must not make the limit stop working."""
    group = models.BackupGroup(
        name="NightlyGroup", interval="weekly", start_time="02:00", end_time="05:00",
        concurrency_limit=1, randomize_days=True,
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)

    running = models.Node(hostname="node-01", ip_address="192.168.1.10",
                          group_id=group.id, backup_paused=False, status="READY")
    waiting = models.Node(hostname="node-02", ip_address="192.168.1.11",
                          group_id=group.id, backup_paused=False, status="READY")
    test_db.add_all([running, waiting])
    test_db.commit()
    test_db.refresh(running)
    test_db.refresh(waiting)

    def lock_for(key):
        return b"1700000000:still-running-task" if str(running.id) in key else None

    mock_redis.get.side_effect = lock_for
    mock_redis.mget.side_effect = lambda keys: [lock_for(k) for k in keys]

    node_hash = deterministic_hash(waiting.hostname)
    day_index = node_hash % 7
    hour_offset = node_hash % 3
    target_time = (datetime(2026, 6, 15) + timedelta(days=day_index)).replace(
        hour=(2 + hour_offset) % 24, minute=(node_hash // 3) % 60
    )

    class _RunningResult:
        def ready(self):
            return False

    with patch('celery_app.celery_app.AsyncResult', return_value=_RunningResult()), \
         patch('core.scheduler.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = target_time
        check_and_trigger_backups(test_db)

    mock_run_backup_task.delay.assert_not_called()
