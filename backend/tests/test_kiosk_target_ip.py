import os
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from datetime import datetime

from database import Base, get_db
import models
import schemas
from main import app
from iso_tasks import repack_kiosk_iso_task

TEST_DATABASE_URL = "sqlite:///./test_kiosk_target_ip_db.db"

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
        if os.path.exists("./test_kiosk_target_ip_db.db"):
            os.remove("./test_kiosk_target_ip_db.db")

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

def test_update_kiosk_ip_endpoint(client, db_session):
    # 1. Create a kiosk
    kiosk = models.Kiosk(
        name="IP Test Kiosk",
        kiosk_id="KS7777",
        key="7777KS",
        status="APPROVED",
        auth_token="TEST77",
        target_ip=None,
        rebuild_required=False
    )
    db_session.add(kiosk)
    db_session.commit()
    db_session.refresh(kiosk)
    
    # 2. Call the update_ip endpoint
    resp = client.post(
        f"/api/iso/kiosks/{kiosk.id}/update_ip",
        json={"target_ip": "192.168.1.150"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "Kiosk target IP updated" in data["message"]
    
    # 3. Verify in DB
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db.target_ip == "192.168.1.150"
    assert kiosk_db.rebuild_required is True

@patch("iso_tasks.repack_kiosk_iso_task.delay")
def test_recreate_kiosk_iso_endpoint(mock_repack_task, client, db_session):
    mock_repack_task.return_value = MagicMock(id="recreate-task-456")
    
    # Create kiosk
    kiosk = models.Kiosk(
        name="Recreate Test Kiosk",
        kiosk_id="KS6666",
        key="6666KS",
        status="APPROVED",
        auth_token="TEST66"
    )
    db_session.add(kiosk)
    db_session.commit()
    db_session.refresh(kiosk)
    
    # Call endpoint
    resp = client.post(f"/api/iso/kiosks/{kiosk.id}/recreate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "recreate-task-456"
    assert "Recreation task started" in data["message"]
    mock_repack_task.assert_called_once_with(kiosk.id)

def test_repack_kiosk_iso_task_uses_target_ip(db_session):
    from unittest.mock import mock_open
    
    # Create a kiosk with a target IP
    kiosk = models.Kiosk(
        name="Target IP Repack Kiosk",
        kiosk_id="KS5555",
        key="5555KS",
        status="APPROVED",
        auth_token="TEST55",
        target_ip="10.0.0.99"
    )
    db_session.add(kiosk)
    db_session.commit()
    db_session.refresh(kiosk)
    kiosk_id = kiosk.id
    
    mock_request = MagicMock()
    mock_request.id = "repack-task-uuid"
    
    # Mock open and check if JSON write contains orchestrator_ip set to 10.0.0.99
    written_data = []
    
    def fake_open(file, mode="r", *args, **kwargs):
        if "config.json" in file and "w" in mode:
            f = MagicMock()
            def write_side_effect(s):
                written_data.append(s)
            f.write.side_effect = write_side_effect
            # Also mock context manager protocol
            f.__enter__.return_value = f
            return f
        # Fallback to standard mock_open behavior or empty json for read
        m = mock_open(read_data='{"orchestrator_ip": "127.0.0.1"}')()
        return m
        
    with patch("database.SessionLocal") as mock_session, \
         patch("core.task_log.run_command_with_logging") as mock_run, \
         patch("tasks.log_to_task") as mock_log, \
         patch("subprocess.run") as mock_sub, \
         patch("os.path.exists") as mock_exists, \
         patch("os.listdir") as mock_listdir, \
         patch("os.makedirs") as mock_makedirs, \
         patch("builtins.open", new=fake_open):
         
        mock_session.return_value = db_session
        mock_exists.side_effect = lambda path: True
        mock_listdir.return_value = []
        
        repack_kiosk_iso_task.request_stack.push(mock_request)
        try:
            repack_kiosk_iso_task.run(kiosk_id)
        finally:
            repack_kiosk_iso_task.request_stack.pop()
            
    # Verify that the correct target_ip was written to config.json
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    assert kiosk_db.rebuild_required is False
    assert kiosk_db.iso_built_at is not None
    
    # Check what was written. It should contain target_ip "10.0.0.99"
    import json
    try:
        cfg = json.loads("".join(written_data))
        assert cfg.get("orchestrator_ip") == "10.0.0.99", "orchestrator_ip should be set to kiosk.target_ip"
    except Exception as parse_err:
        pytest.fail(f"Failed to parse written config.json or orchestrator_ip mismatch: {parse_err}. Data: {written_data}")
