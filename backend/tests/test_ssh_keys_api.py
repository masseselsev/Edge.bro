import pytest
from fastapi import HTTPException

import models
from core import ssh_keys
from routers import ssh_keys as ssh_keys_router
from tests.test_ssh_audit import KEY_A, KEY_B, db  # noqa: F401  (fixture reuse)


class _User:
    username = "alice"


@pytest.fixture(autouse=True)
def _no_orchestrator_key(monkeypatch):
    """Keep the real /root/.ssh out of these tests."""
    from core import ssh_audit
    monkeypatch.setattr(ssh_audit, "orchestrator_fingerprint", lambda: None)


def _finding(db, classification, fingerprint="SHA256:a"):
    finding = models.SshKeyFinding(
        location="ORCHESTRATOR", host="__orchestrator__",
        fingerprint=fingerprint, classification=classification,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


def test_purge_refuses_a_matched_finding(db):
    finding = _finding(db, "OURS_MATCHED")
    with pytest.raises(HTTPException) as exc:
        ssh_keys_router.purge_finding(finding.id, confirm=True, request=None,
                                      db=db, current_user=_User())
    assert exc.value.status_code == 400


@pytest.mark.parametrize("classification", ["OURS_LEGACY", "UNKNOWN"])
def test_purge_requires_confirmation_for_unprovable_entries(db, classification):
    finding = _finding(db, classification)
    with pytest.raises(HTTPException) as exc:
        ssh_keys_router.purge_finding(finding.id, confirm=False, request=None,
                                      db=db, current_user=_User())
    assert exc.value.status_code == 400
    assert "confirm" in exc.value.detail.lower()


def test_purge_removes_the_entry_and_logs_the_actor(db, tmp_path, monkeypatch):
    path = tmp_path / "authorized_keys"
    path.write_text(f"{KEY_A} admin@laptop\n{KEY_B} edge-bro-node-9\n")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", str(path))

    finding = _finding(db, "UNKNOWN", fingerprint=ssh_keys.fingerprint(KEY_A))
    result = ssh_keys_router.purge_finding(finding.id, confirm=True, request=None,
                                           db=db, current_user=_User())

    assert result["status"] == "SUCCESS"
    remaining = ssh_keys.list_entries(str(path))
    assert len(remaining) == 1
    db.refresh(finding)
    assert finding.pruned_at is not None

    logs = db.query(models.AuditLog).filter(
        models.AuditLog.action == "SSH Key Manual Purge"
    ).all()
    assert len(logs) == 1
    assert logs[0].username == "alice"


def test_list_audit_filters_by_classification(db):
    _finding(db, "UNKNOWN", fingerprint="SHA256:u")
    _finding(db, "OURS_MATCHED", fingerprint="SHA256:m")
    rows = ssh_keys_router.list_audit(classification="UNKNOWN", location=None,
                                      include_resolved=False, db=db, current_user=_User())
    assert [r.fingerprint for r in rows] == ["SHA256:u"]
