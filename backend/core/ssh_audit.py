"""Audit of authorized_keys files against the live database."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import models
from core import ssh_keys

logger = logging.getLogger(__name__)

#: Stands in for the orchestrator in SshKeyFinding.host, which is NOT NULL so
#: that the (location, host, fingerprint) unique constraint behaves.
ORCHESTRATOR_HOST = "__orchestrator__"

#: An orphan must be seen this many times, spanning at least PRUNE_MIN_AGE,
#: before it is removed. A transient database failure cannot satisfy both.
PRUNE_MIN_SCANS = 2
PRUNE_MIN_AGE = timedelta(hours=12)

#: Blast-radius caps. Mass orphaning is the signature of a broken database
#: rather than real drift, so a run that looks like one is abandoned whole.
PRUNE_MAX_ABSOLUTE = 5
PRUNE_MAX_FRACTION = 0.25
#: The fraction cap only applies once a file has enough tagged entries for a
#: fraction to be meaningful. Below this the absolute cap governs; otherwise a
#: small installation could never clean up a single legitimate orphan.
PRUNE_FRACTION_FLOOR = 8


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


@dataclass
class PruneDecision:
    candidates: list = field(default_factory=list)
    aborted: bool = False
    abort_reason: Optional[str] = None
    tagged_total: int = 0


def select_prune_candidates(findings) -> PruneDecision:
    """Choose which findings may be deleted, or abandon the run.

    Only OURS_ORPHANED is ever eligible: the tag proves edge-bro wrote the
    entry. Everything else is reported and left alone.

    Callers must pass the findings for one file, since the fraction cap is
    computed against the total handed in.
    """
    now = datetime.utcnow()
    tagged_total = sum(
        1 for f in findings
        if f.classification in (
            ssh_keys.Classification.OURS_MATCHED.value,
            ssh_keys.Classification.OURS_ORPHANED.value,
        )
    )

    eligible = []
    for finding in findings:
        if finding.classification != ssh_keys.Classification.OURS_ORPHANED.value:
            continue
        if (finding.orphan_scan_count or 0) < PRUNE_MIN_SCANS:
            continue
        if finding.orphan_since is None or now - finding.orphan_since < PRUNE_MIN_AGE:
            continue
        eligible.append(finding)

    decision = PruneDecision(tagged_total=tagged_total)

    if len(eligible) > PRUNE_MAX_ABSOLUTE:
        decision.aborted = True
        decision.abort_reason = (
            f"{len(eligible)} entries eligible, above the absolute cap of "
            f"{PRUNE_MAX_ABSOLUTE}; refusing to prune"
        )
        decision.candidates = []
        return decision

    if tagged_total >= PRUNE_FRACTION_FLOOR and eligible:
        fraction = len(eligible) / tagged_total
        if fraction > PRUNE_MAX_FRACTION:
            decision.aborted = True
            decision.abort_reason = (
                f"{len(eligible)} of {tagged_total} tagged entries "
                f"({fraction:.0%}) eligible, above the {PRUNE_MAX_FRACTION:.0%} "
                f"cap; refusing to prune"
            )
            decision.candidates = []
            return decision

    decision.candidates = eligible
    return decision


def prune(db, path: str, decision: PruneDecision) -> list:
    """Delete the chosen entries. Returns the findings actually removed."""
    if decision.aborted:
        logger.warning(
            "SSH audit prune aborted for %s: %s", path, decision.abort_reason
        )
        return []

    now = datetime.utcnow()
    pruned = []
    for finding in decision.candidates:
        action = ssh_keys.revoke(path, finding.fingerprint)
        if action is ssh_keys.Action.REMOVED:
            age = now - finding.orphan_since if finding.orphan_since else None
            finding.pruned_at = now
            finding.resolved_at = now
            pruned.append(finding)
            logger.info(
                "SSH audit pruned %s from %s (tag=%s, orphaned for %s)",
                finding.fingerprint, path, finding.comment, age,
            )
        else:
            logger.warning(
                "SSH audit could not remove %s from %s: %s",
                finding.fingerprint, path, action.value,
            )
    db.commit()
    return pruned
