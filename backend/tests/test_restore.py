import os
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from database import Base
import models
import schemas
from routers.restore import trigger_restore

TEST_DATABASE_URL = "sqlite:///./test_restore_db.db"

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
        if os.path.exists("./test_restore_db.db"):
            os.remove("./test_restore_db.db")

@patch("routers.restore.flash_restore_device")
def test_trigger_restore_mismatch(mock_flash, db_session):
    # Create test node
    node = models.Node(
        hostname="testnode",
        ip_address="127.0.0.1",
        disk_type="SATA 232.9G Samsung SSD 870 EVO 250GB",
        efi_uuid="0AF5-2CA3"
    )
    db_session.add(node)
    db_session.commit()

    class DummyUser:
        username = "admin"

    # Case 1: Target dev is NVME (mismatch) without override
    payload = schemas.RestoreRequest(
        node_id=node.id,
        archive_name="test-archive.tar.gz",
        target_dev="/dev/nvme0n1",
        override_mismatch=False,
        keep_network_configs=True,
        wipe_mac_bindings=False
    )
    
    with pytest.raises(HTTPException) as exc_info:
        trigger_restore(payload, request=MagicMock(), db=db_session, current_user=DummyUser())
    assert exc_info.value.status_code == 400
    assert "DISK TYPE MISMATCH WARNING" in exc_info.value.detail

    # Case 2: Target dev is NVME (mismatch) WITH override (should proceed)
    payload.override_mismatch = True
    mock_task = MagicMock()
    mock_task.id = "test-task-id"
    mock_flash.delay.return_value = mock_task

    res = trigger_restore(payload, request=MagicMock(), db=db_session, current_user=DummyUser())
    assert res["task_id"] == "test-task-id"

    # Case 3: Target dev is SATA (no mismatch, since node has "SATA 232.9G...")
    payload.target_dev = "/dev/sdb"
    payload.override_mismatch = False
    res = trigger_restore(payload, request=MagicMock(), db=db_session, current_user=DummyUser())
    assert res["task_id"] == "test-task-id"
