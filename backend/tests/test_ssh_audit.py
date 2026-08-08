import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_ssh_audit.db"


@pytest.fixture
def db():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_ssh_audit.db"):
            os.remove("./test_ssh_audit.db")


def test_finding_uniqueness_is_per_location_host_and_fingerprint(db):
    from sqlalchemy.exc import IntegrityError

    common = dict(
        location="ORCHESTRATOR", host="__orchestrator__",
        fingerprint="SHA256:abc", classification="OURS_ORPHANED",
    )
    db.add(models.SshKeyFinding(**common))
    db.commit()
    db.add(models.SshKeyFinding(**common))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orchestrator_host_is_not_nullable(db):
    """NULL hosts would defeat the unique constraint, so the sentinel is used."""
    from core import ssh_audit
    assert ssh_audit.ORCHESTRATOR_HOST == "__orchestrator__"

    finding = models.SshKeyFinding(
        location="ORCHESTRATOR", host=ssh_audit.ORCHESTRATOR_HOST,
        fingerprint="SHA256:xyz", classification="UNKNOWN",
    )
    db.add(finding)
    db.commit()
    assert finding.first_seen is not None
    assert finding.orphan_scan_count == 0
    assert finding.pruned_at is None


def test_node_carries_a_cached_authorized_keys_inventory(db):
    node = models.Node(
        hostname="n1", ip_address="10.0.0.1", ssh_port=22,
        node_authorized_keys=["ssh-ed25519 AAAA edge-bro-orchestrator"],
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    assert node.node_authorized_keys == ["ssh-ed25519 AAAA edge-bro-orchestrator"]
