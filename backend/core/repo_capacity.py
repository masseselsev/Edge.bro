"""How much of each borg repository's nightly window is actually consumed.

Borg holds a repository's exclusive lock for the whole of `borg create`, so the
number of repositories *is* the number of parallel writers. That makes
`BORG_SHARD_COUNT` the most consequential storage setting in the product, and
nothing in the interface has ever said whether the chosen value is right — the
only feedback is a backup window that silently overruns, months later.

This module turns the schedule into that answer. It is deliberately pure: every
function takes plain values and returns plain values, so the arithmetic can be
tested without a database, a clock or a filesystem. The caller resolves
timezones, recurrence and durations and hands the results in.

Two properties of the system shape everything here:

**A node's repository never changes.** `Node.borg_shard_index` is computed once
at enrolment and persisted (see `core/repo_paths`), so raising
`BORG_SHARD_COUNT` does not redistribute the existing fleet — it only routes
new enrolments. A forecast that models expansion as relief for current load
would recommend a change that delivers nothing, which is why
`project_expansion` reports existing load as unchanged rather than spreading it.

**Repositories multiply locks, not bandwidth.** Five repositories behind one
network mount are five writers sharing one pipe. `measure_shared_ceiling`
exists to find that pipe in the recorded history rather than assume it away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: Aggregate throughput has to improve by at least this factor when another
#: writer is added, or the shared path underneath is taken to be the limit
#: rather than the number of repositories.
SATURATION_GAIN = 1.05

#: An overlap shorter than this is not a rate. Two backups brushing past each
#: other for a couple of seconds say nothing about sustained throughput, and
#: without this a single such brush would flip the whole report to "measured".
#: The same reasoning as `transfer_speed.SpeedTracker.min_span_seconds`, at the
#: scale of whole backups rather than progress samples.
MIN_SEGMENT_SECONDS = 60.0

#: A repository this full has no useful room left, and is where the advice
#: changes from "add repositories" to "move archives".
CROWDED_UTILIZATION_PCT = 80.0


# ─────────────────────────── window arithmetic ───────────────────────────


def merge_intervals(
    intervals: Iterable[Tuple[datetime, datetime]]
) -> List[Tuple[datetime, datetime]]:
    """Overlapping intervals collapsed into disjoint ones, in order.

    A repository serving several backup groups can work across the union of
    their execution windows, not just one of them. Touching intervals merge:
    a window ending at 04:00 and another starting at 04:00 are one span of
    working time, not two.
    """
    ordered = sorted((s, e) for s, e in intervals if e > s)
    if not ordered:
        return []

    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def union_hours(intervals: Iterable[Tuple[datetime, datetime]]) -> float:
    """Total hours covered by the union of the given intervals."""
    return sum(
        (end - start).total_seconds() / 3600.0
        for start, end in merge_intervals(intervals)
    )


def utilization_pct(load_hours: float, window_hours: float) -> Optional[float]:
    """Share of the available window the load consumes, as a percentage.

    None when there is no window to divide by — a group configured with a
    zero-length window is a configuration error, not a full repository, and
    reporting it as infinitely overloaded would bury the real cause.
    """
    if window_hours <= 0:
        return None
    return (load_hours / window_hours) * 100.0


# ─────────────────────────── per-shard load ───────────────────────────


@dataclass(frozen=True)
class ScheduledRun:
    """One node's backup, on one night, as the projection sees it."""

    node_id: int
    shard_index: int
    day: date
    hours: float
    #: The execution window this run may use, in absolute time. Absolute rather
    #: than "02:00-05:00" because groups keep their own timezones, and two
    #: groups nominally sharing a window can be six hours apart in practice.
    window_start: datetime
    window_end: datetime


@dataclass
class ShardNight:
    """What one repository is asked to do on one night."""

    shard_index: int
    day: date
    hours: float = 0.0
    node_ids: List[int] = field(default_factory=list)
    windows: List[Tuple[datetime, datetime]] = field(default_factory=list)

    @property
    def window_hours(self) -> float:
        return union_hours(self.windows)

    @property
    def utilization_pct(self) -> Optional[float]:
        return utilization_pct(self.hours, self.window_hours)

    @property
    def node_count(self) -> int:
        return len(self.node_ids)


def shard_nights(runs: Iterable[ScheduledRun]) -> Dict[Tuple[int, date], ShardNight]:
    """Group scheduled runs by the repository and night they land on."""
    nights: Dict[Tuple[int, date], ShardNight] = {}
    for run in runs:
        key = (run.shard_index, run.day)
        night = nights.get(key)
        if night is None:
            night = ShardNight(shard_index=run.shard_index, day=run.day)
            nights[key] = night
        night.hours += run.hours
        night.node_ids.append(run.node_id)
        night.windows.append((run.window_start, run.window_end))
    return nights


def busiest_night_per_shard(
    nights: Dict[Tuple[int, date], ShardNight]
) -> Dict[int, ShardNight]:
    """The worst night each repository faces.

    The worst night is what decides whether a layout works. An average hides
    exactly the case that matters: a repository idle six nights a week and
    overrunning on the seventh is a repository that fails weekly.
    """
    worst: Dict[int, ShardNight] = {}
    for night in nights.values():
        current = worst.get(night.shard_index)
        if current is None or night.hours > current.hours:
            worst[night.shard_index] = night
    return worst


def distinct_shards_on(runs: Iterable[ScheduledRun]) -> int:
    """How many repositories a set of runs actually spreads across.

    This is a group's real concurrency ceiling, and it is routinely lower than
    the repository count: nodes are assigned by `node_id % SHARD_COUNT`, which
    knows nothing about group membership, so five nodes of one group can share
    one repository and serialize behind a single lock while four repositories
    sit idle.
    """
    return len({run.shard_index for run in runs})


# ─────────────────────────── capacity ───────────────────────────


def nodes_per_night(window_hours: float, node_hours: float) -> int:
    """How many nodes one repository can serve in a single night."""
    if node_hours <= 0 or window_hours <= 0:
        return 0
    return int(window_hours // node_hours)


def sustained_nodes_per_shard(
    period_nights: int,
    window_hours: float,
    runs_per_node: float,
    node_hours: float,
) -> int:
    """How many nodes one repository can carry at the current schedule mix.

    Higher than the per-night figure, and the more useful of the two: backup
    groups spread the fleet across weeks and months, so a node scheduled
    monthly occupies roughly a thirtieth of a nightly slot rather than a whole
    one. `runs_per_node` is what converts between the two — the average number
    of times a node runs over the projected period.
    """
    if node_hours <= 0 or window_hours <= 0 or period_nights <= 0:
        return 0
    if runs_per_node <= 0:
        return 0
    capacity_hours = period_nights * window_hours
    hours_per_node = runs_per_node * node_hours
    return int(capacity_hours // hours_per_node)


@dataclass(frozen=True)
class ExpansionOutlook:
    """What a given repository count would mean, honestly stated."""

    shard_count: int
    #: Utilization of the busiest repository. Unchanged from today for every
    #: count at or above the current one, because existing nodes do not move.
    busiest_utilization_pct: Optional[float]
    #: Always False. Kept as an explicit field rather than left implicit,
    #: because "more repositories will fix the current overload" is the wrong
    #: conclusion this whole forecast exists to prevent.
    relieves_existing: bool
    #: New enrolments that fit before the first repository saturates.
    new_node_headroom: int


def project_expansion(
    shard_hours: Dict[int, float],
    window_hours: float,
    node_hours: float,
    current_count: int,
    candidate_counts: Sequence[int],
) -> List[ExpansionOutlook]:
    """What each candidate repository count would deliver.

    New enrolments are assigned `node_id % SHARD_COUNT`, which is uniform over
    the repositories in the long run, so `n` new nodes add roughly `n / S`
    nodes to each. The first repository to saturate is therefore the one
    already fullest, and the headroom is what it can still absorb, multiplied
    back up by `S` to express it as a fleet-wide number of enrolments.

    Existing load is carried across unchanged, at every candidate count. That
    is not a simplification: a node's repository is fixed at enrolment, so
    raising the count genuinely does nothing for the nodes already placed.
    """
    outlooks: List[ExpansionOutlook] = []

    for count in candidate_counts:
        if count < current_count:
            # Lowering is prevented elsewhere (`repo_paths` floors the count by
            # the directories on disk); projecting it would only suggest it.
            continue

        # Repositories beyond the current count exist but hold nothing yet.
        hours_by_shard = {i: shard_hours.get(i, 0.0) for i in range(count)}
        busiest = max(hours_by_shard.values()) if hours_by_shard else 0.0

        if node_hours > 0 and window_hours > 0:
            per_shard_headroom = [
                max(0.0, window_hours - hours) / node_hours
                for hours in hours_by_shard.values()
            ]
            headroom = int(count * min(per_shard_headroom)) if per_shard_headroom else 0
        else:
            headroom = 0

        outlooks.append(
            ExpansionOutlook(
                shard_count=count,
                busiest_utilization_pct=utilization_pct(busiest, window_hours),
                relieves_existing=False,
                new_node_headroom=headroom,
            )
        )

    return outlooks


# ─────────────────────────── measured storage ceiling ───────────────────────────


@dataclass(frozen=True)
class ObservedRun:
    """A completed backup, as the throughput sweep sees it."""

    start: datetime
    end: datetime
    mbps: float
    #: Which node produced it. A node backs up one archive at a time, so two
    #: overlapping records for one node are duplicated or backfilled rows
    #: rather than genuine contention, and counting both would inflate the
    #: measured ceiling with throughput that never happened at once.
    node_id: Optional[int] = None


@dataclass(frozen=True)
class CeilingObservation:
    """What the recorded history says the shared storage path can carry."""

    #: Highest aggregate throughput ever observed across concurrent writers.
    #: A lower bound on capability — never an estimate of what the hardware
    #: could do under load it has not yet seen.
    ceiling_mbps: Optional[float]
    #: The most writers seen running at once.
    max_observed_writers: int
    #: Distinct constant-concurrency stretches the sweep found.
    segments: int
    #: False until at least two backups have genuinely overlapped. Nothing here
    #: can be concluded from a deployment that has only ever run one at a time,
    #: and saying so is the correct output rather than a failure.
    sufficient: bool
    #: Aggregate throughput stopped improving as writers were added.
    saturated: bool
    #: Best aggregate seen at each concurrency level, for the caller to show.
    by_writers: Dict[int, float]


def measure_shared_ceiling(runs: Sequence[ObservedRun]) -> CeilingObservation:
    """Find the shared throughput ceiling by sweeping recorded backups.

    Every backup is recorded with a completion time, a duration and an average
    throughput, so the intervals they occupied can be reconstructed and swept.
    Wherever several overlapped, their throughputs sum to what the shared path
    was carrying at that moment; the largest such sum is the most it has been
    seen to carry.

    Two honest limits, both worth stating rather than hiding:

    Throughput here derives from borg's `deduplicated_size` counter, which also
    advances for chunks already present in the repository that never crossed
    the wire — the same effect `core.transfer_speed.limit_is_binding`
    compensates for. It can therefore read above true wire throughput, though
    the repository does perform the work either way.

    And a ceiling is a *lower bound*: it says the path has carried this much,
    not that it cannot carry more. Concurrency it has never been asked for
    tells us nothing, which is what `sufficient` reports.
    """
    usable = [r for r in runs if r.end > r.start and r.mbps and r.mbps > 0]
    if not usable:
        return CeilingObservation(None, 0, 0, False, False, {})

    # Sweep the boundaries: between two consecutive boundaries the set of
    # active runs cannot change, so throughput there is constant.
    boundaries = sorted({r.start for r in usable} | {r.end for r in usable})

    by_writers: Dict[int, float] = {}
    segments = 0

    for left, right in zip(boundaries, boundaries[1:]):
        if (right - left).total_seconds() < MIN_SEGMENT_SECONDS:
            continue
        active = [r for r in usable if r.start < right and r.end > left]
        if not active:
            continue
        segments += 1

        # One writer per node. Where a node has several records covering the
        # same moment -- duplicated rows, a backfill, a clock that moved -- the
        # fastest stands in for the node rather than all of them summing into a
        # throughput figure nothing ever achieved.
        fastest_per_node: Dict[object, float] = {}
        for index, run in enumerate(active):
            # An absent node id cannot be pooled with anything, so it keeps its
            # own slot rather than colliding with every other anonymous run.
            key = run.node_id if run.node_id is not None else ("anon", index)
            if run.mbps > fastest_per_node.get(key, 0.0):
                fastest_per_node[key] = run.mbps

        writers = len(fastest_per_node)
        aggregate = sum(fastest_per_node.values())
        if aggregate > by_writers.get(writers, 0.0):
            by_writers[writers] = aggregate

    if not by_writers:
        return CeilingObservation(None, 0, 0, False, False, {})

    max_writers = max(by_writers)
    ceiling = max(by_writers.values())
    # The one question this flag names: has anything ever actually contended?
    # A ceiling from a deployment that only ever ran one backup at a time
    # measures a single node's link, not the storage behind every repository.
    sufficient = max_writers >= 2

    # Saturation is claimed only on the weakest defensible evidence: adding a
    # writer stopped increasing total throughput. Comparing against a multiple
    # of the single-writer figure would be stronger and wrong — one fast node
    # alone can beat two slow ones together without anything being saturated.
    saturated = False
    if sufficient:
        for writers in range(2, max_writers + 1):
            here = by_writers.get(writers)
            below = by_writers.get(writers - 1)
            if here is None or below is None:
                continue
            if here < below * SATURATION_GAIN:
                saturated = True
                break

    return CeilingObservation(
        ceiling_mbps=ceiling,
        max_observed_writers=max_writers,
        segments=segments,
        sufficient=sufficient,
        saturated=saturated,
        by_writers=by_writers,
    )


def writers_supported_by_storage(
    ceiling: CeilingObservation, per_node_mbps: Optional[float]
) -> Optional[int]:
    """How many concurrent writers the measured ceiling supports.

    None when the history cannot say — which is the common case on a young
    deployment, and must stay distinguishable from "supports one".
    """
    if not ceiling.sufficient or not ceiling.ceiling_mbps:
        return None
    if not per_node_mbps or per_node_mbps <= 0:
        return None
    return max(1, int(ceiling.ceiling_mbps // per_node_mbps))


def median_or_none(values: Iterable[float]) -> Optional[float]:
    """Median of whatever is present, or None if nothing is."""
    usable = [v for v in values if v is not None and v > 0]
    if not usable:
        return None
    return median(usable)
