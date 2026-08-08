"""Audit of authorized_keys files against the live database."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import models
from core import ssh_keys

logger = logging.getLogger(__name__)

#: Stands in for the orchestrator in SshKeyFinding.host, which is NOT NULL so
#: that the (location, host, fingerprint) unique constraint behaves.
ORCHESTRATOR_HOST = "__orchestrator__"


def orchestrator_fingerprint() -> Optional[str]:
    """Fingerprint of the orchestrator's own public key, or None if absent."""
    directory = ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS.rsplit("/", 1)[0]
    try:
        with open(f"{directory}/id_ed25519.pub") as handle:
            return ssh_keys.fingerprint(handle.read())
    except (OSError, ValueError):
        logger.warning("Orchestrator public key unavailable")
        return None


def known_fingerprints(db) -> set[str]:
    """Fingerprints that legitimately belong in the orchestrator's file.

    Disabled and revoked kiosks are deliberately excluded: their keys should
    already have been withdrawn, so leaving them out lets the audit finish a
    revocation that failed.
    """
    known: set[str] = set()

    for (pubkey,) in db.query(models.Node.ssh_pub_key).filter(
        models.Node.ssh_pub_key.isnot(None)
    ):
        try:
            known.add(ssh_keys.fingerprint(pubkey))
        except ValueError:
            logger.warning("Node has an unparseable stored SSH key; ignoring it")

    for (pubkey,) in db.query(models.Kiosk.ssh_pub_key).filter(
        models.Kiosk.ssh_pub_key.isnot(None), models.Kiosk.status == "APPROVED"
    ):
        try:
            known.add(ssh_keys.fingerprint(pubkey))
        except ValueError:
            logger.warning("Kiosk has an unparseable stored SSH key; ignoring it")

    # The orchestrator's self-grant, used by kiosks with the copied private key.
    own = orchestrator_fingerprint()
    if own:
        known.add(own)

    return known


def upsert_finding(
    db,
    *,
    location: str,
    host: str,
    node_id: Optional[int],
    entry: ssh_keys.AuthorizedKey,
    classification: ssh_keys.Classification,
    reason: str,
) -> models.SshKeyFinding:
    """Record what this scan saw, preserving first_seen and the orphan streak."""
    now = datetime.utcnow()
    finding = (
        db.query(models.SshKeyFinding)
        .filter(
            models.SshKeyFinding.location == location,
            models.SshKeyFinding.host == host,
            models.SshKeyFinding.fingerprint == entry.fingerprint,
        )
        .first()
    )
    if finding is None:
        finding = models.SshKeyFinding(
            location=location,
            host=host,
            fingerprint=entry.fingerprint,
            first_seen=now,
        )
        db.add(finding)

    finding.node_id = node_id
    finding.key_type = entry.keytype
    finding.comment = entry.comment
    finding.options = entry.options
    finding.classification = classification.value
    finding.reason = reason
    finding.last_seen = now
    finding.resolved_at = None

    if classification is ssh_keys.Classification.OURS_ORPHANED:
        if finding.orphan_since is None:
            finding.orphan_since = now
            finding.orphan_scan_count = 1
        else:
            finding.orphan_scan_count = (finding.orphan_scan_count or 0) + 1
    else:
        finding.orphan_since = None
        finding.orphan_scan_count = 0

    return finding


def _node_id_for(db, entry: ssh_keys.AuthorizedKey) -> Optional[int]:
    """Resolve the node a tagged entry refers to, if the tag names one."""
    tag = entry.tag
    prefix = f"{ssh_keys.TAG_PREFIX}-node-"
    if not tag or not tag.startswith(prefix):
        return None
    try:
        node_id = int(tag[len(prefix):])
    except ValueError:
        return None
    exists = db.query(models.Node.id).filter(models.Node.id == node_id).first()
    return node_id if exists else None


def _resolve_absent(db, location: str, host: str, seen: set[str]) -> None:
    """Mark rows for entries no longer present in the file."""
    now = datetime.utcnow()
    query = db.query(models.SshKeyFinding).filter(
        models.SshKeyFinding.location == location,
        models.SshKeyFinding.host == host,
        models.SshKeyFinding.resolved_at.is_(None),
    )
    for finding in query:
        if finding.fingerprint not in seen:
            finding.resolved_at = now
            finding.orphan_since = None
            finding.orphan_scan_count = 0


def scan_orchestrator(db) -> list[models.SshKeyFinding]:
    """Classify every entry in the orchestrator's authorized_keys."""
    path = ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS
    known = known_fingerprints(db)
    entries = ssh_keys.list_entries(path)

    seen_fingerprints = set()
    findings = []
    for entry in entries:
        classification, reason = ssh_keys.classify(entry, known)
        findings.append(
            upsert_finding(
                db,
                location="ORCHESTRATOR",
                host=ORCHESTRATOR_HOST,
                node_id=_node_id_for(db, entry),
                entry=entry,
                classification=classification,
                reason=reason,
            )
        )
        seen_fingerprints.add(entry.fingerprint)

    _resolve_absent(db, "ORCHESTRATOR", ORCHESTRATOR_HOST, seen_fingerprints)
    db.commit()

    logger.info(
        "SSH audit scanned %s: %d entries (%s)",
        path, len(entries),
        ", ".join(
            f"{c}={sum(1 for f in findings if f.classification == c)}"
            for c in sorted({f.classification for f in findings})
        ) or "empty",
    )
    return findings
