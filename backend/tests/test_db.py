import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models

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
        borg_repo_path="/data/borg",
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
    from tasks import ensure_orchestrator_ssh_key
    
    pub_key_content = ensure_orchestrator_ssh_key()
    assert isinstance(pub_key_content, str)
    assert pub_key_content.startswith("ssh-ed25519")
    
    assert os.path.exists("/root/.ssh/id_ed25519")
    assert os.path.exists("/root/.ssh/id_ed25519.pub")


def test_upgrade_settings(db_session):
    """
    Verify that old default settings values are upgraded to the new default,
    while custom settings are preserved.
    """
    from main import upgrade_settings
    
    new_default = (
        '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,'
        '/var/log/edge/*,/var/opt/edge/blobstore/*,/var/spool/edge/*,/var/log/journal/*,'
        '/var/log/**/*.gz,/var/log/**/*.1'
    )
    
    # Test case 1: Upgrade from first default
    db_session.query(models.Settings).delete()
    db_session.commit()
    s1 = models.Settings(global_exclusions='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*')
    db_session.add(s1)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s1)
    assert s1.global_exclusions == new_default

    # Test case 2: Upgrade from second default
    db_session.query(models.Settings).delete()
    db_session.commit()
    s2 = models.Settings(global_exclusions='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*')
    db_session.add(s2)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s2)
    assert s2.global_exclusions == new_default

    # Test case 3: Upgrade from third default (pre-journal/gz/1 addition)
    db_session.query(models.Settings).delete()
    db_session.commit()
    s3 = models.Settings(global_exclusions='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*')
    db_session.add(s3)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s3)
    assert s3.global_exclusions == new_default

    # Test case 4: Upgrade from fourth default (containing /var/opt/edge/* but also journal/gz/1)
    db_session.query(models.Settings).delete()
    db_session.commit()
    s4 = models.Settings(
        global_exclusions=(
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,'
            '/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,'
            '/var/log/**/*.gz,/var/log/**/*.1'
        )
    )
    db_session.add(s4)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s4)
    assert s4.global_exclusions == new_default

    # Test case 5: Custom user setting is NOT upgraded
    db_session.query(models.Settings).delete()
    db_session.commit()
    custom_val = '/dev/*,/custom/*'
    s5 = models.Settings(global_exclusions=custom_val)
    db_session.add(s5)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s5)
    assert s5.global_exclusions == custom_val


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
        timestamp=datetime.datetime.utcnow() - datetime.timedelta(days=1)
    )
    h2 = models.BackupHistory(
        node_id=node.id,
        archive_name="test-archive-new",
        original_size=200,
        deduplicated_size=100,
        status="SUCCESS",
        timestamp=datetime.datetime.utcnow()
    )
    db_session.add(h1)
    db_session.add(h2)
    db_session.commit()

    records = get_all_history(db=db_session)
    assert len(records) >= 2
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
    assert settings.default_cpu_quota is None

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


def test_checkpoint_calculation_and_command_builder():
    """
    Verify auto-calculation of Borg checkpoint interval and the generated systemd-run SSH CLI command.
    """
    from backup_tasks import compute_checkpoint_interval, build_borg_create_cmd

    # 1. Test compute_checkpoint_interval helper
    assert compute_checkpoint_interval(None) == 1800
    assert compute_checkpoint_interval(0) == 1800
    # Slow rate (<= 500 KiB/s)
    assert compute_checkpoint_interval(250) == 204  # (50 * 1024) // 250
    # Medium rate (<= 5000 KiB/s)
    assert compute_checkpoint_interval(1000) == 204  # (200 * 1024) // 1000
    # Fast rate (> 5000 KiB/s)
    assert compute_checkpoint_interval(6000) == 1800

    # 2. Test build_borg_create_cmd helper
    # 2.1 Without CPU quota
    cmd_no_cpu = build_borg_create_cmd(
        node_ip="192.168.1.5",
        node_ssh_port=22,
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-node-archive",
        exclude_str="--exclude '/proc/*'",
        compression="lz4",
        rate_limit_kib=1000,
        checkpoint_secs=204,
        cpu_quota=None,
        borg_passphrase="my-secret-passphrase"
    )
    # Ensure correct format and options
    assert cmd_no_cpu[0] == "ssh"
    assert cmd_no_cpu[1] == "-o"
    assert cmd_no_cpu[7] == "-p"
    assert cmd_no_cpu[9] == "-i"

    # Assert Keepalive options in ssh command list
    assert "-o" in cmd_no_cpu
    assert "ServerAliveInterval=30" in cmd_no_cpu
    assert "ServerAliveCountMax=3" in cmd_no_cpu

    # Ensure BORG_PASSPHRASE is in the script run on the host
    inner_bash_cmd = cmd_no_cpu[-1]
    assert "BORG_PASSPHRASE='my-secret-passphrase'" in inner_bash_cmd
    assert "--remote-ratelimit 1000" in inner_bash_cmd
    assert "--checkpoint-interval 204" in inner_bash_cmd
    assert "--compression lz4" in inner_bash_cmd
    assert "systemd-run" not in inner_bash_cmd
    assert "ServerAliveInterval=30" in inner_bash_cmd
    assert "ServerAliveCountMax=3" in inner_bash_cmd

    # 2.2 With CPU quota
    cmd_with_cpu = build_borg_create_cmd(
        node_ip="192.168.1.5",
        node_ssh_port=22,
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-node-archive",
        exclude_str="--exclude '/proc/*'",
        compression="lz4",
        rate_limit_kib=1000,
        checkpoint_secs=204,
        cpu_quota=85,
        borg_passphrase="my-secret-passphrase"
    )
    inner_bash_cmd_cpu = cmd_with_cpu[-1]
    assert "systemd-run --scope" in inner_bash_cmd_cpu
    assert "-p CPUQuota=85%" in inner_bash_cmd_cpu
    assert "-p IOSchedulingClass=idle" in inner_bash_cmd_cpu


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
