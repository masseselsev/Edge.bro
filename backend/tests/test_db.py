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
    from main import parse_ip_input

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
        '/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,'
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

    # Test case 4: Custom user setting is NOT upgraded
    db_session.query(models.Settings).delete()
    db_session.commit()
    custom_val = '/dev/*,/custom/*'
    s4 = models.Settings(global_exclusions=custom_val)
    db_session.add(s4)
    db_session.commit()
    upgrade_settings(db_session)
    db_session.refresh(s4)
    assert s4.global_exclusions == custom_val



