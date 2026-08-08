"""Scheduled audit of authorized_keys files."""
from typing import Any, Dict

from celery_app import celery_app
from core import ssh_audit, ssh_keys
from database import SessionLocal, log_user_action


def run_audit(db, include_nodes: bool = False) -> Dict[str, Any]:
    """Scan, classify, then prune what the guardrails allow.

    Returns a summary suitable for both the API and the log line.
    """
    findings = ssh_audit.scan_orchestrator(db)
    decision = ssh_audit.select_prune_candidates(findings)
    pruned = ssh_audit.prune(db, ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS, decision)

    for finding in pruned:
        log_user_action(
            db, "system", "SSH Key Auto-Prune",
            f"Removed orphaned key {finding.fingerprint} "
            f"(tag {finding.comment}) from {finding.host}",
        )

    if decision.aborted:
        log_user_action(
            db, "system", "SSH Key Prune Aborted", decision.abort_reason,
        )

    node_findings = ssh_audit.scan_nodes_from_cache(db) if include_nodes else []

    by_classification: Dict[str, int] = {}
    for finding in list(findings) + list(node_findings):
        by_classification[finding.classification] = (
            by_classification.get(finding.classification, 0) + 1
        )

    summary = {
        "scanned": len(findings) + len(node_findings),
        "pruned": len(pruned),
        "aborted": decision.aborted,
        "abort_reason": decision.abort_reason,
        "by_classification": by_classification,
    }
    ssh_audit.logger.info("SSH key audit finished: %s", summary)
    return summary


@celery_app.task(name="tasks.ssh_key_audit_task")
def ssh_key_audit_task(include_nodes: bool = False) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return run_audit(db, include_nodes=include_nodes)
    finally:
        db.close()
