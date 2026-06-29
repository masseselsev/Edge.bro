import os
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
from database import Base
from backup_tasks import global_daily_prune

TEST_DATABASE_URL = "sqlite:///./test_pruning_db.db"

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

@patch('backup_tasks.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_prune_legacy_fallback(mock_exists, mock_run, mock_session, session_factory):
    mock_exists.return_value = True
    mock_session.side_effect = session_factory
    
    db = session_factory()
    # Setup settings
    settings = models.Settings(
        keep_daily=5,
        keep_weekly=3,
        keep_monthly=2,
        retention_policy=None
    )
    db.add(settings)
    
    # Setup node
    node = models.Node(
        hostname="node-legacy",
        ip_address="192.168.1.10",
        group_id=None
    )
    db.add(node)
    db.commit()
    
    print("Nodes in DB before run:", [n.hostname for n in db.query(models.Node).all()])
    db.close()
    
    mock_run.return_value = MagicMock(returncode=0)
    
    res = global_daily_prune()
    print("Subprocess run calls:", [call[0][0] for call in mock_run.call_args_list])
    
    # Check that subprocess.run was called for prune (1), compact (1) and permission fixes (3)
    assert mock_run.call_count == 5
    
    # First call: prune
    prune_args = mock_run.call_args_list[0][0][0]
    assert prune_args[0] == "borg"
    assert prune_args[1] == "prune"
    assert "--prefix" in prune_args
    assert "node-legacy-" in prune_args
    assert "--keep-daily" in prune_args
    assert "5" in prune_args
    assert "--keep-weekly" in prune_args
    assert "3" in prune_args
    assert "--keep-monthly" in prune_args
    assert "2" in prune_args
    
    # Second call: compact
    compact_args = mock_run.call_args_list[1][0][0]
    assert compact_args == ["borg", "compact", "/data/borg/fleet/node-legacy"]

@patch('backup_tasks.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_prune_global_custom_strategies(mock_exists, mock_run, mock_session, session_factory):
    mock_exists.return_value = True
    mock_session.side_effect = session_factory
    
    db = session_factory()
    # 1. Test global policy of type "count"
    settings = models.Settings(
        keep_daily=7,
        keep_weekly=4,
        keep_monthly=6,
        retention_policy={
            "type": "count",
            "keep_last": 10
        }
    )
    db.add(settings)
    
    node = models.Node(
        hostname="node-count",
        ip_address="192.168.1.11",
        group_id=None
    )
    db.add(node)
    db.commit()
    db.close()
    
    mock_run.return_value = MagicMock(returncode=0)
    
    global_daily_prune()
    
    prune_args = mock_run.call_args_list[0][0][0]
    print("Prune args first run:", prune_args)
    assert "--keep-last" in prune_args
    assert "10" in prune_args
    assert "node-count-" in prune_args
    
    # Reset mock and test "timeframe"
    mock_run.reset_mock()
    
    db = session_factory()
    settings = db.query(models.Settings).first()
    print("Settings policy before update:", settings.retention_policy)
    # Re-assign standard JSON dict so SQLAlchemy detects the modification
    settings.retention_policy = {
        "type": "timeframe",
        "within_value": 6,
        "within_unit": "w"
    }
    db.commit()
    print("Settings policy after update:", settings.retention_policy)
    db.close()
    
    global_daily_prune()
    
    prune_args = mock_run.call_args_list[0][0][0]
    print("Prune args second run:", prune_args)
    # Timeframe strategy translates to --keep-last 1 --keep-within 6w
    assert "--keep-last" in prune_args
    assert "1" in prune_args
    assert "--keep-within" in prune_args
    assert "6w" in prune_args

@patch('backup_tasks.SessionLocal')
@patch('backup_tasks.subprocess.run')
@patch('backup_tasks.os.path.exists')
def test_prune_group_override(mock_exists, mock_run, mock_session, session_factory):
    mock_exists.return_value = True
    mock_session.side_effect = session_factory
    
    db = session_factory()
    # Global settings have one policy
    settings = models.Settings(
        retention_policy={
            "type": "count",
            "keep_last": 10
        }
    )
    test_db = db # renaming for convenience
    test_db.add(settings)
    
    # Group overrides and has count=2 policy
    group = models.BackupGroup(
        name="OverridingGroup",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        timezone="UTC",
        override_retention=True,
        retention_policy={
            "type": "count",
            "keep_last": 2
        }
    )
    test_db.add(group)
    test_db.commit()
    
    node = models.Node(
        hostname="node-override",
        ip_address="192.168.1.12",
        group_id=group.id
    )
    test_db.add(node)
    test_db.commit()
    test_db.close()
    
    mock_run.return_value = MagicMock(returncode=0)
    
    global_daily_prune()
    
    prune_args = mock_run.call_args_list[0][0][0]
    # Should use group's policy: keep_last = 2
    assert "--keep-last" in prune_args
    assert "2" in prune_args
    assert "node-override-" in prune_args
