import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from datetime import datetime, timedelta

from database import Base
import models
from main import app
from database import get_db

TEST_DATABASE_URL = "sqlite:///./test_stats_db.db"

@pytest.fixture(scope="module")
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
        if os.path.exists("./test_stats_db.db"):
            os.remove("./test_stats_db.db")

@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    
    from routers.users import require_admin
    def override_require_admin():
        return models.User(username="test_admin", is_superadmin=True)
    app.dependency_overrides[require_admin] = override_require_admin
    
    with TestClient(app) as c:
        yield c
        
    app.dependency_overrides.clear()

def test_stats_initial_backups_filtering(client, db_session):
    # Setup test nodes
    node1 = models.Node(hostname="node-1", ip_address="192.168.1.101")
    node2 = models.Node(hostname="node-2", ip_address="192.168.1.102")
    db_session.add_all([node1, node2])
    db_session.commit()
    
    # Setup backup history records
    # Node 1 has two successful backups:
    # 1. Oldest (initial): original = 10000, deduplicated = 2000
    # 2. Newer (incremental): original = 10000, deduplicated = 100
    # Node 2 has one successful backup:
    # 1. Oldest (initial): original = 5000, deduplicated = 1000
    # Also a failed backup of Node 2 which should be ignored: original = 5000, deduplicated = 5000 (status = FAILED)
    
    now = datetime.utcnow()
    
    h1_1 = models.BackupHistory(
        node_id=node1.id,
        archive_name="node1-archive-1",
        timestamp=now - timedelta(days=2),
        original_size=10000,
        deduplicated_size=2000,
        status="SUCCESS"
    )
    h1_2 = models.BackupHistory(
        node_id=node1.id,
        archive_name="node1-archive-2",
        timestamp=now - timedelta(days=1),
        original_size=10000,
        deduplicated_size=100,
        status="SUCCESS"
    )
    h2_1 = models.BackupHistory(
        node_id=node2.id,
        archive_name="node2-archive-1",
        timestamp=now - timedelta(days=3),
        original_size=5000,
        deduplicated_size=1000,
        status="SUCCESS"
    )
    h2_failed = models.BackupHistory(
        node_id=node2.id,
        archive_name="node2-archive-failed",
        timestamp=now - timedelta(days=4),
        original_size=5000,
        deduplicated_size=5000,
        status="FAILED"
    )
    
    db_session.add_all([h1_1, h1_2, h2_1, h2_failed])
    db_session.commit()
    
    # Retrieve stats
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    
    # Calculated based ONLY on:
    # - Node 1's oldest: h1_1 (original: 10000, deduplicated: 2000)
    # - Node 2's oldest: h2_1 (original: 5000, deduplicated: 1000)
    # Total original: 10000 + 5000 = 15000
    # Total deduplicated: 2000 + 1000 = 3000
    # Deduplication ratio: 15000 / 3000 = 5.0
    
    assert data["total_original_size_bytes"] == 15000
    assert data["total_deduplicated_size_bytes"] == 3000
    assert data["deduplication_ratio"] == 5.0
