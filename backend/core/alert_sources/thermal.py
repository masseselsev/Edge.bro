"""Thermal alert candidates, one per node whose combined verdict is WATCH or
ALERT. Calls the same verdict function the dashboard badge uses.

Known cost: this calls thermal_verdict() once per node, and that function
re-runs a fleet-wide peer query internally each time — O(nodes) redundant
identical-shaped queries per sweep. Acceptable at the 200-1000 node scale
this was built for and at an hourly cadence; worth batching if either grows
enough to matter.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

import models
from core.alerts import AlertCandidate
from core.monitoring_verdicts import thermal_verdict

_ALERT_STATUSES = {"WATCH", "ALERT"}


def evaluate(db: Session) -> List[AlertCandidate]:
    now = datetime.utcnow()
    candidates: List[AlertCandidate] = []
    for node in db.query(models.Node).all():
        verdict = thermal_verdict(db, node, now)
        if verdict.status not in _ALERT_STATUSES:
            continue
        candidates.append(AlertCandidate(
            module="thermal",
            node_id=node.id,
            dedup_key=f"thermal:{node.id}",
            severity=verdict.status,
            title=f"Thermal interface {verdict.status.lower()}: {node.hostname}",
            detail={
                "theta_c_per_w": verdict.theta_c_per_w,
                "cohort_status": verdict.cohort_status,
                "drift_status": verdict.drift_status,
                "reasons": verdict.reasons,
            },
        ))
    return candidates
