import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from tasks import ping_all_nodes_task

TEST_DATABASE_URL = "sqlite:///./test_ping_db.db"

@pytest.fixture(scope="function")
def session_factory():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    Base.metadata.create_all(bind=engine)
    yield TestingSessionLocal
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("./test_ping_db.db"):
        try:
            os.remove("./test_ping_db.db")
        except Exception:
            pass

@patch('tasks.SessionLocal')
@patch('tasks.async_ping_ip', new_callable=AsyncMock)
def test_ping_all_nodes_task(mock_async_ping, mock_session, session_factory):
    mock_session.side_effect = session_factory
    
    db = session_factory()
    # Create test nodes
    n1 = models.Node(hostname="node-1", ip_address="192.168.1.5", status="READY")
    n2 = models.Node(hostname="node-2", ip_address="192.168.1.6", status="READY")
    db.add_all([n1, n2])
    db.commit()
    
    # Mock ping status: n1 online, n2 offline
    def mock_ping_side_effect(ip):
        if ip == "192.168.1.5":
            return True
        return False
        
    mock_async_ping.side_effect = mock_ping_side_effect
    
    res = ping_all_nodes_task()
    assert res["status"] == "SUCCESS"
    
    # Verify DB updates
    db_refresh = session_factory()
    node1 = db_refresh.query(models.Node).filter_by(hostname="node-1").first()
    node2 = db_refresh.query(models.Node).filter_by(hostname="node-2").first()
    
    assert node1.last_ping_status is True
    assert node1.last_available_at is not None
    assert node2.last_ping_status is False
