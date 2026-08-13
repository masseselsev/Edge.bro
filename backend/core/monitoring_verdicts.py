"""DB-facing verdict computation for the monitoring dashboard and the alert
sweep. Unlike core.thermal/core.cohort, this module takes a Session — it
belongs here rather than in either pure module, the same way core.scheduler
and core.hasp_helper already mix Session access with business logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

import models
from core import cohort

#: How far back the thermal verdict looks for its "recent" evidence.
RECENT_WINDOW_DAYS = 30

#: The baseline is taken from the node's first weeks of service, when the
#: thermal interface was fresh. Anything later risks baselining a fault.
BASELINE_WINDOW_DAYS = 60


@dataclass
class ThermalVerdict:
    status: str
    cohort_status: Optional[str] = None
    drift_status: Optional[str] = None
    theta_c_per_w: Optional[float] = None
    cohort_key: Optional[str] = None
    cohort_size: int = 0
    cohort_median: Optional[float] = None
    z_score: Optional[float] = None
    excess_ratio: Optional[float] = None
    baseline_theta: Optional[float] = None
    recent_theta: Optional[float] = None
    drift_ratio: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    windows_fitted: int = 0
    windows_rejected: int = 0
    last_rejection: Optional[str] = None


def thermal_verdict(db: Session, node: "models.Node", now: datetime) -> ThermalVerdict:
    """Run both detectors for one node against the current fleet."""
    recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)

    own = (
        db.query(models.ThermalFit)
        .filter(models.ThermalFit.node_id == node.id,
                models.ThermalFit.window_start >= recent_cutoff)
        .all()
    )
    fitted = [f for f in own if f.rejection == "OK"]
    rejected = [f for f in own if f.rejection != "OK"]

    last_rejection = None
    if rejected:
        last_rejection = max(rejected, key=lambda f: f.window_start).rejection

    # Cohort: every node's recent fits, grouped by hardware. Pulled in one
    # query rather than per node — a thousand-node fleet would otherwise make
    # this endpoint a thousand round trips.
    peer_rows = (
        db.query(models.ThermalFit.node_id, models.ThermalFit.theta_c_per_w,
                 models.ThermalFit.theta_normalised)
        .filter(models.ThermalFit.window_start >= recent_cutoff,
                models.ThermalFit.rejection == "OK")
        .all()
    )
    observations: Dict[int, list] = {}
    for row in peer_rows:
        observations.setdefault(row.node_id, []).append(
            cohort.Observation(node_id=row.node_id, theta=row.theta_c_per_w,
                               theta_normalised=row.theta_normalised)
        )

    keys = {
        n.id: cohort.cohort_key(n.cpu_info)
        for n in db.query(models.Node.id, models.Node.cpu_info).all()
    }

    cohort_verdict = None
    if node.id in observations:
        verdicts = cohort.assess_cohort(observations, keys)
        cohort_verdict = next((v for v in verdicts if v.node_id == node.id), None)

    # Drift: the node's own first weeks against its recent ones.
    baseline_rows = (
        db.query(models.ThermalFit)
        .filter(models.ThermalFit.node_id == node.id, models.ThermalFit.rejection == "OK")
        .order_by(models.ThermalFit.window_start.asc())
        .limit(200)
        .all()
    )
    baseline_cutoff = (
        baseline_rows[0].window_start + timedelta(days=BASELINE_WINDOW_DAYS)
        if baseline_rows else None
    )
    baseline = [
        cohort.Observation(node.id, f.theta_c_per_w, f.theta_normalised)
        for f in baseline_rows
        if baseline_cutoff and f.window_start <= baseline_cutoff
    ]
    recent = [
        cohort.Observation(node.id, f.theta_c_per_w, f.theta_normalised) for f in fitted
    ]
    drift_verdict = cohort.assess_drift(node.id, baseline, recent)

    combined = cohort.combine(cohort_verdict, drift_verdict)
    reasons = [
        v.reason for v in (cohort_verdict, drift_verdict) if v is not None and v.reason
    ]

    return ThermalVerdict(
        status=combined.value,
        cohort_status=cohort_verdict.status.value if cohort_verdict else None,
        drift_status=drift_verdict.status.value,
        theta_c_per_w=cohort_verdict.theta if cohort_verdict else None,
        cohort_key=cohort_verdict.cohort_key if cohort_verdict else keys.get(node.id),
        cohort_size=cohort_verdict.cohort_size if cohort_verdict else 0,
        cohort_median=cohort_verdict.cohort_median if cohort_verdict else None,
        z_score=cohort_verdict.z_score if cohort_verdict else None,
        excess_ratio=cohort_verdict.excess_ratio if cohort_verdict else None,
        baseline_theta=drift_verdict.baseline_theta,
        recent_theta=drift_verdict.recent_theta,
        drift_ratio=drift_verdict.ratio,
        reasons=reasons,
        windows_fitted=len(fitted),
        windows_rejected=len(rejected),
        last_rejection=last_rejection,
    )
