"""Which backups the schedule will actually produce, and what they will cost.

Two endpoints need the same walk over the fleet: the load calendar, which asks
whether each group's busiest day fits its execution window, and the repository
capacity report, which asks the same question of each borg repository. Doing
the walk twice would let the two drift apart in exactly the way
`core/schedule_slots` exists to prevent, so it lives here once.

The walk resolves three things per scheduled run and hands them on as plain
data: which repository it lands in, how long it is expected to take, and the
absolute time span it may use. Everything downstream is arithmetic on those.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

import models
from core.schedule_estimate import DurationEstimator
from core.schedule_slots import (
    WindowBounds,
    get_tzinfo,
    is_scheduled_on,
    node_slot,
    parse_window,
)


@dataclass(frozen=True)
class ProjectedRun:
    """One backup the schedule will produce, priced and placed."""

    node_id: int
    hostname: str
    group_id: int
    #: The repository this node's archives live in. Stored on the node, never
    #: recomputed -- see `core/repo_paths`.
    shard_index: int
    #: The date the execution window opens on, in the group's own timezone.
    #: A run beginning at 23:00 belongs to the day it started, not the one it
    #: finishes on.
    day: date
    hours: float
    #: True when `hours` came from this node's own recorded runs rather than
    #: from a rate limit or the fallback constant.
    measured: bool
    #: Absolute bounds of the window this run may use. Absolute rather than
    #: "02:00-05:00" because groups carry their own timezones, and two groups
    #: nominally sharing a window can be hours apart in real time.
    window_start: datetime
    window_end: datetime
    #: When the run is expected to begin, in the dashboard's timezone. Only the
    #: hourly load histogram needs this.
    run_at: datetime


@dataclass
class GroupContext:
    """The per-group values the walk resolves once instead of per node."""

    group: models.BackupGroup
    window: WindowBounds
    tzinfo: object


def _active_nodes(db: Session) -> List[models.Node]:
    return (
        db.query(models.Node)
        .filter(
            models.Node.group_id.isnot(None),
            models.Node.backup_paused == False,  # noqa: E712 - SQL, not Python
        )
        .all()
    )


def project_runs(
    db: Session,
    days: Sequence[date],
    target_tz,
    nodes: Optional[Iterable[models.Node]] = None,
) -> List[ProjectedRun]:
    """Every backup the schedule produces across `days`.

    `days` are calendar dates evaluated against each group's own timezone, so
    the same date means a different absolute moment for groups in different
    places -- which is the point.

    Durations come from `DurationEstimator`, which resolves the whole fleet in
    three queries. Pricing nodes one at a time here would reintroduce the N+1
    the estimator exists to remove, on a path that runs every minute.
    """
    node_list = list(nodes) if nodes is not None else _active_nodes(db)
    if not node_list:
        return []

    groups = {g.id: g for g in db.query(models.BackupGroup).all()}
    estimator = DurationEstimator(db, [n.id for n in node_list])

    contexts: Dict[int, GroupContext] = {}
    for group in groups.values():
        contexts[group.id] = GroupContext(
            group=group,
            window=parse_window(group.start_time, group.end_time),
            tzinfo=get_tzinfo(group.timezone, db),
        )

    runs: List[ProjectedRun] = []

    for node in node_list:
        context = contexts.get(node.group_id)
        if context is None:
            continue

        group = context.group
        window = context.window
        start_h, start_m = divmod(window.start_mins, 60)

        hours = estimator.minutes(node.id, group.upload_rate_limit) / 60.0
        measured = estimator.is_measured(node.id)
        # Unset means a node enrolled before sharding, which is repository 0 --
        # the same fallback `repo_paths.repo_path_for_node` applies.
        shard_index = node.borg_shard_index or 0
        slot = node_slot(group, node.hostname, window)

        for day in days:
            window_start = datetime(
                day.year, day.month, day.day, start_h, start_m, tzinfo=context.tzinfo
            )
            if not is_scheduled_on(group, node.hostname, window_start, window):
                continue

            window_end = window_start + timedelta(minutes=window.duration_minutes)
            run_at = (
                window_start + timedelta(minutes=slot.stagger_offset_mins)
            ).astimezone(target_tz)

            runs.append(
                ProjectedRun(
                    node_id=node.id,
                    hostname=node.hostname,
                    group_id=group.id,
                    shard_index=shard_index,
                    day=day,
                    hours=hours,
                    measured=measured,
                    window_start=window_start,
                    window_end=window_end,
                    run_at=run_at,
                )
            )

    return runs


def runs_per_node(runs: Sequence[ProjectedRun]) -> float:
    """Average number of times a node runs across the projected period.

    What converts a per-night capacity into a sustained one: a node scheduled
    monthly occupies a small fraction of a nightly slot, not a whole one.
    """
    if not runs:
        return 0.0
    distinct_nodes = len({run.node_id for run in runs})
    if not distinct_nodes:
        return 0.0
    return len(runs) / distinct_nodes
