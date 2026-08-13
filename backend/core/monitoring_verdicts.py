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


#: Safety cap on how many of a node's earliest fits are considered when
#: establishing its baseline. At the observed ~0.6 accepted fits per node per
#: day, BASELINE_WINDOW_DAYS covers roughly 36 fits, so this only bites on a
#: node that was sampled far more aggressively than the default cadence.
BASELINE_MAX_FITS = 200


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


@dataclass
class ThermalContext:
    """Everything the thermal detectors need about the whole fleet, read once.

    The cohort detector is inherently fleet-wide: judging one node means
    comparing it against every peer with the same CPU. That made the obvious
    implementation — call `thermal_verdict(node)` in a loop — quadratic, since
    each call re-read every fit in the fleet and then threw away all but one
    of the verdicts it computed. At 2000 nodes the hourly alert sweep was
    materialising tens of millions of rows to produce a few hundred alerts.

    Building this once and reading per node makes the sweep a fixed three
    queries regardless of fleet size.
    """
    now: datetime
    cohort_keys: Dict[int, str] = field(default_factory=dict)
    cohort_verdicts: Dict[int, "cohort.CohortVerdict"] = field(default_factory=dict)
    #: node_id -> recent accepted fits, as cohort observations
    recent_by_node: Dict[int, List["cohort.Observation"]] = field(default_factory=dict)
    #: node_id -> earliest-service accepted fits, as cohort observations
    baseline_by_node: Dict[int, List["cohort.Observation"]] = field(default_factory=dict)
    #: node_id -> (accepted count, rejected count, most recent rejection reason)
    window_counts: Dict[int, tuple] = field(default_factory=dict)


def build_thermal_context(db: Session, now: datetime) -> ThermalContext:
    """Read the fleet's thermal state in a fixed number of queries."""
    recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)

    ctx = ThermalContext(now=now)

    # 1. Cohort keys for every node.
    ctx.cohort_keys = {
        n.id: cohort.cohort_key(n.cpu_info)
        for n in db.query(models.Node.id, models.Node.cpu_info).all()
    }

    # 2. Every fit inside the recent window, accepted or not. Serves the
    #    cohort comparison, each node's recent θ, and the fitted/rejected
    #    counts shown in the UI.
    recent_rows = (
        db.query(
            models.ThermalFit.node_id,
            models.ThermalFit.theta_c_per_w,
            models.ThermalFit.theta_normalised,
            models.ThermalFit.rejection,
            models.ThermalFit.window_start,
        )
        .filter(models.ThermalFit.window_start >= recent_cutoff)
        .all()
    )

    accepted: Dict[int, int] = {}
    rejected: Dict[int, int] = {}
    last_rejection: Dict[int, tuple] = {}
    for row in recent_rows:
        if row.rejection == "OK":
            accepted[row.node_id] = accepted.get(row.node_id, 0) + 1
            ctx.recent_by_node.setdefault(row.node_id, []).append(
                cohort.Observation(
                    node_id=row.node_id,
                    theta=row.theta_c_per_w,
                    theta_normalised=row.theta_normalised,
                )
            )
        else:
            rejected[row.node_id] = rejected.get(row.node_id, 0) + 1
            prev = last_rejection.get(row.node_id)
            if prev is None or row.window_start > prev[0]:
                last_rejection[row.node_id] = (row.window_start, row.rejection)

    for node_id in set(accepted) | set(rejected):
        ctx.window_counts[node_id] = (
            accepted.get(node_id, 0),
            rejected.get(node_id, 0),
            (last_rejection.get(node_id) or (None, None))[1],
        )

    # 3. Baseline fits — each node's first weeks of service.
    #
    #    Streamed with yield_per and cut off per node rather than pulled into
    #    memory whole: accepted fits are deliberately never pruned, so this
    #    table is the one that grows without bound, and a fleet with years of
    #    history would otherwise load all of it to read the first month of each.
    #    Ordering by (node_id, window_start) lets us stop collecting for a node
    #    as soon as it leaves its baseline window.
    baseline_cutoffs: Dict[int, datetime] = {}
    query = (
        db.query(
            models.ThermalFit.node_id,
            models.ThermalFit.theta_c_per_w,
            models.ThermalFit.theta_normalised,
            models.ThermalFit.window_start,
        )
        .filter(models.ThermalFit.rejection == "OK")
        .order_by(models.ThermalFit.node_id.asc(), models.ThermalFit.window_start.asc())
    )
    for row in query.yield_per(1000):
        cutoff = baseline_cutoffs.get(row.node_id)
        if cutoff is None:
            # First row for this node is, by the ordering, its earliest fit.
            cutoff = row.window_start + timedelta(days=BASELINE_WINDOW_DAYS)
            baseline_cutoffs[row.node_id] = cutoff
        if row.window_start > cutoff:
            continue
        bucket = ctx.baseline_by_node.setdefault(row.node_id, [])
        if len(bucket) >= BASELINE_MAX_FITS:
            continue
        bucket.append(
            cohort.Observation(
                node_id=row.node_id,
                theta=row.theta_c_per_w,
                theta_normalised=row.theta_normalised,
            )
        )

    # 4. One cohort pass for the entire fleet, indexed for O(1) lookup.
    if ctx.recent_by_node:
        ctx.cohort_verdicts = {
            v.node_id: v
            for v in cohort.assess_cohort(ctx.recent_by_node, ctx.cohort_keys)
        }

    return ctx


def verdict_from_context(ctx: ThermalContext, node_id: int) -> ThermalVerdict:
    """One node's verdict, read out of a prebuilt context. Issues no queries."""
    cohort_verdict = ctx.cohort_verdicts.get(node_id)
    baseline = ctx.baseline_by_node.get(node_id, [])
    recent = ctx.recent_by_node.get(node_id, [])
    drift_verdict = cohort.assess_drift(node_id, baseline, recent)

    fitted_count, rejected_count, last_rejection = ctx.window_counts.get(
        node_id, (0, 0, None)
    )

    combined = cohort.combine(cohort_verdict, drift_verdict)
    reasons = [
        v.reason for v in (cohort_verdict, drift_verdict) if v is not None and v.reason
    ]

    return ThermalVerdict(
        status=combined.value,
        cohort_status=cohort_verdict.status.value if cohort_verdict else None,
        drift_status=drift_verdict.status.value,
        theta_c_per_w=cohort_verdict.theta if cohort_verdict else None,
        cohort_key=(
            cohort_verdict.cohort_key if cohort_verdict else ctx.cohort_keys.get(node_id)
        ),
        cohort_size=cohort_verdict.cohort_size if cohort_verdict else 0,
        cohort_median=cohort_verdict.cohort_median if cohort_verdict else None,
        z_score=cohort_verdict.z_score if cohort_verdict else None,
        excess_ratio=cohort_verdict.excess_ratio if cohort_verdict else None,
        baseline_theta=drift_verdict.baseline_theta,
        recent_theta=drift_verdict.recent_theta,
        drift_ratio=drift_verdict.ratio,
        reasons=reasons,
        windows_fitted=fitted_count,
        windows_rejected=rejected_count,
        last_rejection=last_rejection,
    )


def thermal_verdict(db: Session, node: "models.Node", now: datetime) -> ThermalVerdict:
    """Run both detectors for one node against the current fleet.

    Convenience wrapper for single-node callers such as the node health
    endpoint. Anything iterating more than a handful of nodes should build a
    ThermalContext once and call verdict_from_context, or it pays the
    fleet-wide read per node.
    """
    return verdict_from_context(build_thermal_context(db, now), node.id)
