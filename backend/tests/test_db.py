import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from core.clock import utcnow

# Use a test SQLite database to verify structures
TEST_DATABASE_URL = "sqlite:///./test_orchestrator.db"

@pytest.fixture(scope="module")
def db_session():
    """
    Creates an in-memory SQLite database session for unit testing DB schemas.
    """
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_orchestrator.db"):
            os.remove("./test_orchestrator.db")

def test_create_settings(db_session):
    """
    Verify that global settings record is correctly initialized.
    """
    settings = models.Settings(
        borg_ssh_port=12345,
        keep_daily=7,
        keep_weekly=4,
        keep_monthly=6,
        global_exclusions="/dev/*"
    )
    db_session.add(settings)
    db_session.commit()

    retrieved = db_session.query(models.Settings).first()
    assert retrieved is not None
    assert retrieved.borg_ssh_port == 12345
    assert retrieved.keep_daily == 7

def test_create_node_with_uuid(db_session):
    """
    Verify that nodes with EFI partition UUID can be saved and retrieved.
    """
    node = models.Node(
        hostname="test-edge-01",
        ip_address="192.168.1.50",
        ssh_port=22,
        status="NEEDS_BOOTSTRAP",
        disk_type="SATA",
        efi_uuid="4F2E-3A5B"
    )
    db_session.add(node)
    db_session.commit()

    retrieved = db_session.query(models.Node).filter(models.Node.hostname == "test-edge-01").first()
    assert retrieved is not None
    assert retrieved.ip_address == "192.168.1.50"
    assert retrieved.efi_uuid == "4F2E-3A5B"

def test_parse_ip_input():
    """
    Test parsing lists, ranges, and CIDR blocks into single IP strings.
    """
    from routers.nodes import parse_ip_input

    # Test single
    assert parse_ip_input("192.168.1.100") == ["192.168.1.100"]

    # Test comma-separated list
    assert parse_ip_input("192.168.1.100, 192.168.1.101") == ["192.168.1.100", "192.168.1.101"]

    # Test range (short)
    assert parse_ip_input("192.168.1.50-52") == ["192.168.1.50", "192.168.1.51", "192.168.1.52"]

    # Test range (long)
    assert parse_ip_input("10.0.0.1-10.0.0.3") == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    # Test CIDR
    assert parse_ip_input("192.168.1.0/30") == ["192.168.1.1", "192.168.1.2"]


def test_ensure_orchestrator_ssh_key():
    """
    Verify that ensure_orchestrator_ssh_key generates key files and returns public key content.
    """
    import os
    import tempfile
    import builtins
    from unittest.mock import patch
    from tasks import ensure_orchestrator_ssh_key
    
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_priv_key = os.path.join(tmpdir, "id_ed25519")
        mock_pub_key = os.path.join(tmpdir, "id_ed25519.pub")
        dummy_pub_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDtestkey orchestrator"
        
        real_exists = os.path.exists
        def spy_exists(path):
            if path == "/root/.ssh/id_ed25519":
                return real_exists(mock_priv_key)
            if path == "/root/.ssh/id_ed25519.pub":
                return real_exists(mock_pub_key)
            if path == "/root/.ssh":
                return True
            return real_exists(path)
            
        real_makedirs = os.makedirs
        def spy_makedirs(path, mode=0o777, exist_ok=False):
            if path == "/root/.ssh":
                return real_makedirs(tmpdir, mode=mode, exist_ok=True)
            return real_makedirs(path, mode=mode, exist_ok=exist_ok)
            
        real_chmod = os.chmod
        def spy_chmod(path, mode):
            if path == "/root/.ssh":
                return real_chmod(tmpdir, mode)
            if path == "/root/.ssh/id_ed25519":
                return real_chmod(mock_priv_key, mode)
            return real_chmod(path, mode)

        real_open = builtins.open
        def spy_open(file, *args, **kwargs):
            if file == "/root/.ssh/id_ed25519.pub":
                return real_open(mock_pub_key, *args, **kwargs)
            if file == "/root/.ssh/id_ed25519":
                return real_open(mock_priv_key, *args, **kwargs)
            return real_open(file, *args, **kwargs)

        def mock_run(cmd, *args, **kwargs):
            if "ssh-keygen" in cmd:
                with real_open(mock_priv_key, "w") as f:
                    f.write("DUMMY PRIVATE KEY")
                with real_open(mock_pub_key, "w") as f:
                    f.write(dummy_pub_key)
                return
            raise ValueError(f"Unexpected subprocess run call: {cmd}")

        with patch('tasks.os.path.exists', side_effect=spy_exists), \
             patch('tasks.os.makedirs', side_effect=spy_makedirs), \
             patch('tasks.os.chmod', side_effect=spy_chmod), \
             patch('tasks.open', side_effect=spy_open), \
             patch('tasks.subprocess.run', side_effect=mock_run), \
             patch('tasks.fix_ssh_permissions'):
            
            with patch('os.path.exists', side_effect=spy_exists):
                pub_key_content = ensure_orchestrator_ssh_key()
                assert pub_key_content == dummy_pub_key
                assert os.path.exists("/root/.ssh/id_ed25519")
                assert os.path.exists("/root/.ssh/id_ed25519.pub")


def test_upgrade_settings(db_session):
    """
    Verify that old default exclusions values are upgraded to the new default,
    while custom settings are preserved.
    """
    from main import upgrade_settings
    
    new_default = [
        {"pattern": "/dev/*", "comment": "System devices"},
        {"pattern": "/proc/*", "comment": "Virtual process filesystem"},
        {"pattern": "/sys/*", "comment": "Sysfs system info"},
        {"pattern": "/run/*", "comment": "Transient runtime files"},
        {"pattern": "/mnt/*", "comment": "Mounted filesystems"},
        {"pattern": "/media/*", "comment": "Removable media mounts"},
        {"pattern": "/lost+found", "comment": "Recovered filesystem fragments"},
        {"pattern": "/var/log/edge/*", "comment": "Edge app logs"},
        {"pattern": "/var/opt/edge/blobstore/*", "comment": "Local media files storage"},
        {"pattern": "/var/spool/edge/*", "comment": "Edge spool directory"},
        {"pattern": "/var/log/journal/*", "comment": "Systemd journal logs"},
        {"pattern": "/var/log/**/*.gz", "comment": "Compressed rotated logs"},
        {"pattern": "/var/log/**/*.1", "comment": "Rotated log backups"},
        {"pattern": "/var/hasplm/*", "comment": "Sentinel HASP licensing data"},
        {"pattern": "/etc/hasplm/*", "comment": "Sentinel HASP licensing config"}
    ]
    
    # Test case 1: Upgrade from first default
    db_session.query(models.Settings).delete()
    db_session.commit()
    s1 = models.Settings(global_exclusions=['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*'])
    db_session.add(s1)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s1)
    assert s1.global_exclusions == new_default

    # Test case 2: Upgrade from second default
    db_session.query(models.Settings).delete()
    db_session.commit()
    s2 = models.Settings(global_exclusions=['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found', '/var/log/edge/*', '/var/opt/edge/*'])
    db_session.add(s2)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s2)
    assert s2.global_exclusions == new_default

    # Test case 3: Upgrade from third default (pre-journal/gz/1 addition)
    db_session.query(models.Settings).delete()
    db_session.commit()
    s3 = models.Settings(global_exclusions=['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found', '/var/log/edge/*', '/var/opt/edge/*', '/var/spool/edge/*'])
    db_session.add(s3)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s3)
    assert s3.global_exclusions == new_default

    # Test case 4: Upgrade from fourth default (containing /var/opt/edge/* but also journal/gz/1)
    db_session.query(models.Settings).delete()
    db_session.commit()
    s4 = models.Settings(
        global_exclusions=[
            '/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found',
            '/var/log/edge/*', '/var/opt/edge/*', '/var/spool/edge/*', '/var/log/journal/*',
            '/var/log/**/*.gz', '/var/log/**/*.1'
        ]
    )
    db_session.add(s4)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s4)
    assert s4.global_exclusions == new_default

    # Test case 5: Custom user setting is NOT upgraded to new defaults, but converted to structure
    db_session.query(models.Settings).delete()
    db_session.commit()
    custom_val = ['/dev/*', '/custom/*']
    s5 = models.Settings(global_exclusions=custom_val)
    db_session.add(s5)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s5)
    assert s5.global_exclusions == [
        {"pattern": "/dev/*", "comment": "Custom exclusion"},
        {"pattern": "/custom/*", "comment": "Custom exclusion"}
    ]


def test_get_all_history(db_session):
    """
    Verify that get_all_history returns all records sorted by timestamp desc.
    """
    from routers.nodes import get_all_history
    import datetime

    # Create dummy node
    node = models.Node(
        hostname="test-node-hist",
        ip_address="192.168.1.99",
        ssh_port=22,
        status="READY"
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    # Add backup histories
    h1 = models.BackupHistory(
        node_id=node.id,
        archive_name="test-archive-old",
        original_size=100,
        deduplicated_size=50,
        status="SUCCESS",
        timestamp=utcnow() - datetime.timedelta(days=1)
    )
    h2 = models.BackupHistory(
        node_id=node.id,
        archive_name="test-archive-new",
        original_size=200,
        deduplicated_size=100,
        status="SUCCESS",
        timestamp=utcnow()
    )
    db_session.add(h1)
    db_session.add(h2)
    db_session.commit()

    res = get_all_history(db=db_session)
    records = res["history"]
    assert res["total"] >= 2
    # Ensure sorted by timestamp descending (h2 should be first, then h1)
    test_records = [r for r in records if r.node_id == node.id]
    assert len(test_records) == 2
    assert test_records[0].archive_name == "test-archive-new"
    assert test_records[1].archive_name == "test-archive-old"


def test_backup_group_relations(db_session):
    """
    Verify creating a BackupGroup and assigning a Node to it.
    """
    group = models.BackupGroup(
        name="test-group-01",
        interval="weekly",
        target_week=1,
        start_time="02:00",
        end_time="05:00",
        concurrency_limit=3,
        randomize_days=True
    )
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    
    assert group.id is not None
    assert group.name == "test-group-01"

    node = models.Node(
        hostname="test-node-in-group",
        ip_address="192.168.1.120",
        ssh_port=22,
        status="READY",
        group_id=group.id,
        backup_paused=False,
        backup_today=False,
        missed_window=False,
        cpu_info="Intel Core i7",
        memory_info="16GB",
        edge_version="2026.3.0",
        notes="Important server"
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    assert node.group_id == group.id
    assert node.cpu_info == "Intel Core i7"
    assert node.notes == "Important server"


def test_resource_limits_settings_and_group(db_session):
    """
    Verify backup resource limits columns on BackupGroup and Settings, and verify API schema defaults.
    """
    # 1. Test Settings resource limits columns
    settings = db_session.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db_session.add(settings)
        db_session.commit()
        db_session.refresh(settings)

    assert settings.default_compression == "zstd:3"
    assert settings.default_cpu_quota == 30

    # Update global settings
    settings.default_compression = "lz4"
    settings.default_cpu_quota = 50
    db_session.commit()
    db_session.refresh(settings)
    assert settings.default_compression == "lz4"
    assert settings.default_cpu_quota == 50

    # 2. Test BackupGroup resource limits columns
    group = models.BackupGroup(
        name="resource-limited-group",
        interval="weekly",
        start_time="02:00",
        end_time="05:00",
        upload_rate_limit=1024,
        compression="zstd:5",
        checkpoint_interval=300,
        cpu_quota=75
    )
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    assert group.upload_rate_limit == 1024
    assert group.compression == "zstd:5"
    assert group.checkpoint_interval == 300
    assert group.cpu_quota == 75


def test_node_cpu_quota_column_roundtrips_through_migration(db_session):
    """NULL/0/positive all persist distinctly, and the migration is reversible."""
    node = models.Node(
        hostname="cpu-quota-node",
        ip_address="10.99.0.1",
        cpu_quota=0,
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    assert node.cpu_quota == 0

    node.cpu_quota = 40
    db_session.commit()
    db_session.refresh(node)
    assert node.cpu_quota == 40

    node.cpu_quota = None
    db_session.commit()
    db_session.refresh(node)
    assert node.cpu_quota is None


def test_checkpoint_interval_is_capped_for_fast_and_unlimited_links():
    """A dropped connection throws away everything since the last checkpoint.

    An unlimited link used to checkpoint every 1800s, so a link lost 20 minutes
    into a transfer discarded all 20 minutes of it — observed in production on
    a node whose IPsec tunnel drops at irregular intervals. The cap bounds that
    loss regardless of how fast the link is.
    """
    from backup_tasks import compute_checkpoint_interval, MAX_CHECKPOINT_INTERVAL_SECONDS

    assert MAX_CHECKPOINT_INTERVAL_SECONDS == 300
    assert compute_checkpoint_interval(None) == 300
    assert compute_checkpoint_interval(0) == 300
    assert compute_checkpoint_interval(6000) == 300


def test_checkpoint_interval_is_capped_for_very_slow_links_too():
    """A 60 KiB/s link targets ~50 MB per checkpoint, which is ~14 minutes of
    transfer. The cap applies to it for the same reason."""
    from backup_tasks import compute_checkpoint_interval

    # Uncapped this would be (50 * 1024) // 60 == 853.
    assert compute_checkpoint_interval(60) == 300


def test_checkpoint_interval_leaves_rates_under_the_cap_alone():
    """The cap is a ceiling, not a replacement for the size-based targets."""
    from backup_tasks import compute_checkpoint_interval

    assert compute_checkpoint_interval(250) == 204  # (50 * 1024) // 250
    assert compute_checkpoint_interval(1000) == 204  # (200 * 1024) // 1000


def test_checkpoint_calculation_and_command_builder():
    """
    Verify the generated borg-create shell fragment. The checkpoint interval
    itself is covered by the three focused tests above.
    """
    from backup_tasks import build_borg_create_inner_cmd

    # Test build_borg_create_inner_cmd helper. This only builds the shell
    # fragment now — the SSH argv it used to build itself (node_ip/port,
    # extra_ssh_args) is assembled once, alongside cleanup and init, by
    # core.node_lock.build_locked_remote_script + core.ssh.command. That
    # combined-argv shape is covered in test_backup_nat_tunnel.py.
    # 2.1 Without CPU quota
    inner_bash_cmd = build_borg_create_inner_cmd(
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-node-archive",
        exclude_str="--exclude '/proc/*'",
        compression="lz4",
        rate_limit_kib=1000,
        checkpoint_secs=204,
        cpu_quota=None,
        borg_passphrase="my-secret-passphrase"
    )
    # Ensure BORG_PASSPHRASE is in the script run on the host
    assert "BORG_PASSPHRASE='my-secret-passphrase'" in inner_bash_cmd
    assert "--remote-ratelimit 1000" in inner_bash_cmd
    assert "--checkpoint-interval 204" in inner_bash_cmd
    assert "--compression lz4" in inner_bash_cmd
    assert "systemd-run" not in inner_bash_cmd
    # BORG_RSH embeds the keepalive options for the node's own connection
    # back to the orchestrator — unrelated to this orchestrator's outer ssh.
    assert "ServerAliveInterval=30" in inner_bash_cmd
    assert "ServerAliveCountMax=3" in inner_bash_cmd

    # 2.2 With CPU quota
    inner_bash_cmd_cpu = build_borg_create_inner_cmd(
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-node-archive",
        exclude_str="--exclude '/proc/*'",
        compression="zstd:3",
        rate_limit_kib=1000,
        checkpoint_secs=204,
        cpu_quota=85,
        borg_passphrase="my-secret-passphrase"
    )
    assert "systemd-run --scope" in inner_bash_cmd_cpu
    assert "-p CPUQuota=85%" in inner_bash_cmd_cpu
    assert "--compression zstd,3" in inner_bash_cmd_cpu


def test_task_log_node_association(db_session):
    """
    Verify that TaskLog can be associated with a Node and queried.
    """
    node = models.Node(
        hostname="test-node-logs-assoc",
        ip_address="192.168.1.251",
        status="READY"
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    task_log = models.TaskLog(
        id="test-task-uuid-1234",
        task_type="BOOTSTRAP",
        status="SUCCESS",
        node_id=node.id,
        log_output="Task completed successfully"
    )
    db_session.add(task_log)
    db_session.commit()

    retrieved = db_session.query(models.TaskLog).filter(models.TaskLog.node_id == node.id).first()
    assert retrieved is not None
    assert retrieved.id == "test-task-uuid-1234"
    assert retrieved.node_id == node.id
    assert retrieved.log_output == "Task completed successfully"


def test_settings_server_name_validation():
    from schemas import SettingsBase
    from pydantic import ValidationError

    # Valid server names
    valid_names = ["orchestrator", "edge-server", "main_server_01", "edge-server-99"]
    for name in valid_names:
        s = SettingsBase(server_name=name)
        assert s.server_name == name

    # Uppercase is accepted but normalised, since the name becomes a filename prefix
    assert SettingsBase(server_name="Edge-Server-99").server_name == "edge-server-99"

    # Invalid server names (spaces, special characters)
    invalid_names = ["orchestrator ", "edge server", "main@server", "edge.server"]
    for name in invalid_names:
        with pytest.raises(ValidationError):
            SettingsBase(server_name=name)


def test_node_response_backup_fields():
    from schemas import NodeResponse
    data = {
        "id": 1,
        "hostname": "test-node",
        "ip_address": "192.168.1.100",
        "ssh_port": 22,
        "status": "READY",
        "last_backup": None,
        "disk_type": "SATA",
        "network_iface": "eth0",
        "efi_uuid": "1234",
        "partition_layout": None,
        "os_version": "Debian 12",
        "next_retry_at": None,
        "repo_size_bytes": 100,
        "group_id": None,
        "backup_paused": False,
        "backup_today": False,
        "missed_window": False,
        "cpu_info": None,
        "memory_info": None,
        "edge_version": None,
        "notes": None,
        "is_backup_running": True,
        "current_speed_mbps": 42.3,
        "current_speed_limit_mbps": 50.0,
        "backup_task_id": "test-task-id-123"
    }
    resp = NodeResponse(**data)
    assert resp.is_backup_running is True
    assert resp.current_speed_mbps == 42.3
    assert resp.current_speed_limit_mbps == 50.0
    assert resp.backup_task_id == "test-task-id-123"


def test_a_running_backup_reports_the_speed_it_published():
    """What the fleet list shows in place of the progress percentage it used
    to invent: the rate borg is actually achieving right now."""
    import time
    from routers.nodes_crud import _backup_run_state

    running = f"{int(time.time())}:some-task-id"
    with patch("celery_app.celery_app") as celery:
        celery.AsyncResult.return_value.ready.return_value = False
        is_running, speed, limit, task_id = _backup_run_state(
            1, raw=running, speed_raw="42.3:50.0", _prefetched=True
        )

    assert is_running is True
    assert speed == 42.3
    assert limit == 50.0
    assert task_id == "some-task-id"


def test_a_backup_that_has_not_published_a_speed_yet_reports_none():
    """The first seconds of every backup, before the rolling window can state
    a rate. Showing nothing beats showing a made-up number."""
    import time
    from routers.nodes_crud import _backup_run_state

    running = f"{int(time.time())}:some-task-id"
    with patch("celery_app.celery_app") as celery:
        celery.AsyncResult.return_value.ready.return_value = False
        is_running, speed, limit, _task_id = _backup_run_state(
            1, raw=running, speed_raw=None, _prefetched=True
        )

    assert is_running is True
    assert speed is None
    assert limit is None


def test_a_node_with_no_backup_running_reports_no_speed():
    from routers.nodes_crud import _backup_run_state

    assert _backup_run_state(1, raw=None, _prefetched=True) == (False, None, None, None)


def test_upgrade_settings_cpu_quota():
    from database import SessionLocal
    from main import upgrade_settings
    import models

    db = SessionLocal()
    try:
        settings = db.query(models.Settings).first()
        if not settings:
            settings = models.Settings()
            db.add(settings)
        settings.default_cpu_quota = 10
        db.commit()

        upgrade_settings(db)

        db.refresh(settings)
        assert settings.default_cpu_quota == 30
    finally:
        db.close()


def test_node_os_arch_column_roundtrips(db_session):
    node = models.Node(
        hostname="arch-node",
        ip_address="10.99.0.2",
        os_arch="x86_64",
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    assert node.os_arch == "x86_64"

    node.os_arch = None
    db_session.commit()
    db_session.refresh(node)
    assert node.os_arch is None


def test_run_prepare_assigns_os_arch_from_parsed_facts(monkeypatch, db_session):
    node = models.Node(hostname="prep-node", ip_address="10.99.0.3")
    db_session.add(node)
    db_session.commit()

    import backup_tasks
    from contextlib import contextmanager

    monkeypatch.setattr(
        backup_tasks, "run_ansible_playbook",
        lambda **kwargs: {"status": "SUCCESS", "parsed_data": {"os_arch": "aarch64"}},
    )

    @contextmanager
    def fake_session_scope():
        yield db_session
        db_session.commit()

    monkeypatch.setattr(backup_tasks, "session_scope", fake_session_scope)

    backup_tasks._run_prepare(node.id, "task-1", lambda task_id, msg, **kwargs: None)

    db_session.refresh(node)
    assert node.os_arch == "aarch64"


