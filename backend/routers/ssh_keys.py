import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import models
import schemas
from core import ssh_audit, ssh_keys
from database import get_db, log_user_action
from auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ssh-keys", tags=["SSH Keys"])

#: Classifications a human may delete by hand. OURS_MATCHED is excluded because
#: the key is still in use by a live record.
MANUALLY_PURGEABLE = {
    ssh_keys.Classification.OURS_LEGACY.value,
    ssh_keys.Classification.UNKNOWN.value,
    ssh_keys.Classification.OURS_ORPHANED.value,
}
#: These cannot be proven to be ours, so a human must confirm explicitly.
REQUIRES_CONFIRMATION = {
    ssh_keys.Classification.OURS_LEGACY.value,
    ssh_keys.Classification.UNKNOWN.value,
}


@router.get("/audit", response_model=List[schemas.SshKeyFindingResponse])
def list_audit(
    classification: Optional[str] = None,
    location: Optional[str] = None,
    include_resolved: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    query = db.query(models.SshKeyFinding)
    if classification:
        query = query.filter(models.SshKeyFinding.classification == classification)
    if location:
        query = query.filter(models.SshKeyFinding.location == location)
    if not include_resolved:
        query = query.filter(models.SshKeyFinding.resolved_at.is_(None))
    return query.order_by(models.SshKeyFinding.last_seen.desc()).all()


@router.post("/audit/run")
def run_audit_now(
    include_nodes: bool = False,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    from tasks.ssh_audit import run_audit

    summary = run_audit(db, include_nodes=include_nodes)
    log_user_action(
        db, getattr(current_user, "username", "unknown"), "SSH Key Audit Run",
        f"Scanned {summary['scanned']} entries, pruned {summary['pruned']}", request,
    )
    return summary


@router.post("/findings/{finding_id}/purge")
def purge_finding(
    finding_id: int,
    confirm: bool = False,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    finding = (
        db.query(models.SshKeyFinding)
        .filter(models.SshKeyFinding.id == finding_id)
        .first()
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")

    if finding.classification not in MANUALLY_PURGEABLE:
        raise HTTPException(
            status_code=400,
            detail="This key is still in use by a live record and cannot be purged.",
        )
    if finding.classification in REQUIRES_CONFIRMATION and not confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "This key cannot be proven to belong to edge-bro. "
                "Re-send with confirm=true to remove it."
            ),
        )
    if finding.location != "ORCHESTRATOR":
        raise HTTPException(
            status_code=400,
            detail="Only orchestrator-side entries can be purged from here.",
        )

    action = ssh_keys.revoke(ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS, finding.fingerprint)
    now = datetime.utcnow()
    finding.pruned_at = now
    finding.resolved_at = now
    db.commit()

    log_user_action(
        db, getattr(current_user, "username", "unknown"), "SSH Key Manual Purge",
        f"Removed {finding.classification} key {finding.fingerprint} "
        f"(comment: {finding.comment}) from {finding.host}", request,
    )
    logger.info(
        "Manual purge of %s by %s: %s",
        finding.fingerprint, getattr(current_user, "username", "unknown"), action.value,
    )
    return {"status": "SUCCESS", "action": action.value}


@router.get("/orchestrator")
def orchestrator_key(
    db: Session = Depends(get_db), current_user=Depends(require_admin)
):
    return {"fingerprint": ssh_audit.orchestrator_fingerprint()}
