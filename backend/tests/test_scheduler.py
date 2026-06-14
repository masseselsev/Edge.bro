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
        backup_today=False
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
        backup_today=False
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
        backup_paused=False
    )
    node2 = models.Node(
        hostname="node-02",
        ip_address="192.168.1.11",
        group_id=group.id,
        backup_paused=False
    )
    test_db.add(node1)
    test_db.add(node2)
    test_db.commit()
    test_db.refresh(node1)
    test_db.refresh(node2)
    
    def redis_get(key):
        if str(node1.id) in key:
            return b"1"
        return None
    mock_redis.get.side_effect = redis_get
    
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
        backup_today=True
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
        missed_window=False
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
