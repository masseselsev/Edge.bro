"""Deciding whether a node's thermal resistance is a problem.

Two detectors, and the order matters.

**Cohort comparison is primary.** Nodes of the same hardware measured in the
same window share the weather, so whatever ambient, wind and sun are doing
cancels between them. That is the one comparison θ's residual ambient
dependence cannot corrupt — and that dependence is 15-20 % across a seasonal
swing, the same size as the degradation being hunted.

**Self-baseline drift is secondary.** Comparing a node against its own past
crosses seasons, so it needs the normalisation in `core.thermal` and is
therefore only as good as that correction. It exists because it catches what
the cohort cannot: a fault that affects the whole cohort at once, and any node
whose cohort is too small to have a distribution.

Both are deliberately conservative about small numbers. A cohort of three
says nothing, and a robust z-score computed over a handful of peers is not
robust. Where the data does not support a verdict, these return one saying so
rather than a confident-looking number.

Pure arithmetic over plain values — no database, no clock of its own.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Sequence

#: Below this many peers a cohort has no usable distribution. Three nodes
#: cannot tell an outlier from a spread.
MIN_COHORT_SIZE = 5

#: Robust z beyond which a node is unlike its peers. 3 sigma on a normal
#: distribution is ~1 in 370; with a robust estimator and a fleet of a
#: thousand it is a handful of false positives, which is the right trade
#: against missing a drying thermal pad.
Z_ALERT = 3.0
Z_WATCH = 2.0

#: A z-score alone is not enough. In a very uniform cohort the spread can be
#: so tight that a trivial difference scores enormously — every node within a
#: degree of its peers, and one half a degree off reads as ten sigma. A node
#: must also be materially worse than the cohort before it is worth a callout.
MIN_EXCESS_RATIO = 0.15

#: Self-baseline: how much worse than its own established baseline a node must
#: run before that counts as drift. Larger than the cohort threshold because
#: this comparison carries the ambient-normalisation error too.
DRIFT_ALERT_RATIO = 1.30
DRIFT_WATCH_RATIO = 1.15

#: Neither detector runs on a single measurement. Thermal fits vary window to
#: window; the median of several is what carries signal.
MIN_OBSERVATIONS = 3


class Status(str, Enum):
    OK = "OK"
    WATCH = "WATCH"
    ALERT = "ALERT"
    #: Not enough peers, or not enough measurements, to say anything.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class Observation:
    """One node's thermal resistance from one fitted window."""

    node_id: int
    theta: float
    #: Corrected back to reference conditions. Required for drift, ignored for
    #: cohort comparison, where sharing the weather already cancels it.
    theta_normalised: Optional[float] = None


@dataclass(frozen=True)
class CohortVerdict:
    node_id: int
    cohort_key: str
    cohort_size: int
    status: Status
    #: Median of this node's own observations in the window.
    theta: Optional[float] = None
    cohort_median: Optional[float] = None
    z_score: Optional[float] = None
    #: How much worse than the cohort median, as a fraction.
    excess_ratio: Optional[float] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class DriftVerdict:
    node_id: int
    status: Status
    baseline_theta: Optional[float] = None
    recent_theta: Optional[float] = None
    ratio: Optional[float] = None
    baseline_samples: int = 0
    recent_samples: int = 0
    reason: Optional[str] = None


def cohort_key(cpu_info: Optional[str]) -> str:
    """The hardware identity that determines thermal behaviour.

    Two nodes are comparable when they dissipate the same power through the
    same package into the same heatsink, which in this fleet is decided by the
    CPU model. Normalised because the same part arrives with cosmetic
    differences in how it names itself — trademark symbols, doubled spaces,
    a trailing clock speed that varies with how it was read.
    """
    if not cpu_info:
        return "unknown"

    text = cpu_info.replace("(R)", "").replace("(TM)", "").replace("®", "").replace("™", "")
    # "11th Gen Intel Core i5-1145G7E @ 2.60GHz" -> the part number is what
    # identifies the silicon; the advertised clock is noise here. The suffix
    # is alphanumeric, not just letters: the embedded parts in this fleet end
    # in G7E, and matching only [A-Z] would silently split i5-1145G7E from
    # itself depending on how the string was gathered.
    match = re.search(r"\b([a-zA-Z]\d{1,2}-\d{3,5}[A-Za-z0-9]{0,4})\b", text)
    if match:
        return match.group(1).lower()

    text = re.sub(r"@.*$", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text or "unknown"


def _median(values: Sequence[float]) -> Optional[float]:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return float(clean[middle])
    return (clean[middle - 1] + clean[middle]) / 2.0


def _mad(values: Sequence[float], centre: float) -> float:
    """Median absolute deviation, scaled to be comparable to a std deviation.

    Used instead of the standard deviation because the thing being detected is
    an outlier: a node with a failing thermal interface would inflate the
    spread it is being measured against and hide itself.
    """
    deviations = [abs(v - centre) for v in values if v is not None]
    middle = _median(deviations)
    return 1.4826 * middle if middle else 0.0


def assess_cohort(
    observations_by_node: dict,
    cohort_keys: dict,
    min_cohort_size: int = MIN_COHORT_SIZE,
    min_observations: int = MIN_OBSERVATIONS,
) -> list:
    """Compare each node's θ against peers of the same hardware.

    `observations_by_node` maps node id to that node's Observations from one
    time window; `cohort_keys` maps node id to its hardware key. Every node
    with observations gets a verdict, including ones that cannot be judged —
    silence is indistinguishable from health, and an operator needs to know
    which nodes are simply unassessable.
    """
    members: dict = {}
    node_theta: dict = {}

    for node_id, observations in observations_by_node.items():
        values = [o.theta for o in observations if o.theta is not None]
        if len(values) < min_observations:
            continue
        node_theta[node_id] = _median(values)
        members.setdefault(cohort_keys.get(node_id, "unknown"), []).append(node_id)

    verdicts = []
    for node_id, observations in observations_by_node.items():
        key = cohort_keys.get(node_id, "unknown")
        peers = members.get(key, [])
        size = len(peers)

        if node_id not in node_theta:
            verdicts.append(CohortVerdict(
                node_id=node_id, cohort_key=key, cohort_size=size,
                status=Status.INSUFFICIENT_DATA,
                reason=f"needs {min_observations} fitted windows, has "
                       f"{len([o for o in observations if o.theta is not None])}",
            ))
            continue

        theta = node_theta[node_id]
        if size < min_cohort_size:
            verdicts.append(CohortVerdict(
                node_id=node_id, cohort_key=key, cohort_size=size, theta=theta,
                status=Status.INSUFFICIENT_DATA,
                reason=f"cohort of {size} is too small to hold a distribution; "
                       f"needs {min_cohort_size}",
            ))
            continue

        # The node is judged against its peers, not against a group it is a
        # member of: leaving it in would let a badly degraded node drag the
        # median it is being compared with.
        peer_values = [node_theta[p] for p in peers if p != node_id]
        centre = _median(peer_values)
        spread = _mad(peer_values, centre)

        excess = (theta - centre) / centre if centre else None
        if spread > 0:
            z = (theta - centre) / spread
        else:
            # Every peer identical. Any difference is infinitely many sigma,
            # which is meaningless — fall back to the relative excess alone.
            z = None

        status = Status.OK
        reason = None
        material = excess is not None and excess >= MIN_EXCESS_RATIO
        if material and (z is None or z >= Z_ALERT):
            status = Status.ALERT
            reason = f"{excess:.0%} above the cohort median"
        elif material and z >= Z_WATCH:
            status = Status.WATCH
            reason = f"{excess:.0%} above the cohort median"
        elif z is not None and z >= Z_ALERT:
            # Statistically unlike its peers but not by a meaningful margin.
            status = Status.OK
            reason = "unlike its peers, but by too small a margin to matter"

        verdicts.append(CohortVerdict(
            node_id=node_id, cohort_key=key, cohort_size=size, status=status,
            theta=round(theta, 4), cohort_median=round(centre, 4) if centre else None,
            z_score=round(z, 2) if z is not None else None,
            excess_ratio=round(excess, 4) if excess is not None else None,
            reason=reason,
        ))

    return verdicts


def assess_drift(
    node_id: int,
    baseline: Sequence[Observation],
    recent: Sequence[Observation],
    min_observations: int = MIN_OBSERVATIONS,
) -> DriftVerdict:
    """Compare a node's recent θ against its own established baseline.

    Uses the normalised θ on both sides. This comparison crosses seasons, and
    uncorrected θ genuinely differs by 15-20 % between a hot afternoon and a
    cold night — which would otherwise read as degradation every summer and
    recovery every winter.
    """
    baseline_values = [o.theta_normalised for o in baseline if o.theta_normalised is not None]
    recent_values = [o.theta_normalised for o in recent if o.theta_normalised is not None]

    if len(baseline_values) < min_observations:
        return DriftVerdict(
            node_id=node_id, status=Status.INSUFFICIENT_DATA,
            baseline_samples=len(baseline_values), recent_samples=len(recent_values),
            reason=f"baseline needs {min_observations} normalised fits, "
                   f"has {len(baseline_values)}",
        )

    if len(recent_values) < min_observations:
        return DriftVerdict(
            node_id=node_id, status=Status.INSUFFICIENT_DATA,
            baseline_theta=round(_median(baseline_values), 4),
            baseline_samples=len(baseline_values), recent_samples=len(recent_values),
            reason=f"needs {min_observations} recent normalised fits, "
                   f"has {len(recent_values)}",
        )

    baseline_theta = _median(baseline_values)
    recent_theta = _median(recent_values)
    if not baseline_theta:
        return DriftVerdict(
            node_id=node_id, status=Status.INSUFFICIENT_DATA,
            baseline_samples=len(baseline_values), recent_samples=len(recent_values),
            reason="baseline theta is zero, which is not physical",
        )

    ratio = recent_theta / baseline_theta
    if ratio >= DRIFT_ALERT_RATIO:
        status, reason = Status.ALERT, f"{(ratio - 1):.0%} worse than its own baseline"
    elif ratio >= DRIFT_WATCH_RATIO:
        status, reason = Status.WATCH, f"{(ratio - 1):.0%} worse than its own baseline"
    else:
        status, reason = Status.OK, None

    return DriftVerdict(
        node_id=node_id, status=status,
        baseline_theta=round(baseline_theta, 4),
        recent_theta=round(recent_theta, 4),
        ratio=round(ratio, 3),
        baseline_samples=len(baseline_values),
        recent_samples=len(recent_values),
        reason=reason,
    )


def combine(cohort: Optional[CohortVerdict], drift: Optional[DriftVerdict]) -> Status:
    """The status to show for a node, given both detectors.

    The worse of the two wins. They answer different questions — "unlike its
    peers" and "worse than it used to be" — and either alone is worth acting
    on. INSUFFICIENT_DATA never outranks a real verdict: one detector being
    unable to judge is not a reason to discard what the other found.
    """
    ranking = {
        Status.ALERT: 3,
        Status.WATCH: 2,
        Status.OK: 1,
        Status.INSUFFICIENT_DATA: 0,
    }
    statuses = [v.status for v in (cohort, drift) if v is not None]
    if not statuses:
        return Status.INSUFFICIENT_DATA
    return max(statuses, key=lambda s: ranking[s])
