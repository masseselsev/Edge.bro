import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

from database import Base, log_user_action
import models

TEST_DATABASE_URL = "sqlite:///./test_audit_logs_db.db"

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
        if os.path.exists("./test_audit_logs_db.db"):
            os.remove("./test_audit_logs_db.db")

def test_log_user_action(db_session):
    # 1. Log an action without request
    log_user_action(db_session, "test_admin", "Test Action", "Details about test action")
    
    # 2. Query from database
    logs = db_session.query(models.AuditLog).all()
    assert len(logs) == 1
    assert logs[0].username == "test_admin"
    assert logs[0].action == "Test Action"
    assert logs[0].details == "Details about test action"
    assert logs[0].ip_address is None

    # 3. Log an action with request containing IP
    mock_request = MagicMock()
    mock_request.client.host = "192.168.1.55"
    log_user_action(db_session, "test_admin_2", "Another Action", "More details", mock_request)

    db_session.expire_all()
    logs = db_session.query(models.AuditLog).order_by(models.AuditLog.id.desc()).all()
    assert len(logs) == 2
    assert logs[0].username == "test_admin_2"
    assert logs[0].action == "Another Action"
    assert logs[0].details == "More details"
    assert logs[0].ip_address == "192.168.1.55"
