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


from core import ssh_keys

KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMgix3E0GojmJVKENYSNXib0XQw0PNVdj2ZrQIZxYpvk"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGgeESfoGvSePeUP3x9YBz4NDUwzPlIXi28cA1qRcBZM"
KEY_C = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDdIpHgAEoo0eoVX4OLeDH5jRtyp4lth3Ash/QoGR0in"


def _write_auth(tmp_path, *lines):
    path = tmp_path / "authorized_keys"
    path.write_text("".join(line + "\n" for line in lines))
    return str(path)


@pytest.fixture(autouse=True)
def _no_orchestrator_key(monkeypatch):
    """Keep the real /root/.ssh out of every test unless one opts in."""
    from core import ssh_audit
    monkeypatch.setattr(ssh_audit, "orchestrator_fingerprint", lambda: None)


def test_known_fingerprints_covers_nodes_and_approved_kiosks(db):
    from core import ssh_audit

    db.add(models.Node(hostname="n1", ip_address="10.0.0.1", ssh_port=22, ssh_pub_key=KEY_A))
    db.add(models.Kiosk(kiosk_id="k1", key="1111AA", status="APPROVED", ssh_pub_key=KEY_B))
    db.add(models.Kiosk(kiosk_id="k2", key="2222BB", status="DISABLED", ssh_pub_key=KEY_C))
    db.commit()

    known = ssh_audit.known_fingerprints(db)
    assert ssh_keys.fingerprint(KEY_A) in known
    assert ssh_keys.fingerprint(KEY_B) in known
    # A disabled kiosk should have had its key revoked; leaving it out makes the
    # audit a backstop for a revoke that failed.
    assert ssh_keys.fingerprint(KEY_C) not in known


def test_scan_orchestrator_classifies_and_persists(db, tmp_path, monkeypatch):
    from core import ssh_audit

    db.add(models.Node(hostname="n1", ip_address="10.0.0.1", ssh_port=22, ssh_pub_key=KEY_A))
    db.commit()
    node = db.query(models.Node).first()

    path = _write_auth(
        tmp_path,
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A} {ssh_keys.node_tag(node.id)}",
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_B} {ssh_keys.node_tag(999)}",
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_C}",
    )
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    findings = ssh_audit.scan_orchestrator(db)
    by_class = {f.classification: f for f in findings}
    assert by_class["OURS_MATCHED"].fingerprint == ssh_keys.fingerprint(KEY_A)
    assert by_class["OURS_ORPHANED"].fingerprint == ssh_keys.fingerprint(KEY_B)
    assert by_class["OURS_LEGACY"].fingerprint == ssh_keys.fingerprint(KEY_C)
    assert all(f.host == ssh_audit.ORCHESTRATOR_HOST for f in findings)


def test_rescanning_updates_rather_than_duplicates(db, tmp_path, monkeypatch):
    from core import ssh_audit

    path = _write_auth(tmp_path, f"{KEY_B} {ssh_keys.node_tag(999)}")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    ssh_audit.scan_orchestrator(db)
    first = db.query(models.SshKeyFinding).one()
    original_first_seen = first.first_seen

    ssh_audit.scan_orchestrator(db)
    assert db.query(models.SshKeyFinding).count() == 1
    refreshed = db.query(models.SshKeyFinding).one()
    assert refreshed.first_seen == original_first_seen
    assert refreshed.orphan_scan_count == 2


def test_entry_that_stops_being_orphaned_resets_its_streak(db, tmp_path, monkeypatch):
    from core import ssh_audit

    path = _write_auth(tmp_path, f"{KEY_A} {ssh_keys.node_tag(1)}")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    ssh_audit.scan_orchestrator(db)
    assert db.query(models.SshKeyFinding).one().orphan_scan_count == 1

    # The node reappears in the database, so the entry is legitimate again.
    db.add(models.Node(hostname="n1", ip_address="10.0.0.1", ssh_port=22, ssh_pub_key=KEY_A))
    db.commit()

    ssh_audit.scan_orchestrator(db)
    finding = db.query(models.SshKeyFinding).one()
    assert finding.classification == "OURS_MATCHED"
    assert finding.orphan_scan_count == 0
    assert finding.orphan_since is None


def test_disappeared_entry_is_marked_resolved(db, tmp_path, monkeypatch):
    from core import ssh_audit

    path = _write_auth(tmp_path, f"{KEY_B} {ssh_keys.node_tag(999)}")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)
    ssh_audit.scan_orchestrator(db)

    open(path, "w").close()
    ssh_audit.scan_orchestrator(db)

    finding = db.query(models.SshKeyFinding).one()
    assert finding.resolved_at is not None
