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


@patch("builtins.open")
@patch("os.path.exists")
@patch("os.path.realpath")
def test_get_host_root_disk(mock_realpath, mock_exists, mock_open):
    from core.disk_ops import get_host_root_disk

    # Case 1: UUID format
    mock_exists.side_effect = lambda path: path == "/proc/cmdline" or "disk/by-uuid" in path
    mock_file = MagicMock()
    mock_file.read.return_value = 'BOOT_IMAGE=/vmlinuz root=UUID="3b27907f-2b21-4b45-b25b-8febdbc3d2a8" ro quiet'
    mock_open.return_value.__enter__.return_value = mock_file
    mock_realpath.return_value = "/dev/sda1"

    res = get_host_root_disk()
    assert res == "/dev/sda"

    # Case 2: PARTUUID format
    mock_exists.side_effect = lambda path: path == "/proc/cmdline" or "disk/by-partuuid" in path
    mock_file.read.return_value = 'BOOT_IMAGE=/vmlinuz root=PARTUUID="092598fe-634e-49e2-bad4-a2d0ba8fa929" ro'
    mock_realpath.return_value = "/dev/nvme0n1p2"

    res = get_host_root_disk()
    assert res == "/dev/nvme0n1"

    # Case 3: Raw dev path format
    mock_exists.side_effect = lambda path: path == "/proc/cmdline" or path == "/dev/sda3"
    mock_file.read.return_value = 'BOOT_IMAGE=/vmlinuz root=/dev/sda3 ro'
    mock_realpath.return_value = "/dev/sda3"

    res = get_host_root_disk()
    assert res == "/dev/sda"


@patch("builtins.open")
@patch("os.path.exists")
@patch("subprocess.run")
def test_safe_unmount_target(mock_run, mock_exists, mock_open):
    import subprocess
    from core.disk_ops import safe_unmount_target

    mock_exists.side_effect = lambda path: path == "/proc/mounts" or "/mnt/target" in path
    mock_file = MagicMock()
    mock_file.readlines.return_value = [
        "/dev/sdb3 /mnt/target ext4 rw 0 0\n",
        "/dev/sdb1 /mnt/target/boot/efi vfat rw 0 0\n",
        "/dev/sdb2 /mnt/target/boot ext2 rw 0 0\n"
    ]
    mock_file.__iter__.return_value = mock_file.readlines.return_value
    mock_open.return_value.__enter__.return_value = mock_file

    safe_unmount_target("/mnt/target")

    # Verify that virtual paths are unmounted first lazily (-l)
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/dev/pts"], stderr=subprocess.DEVNULL)
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/dev"], stderr=subprocess.DEVNULL)
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/proc"], stderr=subprocess.DEVNULL)
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/sys"], stderr=subprocess.DEVNULL)

    # Verify partitions under /mnt/target are unmounted in reverse order
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/boot/efi"], stderr=subprocess.DEVNULL)
    mock_run.assert_any_call(["umount", "-l", "/mnt/target/boot"], stderr=subprocess.DEVNULL)

    # Verify root /mnt/target is unmounted
    mock_run.assert_any_call(["umount", "-l", "/mnt/target"], stderr=subprocess.DEVNULL)


@patch("builtins.open")
@patch("os.path.exists")
@patch("subprocess.Popen")
@patch("subprocess.check_call")
@patch("subprocess.check_output")
@patch("os.makedirs")
@patch("shutil.rmtree")
@patch("os.path.realpath")
def test_format_and_restore_borg_progress_parsing(
    mock_realpath, mock_rmtree, mock_makedirs, mock_check_output, mock_check_call, mock_popen, mock_exists, mock_open
):
    from core.disk_ops import format_and_restore

    # Mock all host & target checks
    mock_exists.side_effect = lambda path: path in ["/dev/sdb", "/proc/cmdline"] or "disk/by-uuid" in path
    mock_check_output.return_value = "/dev/sda1"
    mock_realpath.return_value = "/dev/sda"
    
    # Mock /proc/cmdline
    mock_file = MagicMock()
    mock_file.read.return_value = 'root=UUID="3b27907f-2b21-4b45-b25b-8febdbc3d2a8"'
    mock_open.return_value.__enter__.return_value = mock_file

    # Mock Popen process
    mock_proc = MagicMock()
    # Simulate stderr returning progressive file count outputs separated by \r
    stderr_content = "1,000 files\r2,000 files\r2,500 files\r"
    import io
    mock_proc.stderr = io.StringIO(stderr_content)
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    log_callback = MagicMock()

    format_and_restore(
        target_dev="/dev/sdb",
        partitions=[
            {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": "", "size_bytes": 512 * 1024 * 1024},
            {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 0}
        ],
        efi_uuid="1234-5678",
        archive_name="test.tar",
        repo_path="/repo",
        keep_network_configs=True,
        wipe_mac_bindings=False,
        network_iface="eth0",
        total_files=5000,
        log_callback=log_callback
    )

    # 1000 files / 5000 total = 20% of 45 = 9% progress. So 45 + 9 = 54% progress logged.
    log_callback.assert_any_call("Extracting files (1000/5000)...", 54, None)
    # 2000 files / 5000 total = 40% of 45 = 18%. So 45 + 18 = 63% progress logged.
    log_callback.assert_any_call("Extracting files (2000/5000)...", 63, None)


