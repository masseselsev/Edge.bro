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


@patch("builtins.open")
@patch("os.path.exists")
@patch("subprocess.call")
@patch("subprocess.check_call")
@patch("subprocess.check_output")
def test_format_and_restore_unmounts_busy_partitions(mock_check_output, mock_check_call, mock_call, mock_exists, mock_open):
    from core.disk_ops import format_and_restore

    # Mock filesystem checks
    mock_exists.side_effect = lambda path: path == "/proc/mounts" or path == "/dev/sdb"
    
    # Mock /proc/mounts content
    mock_file = MagicMock()
    mock_file.readlines.return_value = [
        "/dev/sdb1 /media/usb ext4 rw 0 0\n",
        "/dev/sdbb1 /media/other ext4 rw 0 0\n", # should not match /dev/sdb
        "/dev/sda1 / ext4 rw 0 0\n"
    ]
    # File iteration yields line-by-line
    mock_file.__iter__.return_value = mock_file.readlines.return_value
    mock_open.return_value.__enter__.return_value = mock_file

    # Mock host root disk detection
    mock_check_output.return_value = "/dev/sda2"

    # Call format_and_restore (it will fail later during parting/formatting, which is fine, we just want to test initial unmount calls)
    try:
        format_and_restore(
            target_dev="/dev/sdb",
            partitions=[],
            efi_uuid="1234-5678",
            archive_name="test.tar",
            repo_path="/repo",
            keep_network_configs=True,
            wipe_mac_bindings=False,
            network_iface="eth0",
            total_files=0,
            log_callback=MagicMock()
        )
    except Exception:
        pass

    # Verify that umount -l was called for /dev/sdb1's mountpoint (/media/usb)
    # and NOT for /dev/sdbb1's mountpoint (/media/other)
    mock_call.assert_any_call(["umount", "-l", "/media/usb"])
    
    # Check that umount was not called for /media/other
    for call_args in mock_call.call_args_list:
        assert "/media/other" not in call_args[0]
