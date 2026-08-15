import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from core.clock import utcnow

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


from datetime import datetime, timedelta


def _orphan(fingerprint, *, scans=2, age_hours=13, classification="OURS_ORPHANED"):
    return models.SshKeyFinding(
        location="ORCHESTRATOR", host="__orchestrator__",
        fingerprint=fingerprint, classification=classification,
        orphan_since=utcnow() - timedelta(hours=age_hours),
        orphan_scan_count=scans,
    )


def _matched(fingerprint):
    return models.SshKeyFinding(
        location="ORCHESTRATOR", host="__orchestrator__",
        fingerprint=fingerprint, classification="OURS_MATCHED",
    )


def test_first_sighting_is_never_pruned():
    from core import ssh_audit
    decision = ssh_audit.select_prune_candidates([_orphan("SHA256:a", scans=1)])
    assert decision.candidates == []


def test_second_sighting_within_twelve_hours_is_not_pruned():
    from core import ssh_audit
    decision = ssh_audit.select_prune_candidates([_orphan("SHA256:a", scans=2, age_hours=3)])
    assert decision.candidates == []


def test_second_sighting_after_twelve_hours_is_pruned():
    from core import ssh_audit
    decision = ssh_audit.select_prune_candidates([_orphan("SHA256:a")])
    assert [c.fingerprint for c in decision.candidates] == ["SHA256:a"]
    assert not decision.aborted


@pytest.mark.parametrize("classification", ["OURS_LEGACY", "UNKNOWN", "OURS_MATCHED"])
def test_only_tagged_orphans_are_ever_candidates(classification):
    """The guarantee the whole design rests on."""
    from core import ssh_audit
    decision = ssh_audit.select_prune_candidates(
        [_orphan("SHA256:a", classification=classification)]
    )
    assert decision.candidates == []


def test_absolute_cap_aborts_the_whole_run():
    from core import ssh_audit
    findings = [_orphan(f"SHA256:{i}") for i in range(6)]
    decision = ssh_audit.select_prune_candidates(findings)
    assert decision.aborted
    assert decision.candidates == []
    assert "6" in decision.abort_reason


def test_five_candidates_are_allowed():
    from core import ssh_audit
    findings = [_orphan(f"SHA256:{i}") for i in range(5)]
    decision = ssh_audit.select_prune_candidates(findings)
    assert not decision.aborted
    assert len(decision.candidates) == 5


def test_fraction_cap_ignored_on_small_files():
    """Three of four is 75%, but four entries is too few for a fraction to
    mean anything; the absolute cap governs. Without this, a small install
    could never clean up at all."""
    from core import ssh_audit
    findings = [_orphan(f"SHA256:{i}") for i in range(3)] + [_matched("SHA256:m")]
    decision = ssh_audit.select_prune_candidates(findings)
    assert not decision.aborted
    assert len(decision.candidates) == 3


def test_fraction_cap_aborts_once_the_file_is_large_enough():
    from core import ssh_audit
    findings = [_orphan(f"SHA256:{i}") for i in range(4)] + [
        _matched(f"SHA256:m{i}") for i in range(6)
    ]
    # 4 of 10 tagged entries = 40% > 25%, and 10 >= the fraction floor.
    decision = ssh_audit.select_prune_candidates(findings)
    assert decision.aborted
    assert "%" in decision.abort_reason


def test_three_of_forty_proceeds():
    from core import ssh_audit
    findings = [_orphan(f"SHA256:{i}") for i in range(3)] + [
        _matched(f"SHA256:m{i}") for i in range(37)
    ]
    decision = ssh_audit.select_prune_candidates(findings)
    assert not decision.aborted
    assert len(decision.candidates) == 3


def test_total_database_loss_leaves_the_file_untouched(db, tmp_path, monkeypatch):
    """Every tagged entry orphans at once. The cap must catch it."""
    from core import ssh_audit

    lines = [
        f"{ssh_keys.BORG_SERVE_OPTIONS} {key} {ssh_keys.node_tag(i)}"
        for i, key in enumerate([KEY_A, KEY_B, KEY_C], start=1)
    ]
    path = _write_auth(tmp_path, *lines)
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)
    monkeypatch.setattr(ssh_audit, "PRUNE_MAX_ABSOLUTE", 2)
    before = open(path).read()

    ssh_audit.scan_orchestrator(db)
    for finding in db.query(models.SshKeyFinding):
        finding.orphan_since = utcnow() - timedelta(hours=13)
    db.commit()
    findings = ssh_audit.scan_orchestrator(db)

    decision = ssh_audit.select_prune_candidates(findings)
    pruned = ssh_audit.prune(db, path, decision)

    assert pruned == []
    assert open(path).read() == before


def test_prune_removes_the_entry_and_records_it(db, tmp_path, monkeypatch):
    from core import ssh_audit

    path = _write_auth(
        tmp_path,
        f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A} {ssh_keys.node_tag(1)}",
        f"{KEY_B} admin@laptop",
    )
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    ssh_audit.scan_orchestrator(db)
    for finding in db.query(models.SshKeyFinding):
        finding.orphan_since = utcnow() - timedelta(hours=13)
    db.commit()
    findings = ssh_audit.scan_orchestrator(db)

    decision = ssh_audit.select_prune_candidates(findings)
    pruned = ssh_audit.prune(db, path, decision)

    assert len(pruned) == 1
    assert pruned[0].pruned_at is not None
    remaining = ssh_keys.list_entries(path)
    assert len(remaining) == 1
    assert remaining[0].comment == "admin@laptop"


def test_prune_writes_a_backup(db, tmp_path, monkeypatch):
    from core import ssh_audit

    path = _write_auth(tmp_path, f"{KEY_A} {ssh_keys.node_tag(1)}")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    ssh_audit.scan_orchestrator(db)
    for finding in db.query(models.SshKeyFinding):
        finding.orphan_since = utcnow() - timedelta(hours=13)
    db.commit()
    findings = ssh_audit.scan_orchestrator(db)
    ssh_audit.prune(db, path, ssh_audit.select_prune_candidates(findings))

    backups = [n for n in os.listdir(tmp_path) if n.startswith("authorized_keys.bak.")]
    assert backups


def test_node_cache_classifies_stale_orchestrator_keys(db, monkeypatch):
    from core import ssh_audit

    node = models.Node(
        hostname="n1", ip_address="10.0.0.1", ssh_port=22,
        node_authorized_keys=[
            f"{KEY_A} edge-bro-orchestrator",   # current
            f"{KEY_B} edge-bro-orchestrator",   # rotation leftover
            f"{KEY_C} admin@laptop",            # foreign
        ],
    )
    db.add(node)
    db.commit()

    monkeypatch.setattr(
        ssh_audit, "orchestrator_fingerprint", lambda: ssh_keys.fingerprint(KEY_A)
    )
    findings = ssh_audit.scan_nodes_from_cache(db)

    by_fp = {f.fingerprint: f for f in findings}
    assert by_fp[ssh_keys.fingerprint(KEY_A)].classification == "OURS_MATCHED"
    assert by_fp[ssh_keys.fingerprint(KEY_B)].classification == "OURS_ORPHANED"
    assert by_fp[ssh_keys.fingerprint(KEY_C)].classification == "UNKNOWN"
    assert all(f.location == "NODE" and f.host == "n1" for f in findings)


def test_node_without_a_cached_inventory_is_skipped(db, monkeypatch):
    from core import ssh_audit

    db.add(models.Node(hostname="n2", ip_address="10.0.0.2", ssh_port=22))
    db.commit()
    monkeypatch.setattr(
        ssh_audit, "orchestrator_fingerprint", lambda: ssh_keys.fingerprint(KEY_A)
    )
    assert ssh_audit.scan_nodes_from_cache(db) == []


def test_audit_task_records_an_audit_log_row_for_each_prune(db, tmp_path, monkeypatch):
    """An unattended deletion must still be attributable in the audit log."""
    from tasks import ssh_audit as ssh_audit_task

    path = _write_auth(tmp_path, f"{ssh_keys.BORG_SERVE_OPTIONS} {KEY_A} {ssh_keys.node_tag(1)}")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)

    ssh_audit_task.run_audit(db, include_nodes=False)
    for finding in db.query(models.SshKeyFinding):
        finding.orphan_since = utcnow() - timedelta(hours=13)
    db.commit()

    summary = ssh_audit_task.run_audit(db, include_nodes=False)

    assert summary["pruned"] == 1
    entries = db.query(models.AuditLog).filter(
        models.AuditLog.action == "SSH Key Auto-Prune"
    ).all()
    assert len(entries) == 1
    assert entries[0].username == "system"
    assert ssh_keys.fingerprint(KEY_A) in entries[0].details


def test_audit_task_logs_an_abort(db, tmp_path, monkeypatch):
    from core import ssh_audit
    from tasks import ssh_audit as ssh_audit_task

    lines = [
        f"{ssh_keys.BORG_SERVE_OPTIONS} {k} {ssh_keys.node_tag(i)}"
        for i, k in enumerate([KEY_A, KEY_B, KEY_C], start=1)
    ]
    path = _write_auth(tmp_path, *lines)
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)
    monkeypatch.setattr(ssh_audit, "PRUNE_MAX_ABSOLUTE", 2)

    ssh_audit_task.run_audit(db, include_nodes=False)
    for finding in db.query(models.SshKeyFinding):
        finding.orphan_since = utcnow() - timedelta(hours=13)
    db.commit()
    summary = ssh_audit_task.run_audit(db, include_nodes=False)

    assert summary["aborted"] is True
    assert summary["pruned"] == 0
    aborts = db.query(models.AuditLog).filter(
        models.AuditLog.action == "SSH Key Prune Aborted"
    ).all()
    assert len(aborts) == 1
