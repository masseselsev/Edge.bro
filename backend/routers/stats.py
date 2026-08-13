"""Fleet statistics for the Archives page.

Two endpoints, deliberately split by cost. `/api/stats` is the header: a few
sums and a look at the disk, cheap enough to poll. `/api/stats/insights` is the
analysis underneath it — reliability, throughput, window pressure and capacity
— which reads a trailing window of history and is asked for on demand.

All of the arithmetic lives in `core.backup_stats`; this module's job is to
fetch the right rows, hand them over, and shape the result. The one rule worth
stating: nothing here invents a number. A section with no data to work from
reports None and the UI says so.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

import models
import schemas
from core import backup_stats, repo_usage, transfer_speed
from database import get_db
from routers.users import require_admin

router = APIRouter(prefix="/api/stats", dependencies=[Depends(require_admin)])

#: How many entries each "worst offenders" list shows. Long enough to spot a
#: pattern, short enough to stay a summary.
_TOP_N = 5


@router.get("", response_model=schemas.GlobalStatsResponse)
def get_global_stats(db: Session = Depends(get_db)):
    """Fleet-wide totals and what the repository actually occupies.

    The sizes cover every successful archive. The previous version of this
    endpoint summed only each node's first backup, which described a sample of
    three archives while appearing to describe all of them.
    """
    # Summed in the database rather than in Python. This endpoint is polled,
    # and the previous version pulled every backup_history row on every call —
    # at 2000 nodes with a few years of retention that is hundreds of thousands
    # of tuples materialised to produce five integers.
    is_success = models.BackupHistory.status == "SUCCESS"
    totals = db.query(
        func.count(models.BackupHistory.id),
        func.count(func.distinct(models.BackupHistory.node_id)),
        func.coalesce(func.sum(case((is_success, 1), else_=0)), 0),
        func.coalesce(func.sum(case((is_success, models.BackupHistory.original_size), else_=0)), 0),
        func.coalesce(func.sum(case((is_success, models.BackupHistory.deduplicated_size), else_=0)), 0),
    ).one()
    total_archives, nodes_with_archives, successful, total_original, total_dedup = (
        int(totals[0] or 0), int(totals[1] or 0), int(totals[2] or 0),
        int(totals[3] or 0), int(totals[4] or 0),
    )

    # The saving is reported across nodes only. Summing every archive would
    # count a node re-backing up unchanged data as deduplication, which says
    # nothing about how well the shared repository packs the fleet — see
    # backup_stats.base_archive_totals.
    #
    # Each node's base archive is its largest by deduplicated size, so the
    # candidate set is fetched with a greatest-n-per-group join (one row per
    # node) rather than by scanning every archive. Ties return more than one
    # row for a node; base_archive_totals keeps the largest, so that is safe.
    largest = (
        db.query(
            models.BackupHistory.node_id.label("node_id"),
            func.max(models.BackupHistory.deduplicated_size).label("max_dedup"),
        )
        .filter(is_success)
        .group_by(models.BackupHistory.node_id)
        .subquery()
    )
    base_rows = (
        db.query(
            models.BackupHistory.node_id,
            models.BackupHistory.original_size,
            models.BackupHistory.deduplicated_size,
        )
        .join(
            largest,
            and_(
                models.BackupHistory.node_id == largest.c.node_id,
                models.BackupHistory.deduplicated_size == largest.c.max_dedup,
            ),
        )
        .filter(is_success)
        .all()
    )
    base_original, base_dedup, base_nodes = backup_stats.base_archive_totals(base_rows)

    disk = repo_usage.disk_usage()

    return schemas.GlobalStatsResponse(
        total_nodes=db.query(models.Node).count(),
        nodes_with_archives=nodes_with_archives,
        total_archives=total_archives,
        successful_archives=successful,
        failed_archives=total_archives - successful,
        success_rate=backup_stats.success_rate(successful, total_archives),
        total_original_size_bytes=total_original,
        total_deduplicated_size_bytes=total_dedup,
        base_original_size_bytes=base_original,
        base_deduplicated_size_bytes=base_dedup,
        base_nodes=base_nodes,
        saved_space_bytes=max(0, base_original - base_dedup),
        deduplication_ratio=backup_stats.deduplication_ratio(base_original, base_dedup),
        repo_size_bytes=repo_usage.repo_size_bytes(),
        **disk,
    )


@router.get("/insights", response_model=schemas.StatsInsightsResponse)
def get_insights(
    days: int = Query(default=30, ge=1, le=365, description="Trailing window to analyse"),
    db: Session = Depends(get_db),
):
    """Reliability, throughput, window pressure and capacity over a window."""
    now = datetime.utcnow()
    since = now - timedelta(days=days)

    nodes = db.query(models.Node).all()
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    # Categorising old failures used to happen here, which meant a GET wrote
    # to the database — up to 500 rows, reading each one's log_output, with
    # concurrent dashboard loads all doing it over overlapping row sets. It is
    # now a daily task (tasks.backfill_error_categories_task); rows it has not
    # reached yet simply render as uncategorised, which the panel already
    # handles.

    # Only the window is loaded as rows. The previous version fetched every
    # backup_history row ever written and filtered to the window in Python —
    # the `days` parameter did no work in the database at all, and
    # ix_backup_history_timestamp went unused. At 2000 nodes that is hundreds
    # of thousands of tuples per request on an endpoint the Archives page hits
    # on every mount and after every delete.
    #
    # log_output is deliberately absent: it is the only large column on the
    # table and none of the numbers below need it.
    in_window = (
        db.query(
            models.BackupHistory.node_id,
            models.BackupHistory.status,
            models.BackupHistory.timestamp,
            models.BackupHistory.error_category,
            models.BackupHistory.original_size,
            models.BackupHistory.deduplicated_size,
            models.BackupHistory.avg_speed_mbps,
            models.BackupHistory.max_speed_mbps,
            models.BackupHistory.duration_seconds,
        )
        .filter(models.BackupHistory.timestamp >= since)
        .order_by(models.BackupHistory.timestamp.desc())
        .all()
    )

    # The handful of facts that genuinely span a node's whole history — when it
    # last succeeded, how many runs have failed since, how much it has ever
    # contributed — are aggregated in the database instead.
    lifetime = _lifetime_stats(db)

    return schemas.StatsInsightsResponse(
        window_days=days,
        generated_at=now,
        reliability=_reliability(nodes, groups, lifetime, in_window, now, since),
        speed=_speed(nodes, groups, in_window),
        duration=_duration(nodes, groups, in_window),
        capacity=_capacity(nodes, lifetime, in_window, days),
    )


def _lifetime_stats(db: Session) -> dict:
    """Per-node facts that need all of history, computed as grouped SQL.

    Returns node_id -> dict(last_success, contributed, archives,
    failure_streak, last_error_category).
    """
    is_success = models.BackupHistory.status == "SUCCESS"
    stats: dict = defaultdict(lambda: {
        "last_success": None,
        "contributed": 0,
        "archives": 0,
        "failure_streak": 0,
        "last_error_category": None,
    })

    # 1. Last success, lifetime contribution and archive count, per node.
    for node_id, last_success, contributed, archives in db.query(
        models.BackupHistory.node_id,
        func.max(case((is_success, models.BackupHistory.timestamp))),
        func.coalesce(
            func.sum(case((is_success, models.BackupHistory.deduplicated_size), else_=0)), 0
        ),
        func.coalesce(func.sum(case((is_success, 1), else_=0)), 0),
    ).group_by(models.BackupHistory.node_id):
        entry = stats[node_id]
        entry["last_success"] = last_success
        entry["contributed"] = int(contributed or 0)
        entry["archives"] = int(archives or 0)

    # 2. Consecutive failures since the last success. Equivalent to walking a
    #    newest-first list until the first SUCCESS, which is what
    #    backup_stats.failure_streak does over in-memory rows.
    last_ok = (
        db.query(
            models.BackupHistory.node_id.label("node_id"),
            func.max(models.BackupHistory.timestamp).label("last_success"),
        )
        .filter(is_success)
        .group_by(models.BackupHistory.node_id)
        .subquery()
    )
    for node_id, streak in (
        db.query(models.BackupHistory.node_id, func.count(models.BackupHistory.id))
        .outerjoin(last_ok, last_ok.c.node_id == models.BackupHistory.node_id)
        .filter(models.BackupHistory.status != "SUCCESS")
        .filter(or_(
            last_ok.c.last_success.is_(None),
            models.BackupHistory.timestamp > last_ok.c.last_success,
        ))
        .group_by(models.BackupHistory.node_id)
    ):
        stats[node_id]["failure_streak"] = int(streak or 0)

    # 3. The most recent categorised failure per node.
    newest_fail = (
        db.query(
            models.BackupHistory.node_id.label("node_id"),
            func.max(models.BackupHistory.timestamp).label("ts"),
        )
        .filter(models.BackupHistory.status != "SUCCESS",
                models.BackupHistory.error_category.isnot(None))
        .group_by(models.BackupHistory.node_id)
        .subquery()
    )
    for node_id, category in (
        db.query(models.BackupHistory.node_id, models.BackupHistory.error_category)
        .join(newest_fail, and_(
            models.BackupHistory.node_id == newest_fail.c.node_id,
            models.BackupHistory.timestamp == newest_fail.c.ts,
        ))
        .filter(models.BackupHistory.status != "SUCCESS",
                models.BackupHistory.error_category.isnot(None))
    ):
        stats[node_id]["last_error_category"] = category

    return stats


def _group_for(node, groups) -> Optional[models.BackupGroup]:
    return groups.get(node.group_id) if node.group_id else None


def _reliability(nodes, groups, lifetime, in_window, now, since) -> schemas.ReliabilitySection:
    runs_in_window = defaultdict(int)
    for row in in_window:
        runs_in_window[row.node_id] += 1

    successful = sum(1 for row in in_window if row.status == "SUCCESS")
    total = len(in_window)

    stale_nodes = []
    failing_nodes = []
    never_succeeded = 0

    for node in nodes:
        stats = lifetime.get(node.id)

        # A node with no history at all has nothing to report yet. Counting it
        # as a failure would light the panel up for every freshly added node,
        # which is exactly the noise that makes an alert get ignored.
        if stats is None:
            continue

        last_success = stats["last_success"]
        if last_success is None:
            never_succeeded += 1

        group = _group_for(node, groups)
        interval = backup_stats.expected_interval_days(group.interval if group else None)
        age = backup_stats.days_since(last_success, now)
        streak = stats["failure_streak"]

        entry = schemas.NodeReliability(
            node_id=node.id,
            hostname=node.hostname,
            ip_address=node.ip_address,
            group_name=group.name if group else None,
            last_success_at=last_success,
            days_since_success=round(age, 1) if age is not None else None,
            expected_interval_days=interval,
            consecutive_failures=streak,
            last_error_category=stats["last_error_category"],
            runs_in_window=runs_in_window.get(node.id, 0),
            is_stale=backup_stats.is_stale(age, interval),
        )

        if entry.is_stale:
            stale_nodes.append(entry)
        if streak > 0:
            failing_nodes.append(entry)

    # Worst first: never-succeeded nodes sort last by age, so treat them as
    # infinitely old rather than letting None fall to the bottom.
    stale_nodes.sort(key=lambda e: (e.days_since_success is None, e.days_since_success or 0), reverse=True)
    failing_nodes.sort(key=lambda e: e.consecutive_failures, reverse=True)

    top = backup_stats.top_counts(
        (row.error_category or backup_stats.FailureCategory.UNKNOWN
         for row in in_window if row.status != "SUCCESS"),
        limit=_TOP_N,
    )

    return schemas.ReliabilitySection(
        total_runs=total,
        successful_runs=successful,
        failed_runs=total - successful,
        success_rate=backup_stats.success_rate(successful, total),
        nodes_total=len(nodes),
        nodes_never_succeeded=never_succeeded,
        nodes_stale=len(stale_nodes),
        stale_nodes=stale_nodes[:_TOP_N],
        failing_nodes=failing_nodes[:_TOP_N],
        top_failures=[schemas.FailureCount(category=c, count=n) for c, n in top],
    )


def _speed(nodes, groups, in_window) -> schemas.SpeedSection:
    samples = defaultdict(list)
    for row in in_window:
        if row.status == "SUCCESS" and row.avg_speed_mbps is not None:
            samples[row.node_id].append(row)

    all_avg = [row.avg_speed_mbps for rows in samples.values() for row in rows]

    entries = []
    capped = 0
    for node in nodes:
        rows = samples.get(node.id)
        if not rows:
            continue

        group = _group_for(node, groups)
        limit_kib, source = transfer_speed.resolve_rate_limit(
            node.upload_rate_limit, group.upload_rate_limit if group else None
        )
        limit_mbps = transfer_speed.kib_s_to_mbps(limit_kib) if limit_kib else None
        peak = max((r.max_speed_mbps for r in rows if r.max_speed_mbps is not None), default=None)
        binding = transfer_speed.limit_is_binding(peak, limit_mbps)
        if binding:
            capped += 1

        entries.append(schemas.NodeSpeed(
            node_id=node.id,
            hostname=node.hostname,
            runs=len(rows),
            median_mbps=_round(backup_stats.median([r.avg_speed_mbps for r in rows])),
            max_mbps=_round(peak),
            limit_kib=limit_kib or None,
            limit_source=source,
            limit_mbps=_round(limit_mbps),
            limit_binding=binding,
        ))

    # Slowest first — that is the list worth reading.
    entries.sort(key=lambda e: e.median_mbps if e.median_mbps is not None else float("inf"))

    return schemas.SpeedSection(
        measured_runs=len(all_avg),
        median_mbps=_round(backup_stats.median(all_avg)),
        p10_mbps=_round(backup_stats.percentile(all_avg, 10)),
        p90_mbps=_round(backup_stats.percentile(all_avg, 90)),
        slowest_nodes=entries[:_TOP_N],
        capped_nodes=capped,
    )


def _duration(nodes, groups, in_window) -> schemas.DurationSection:
    samples = defaultdict(list)
    for row in in_window:
        if row.status == "SUCCESS" and row.duration_seconds is not None:
            samples[row.node_id].append(row.duration_seconds)

    all_durations = [d for values in samples.values() for d in values]

    entries = []
    at_risk = 0
    for node in nodes:
        values = samples.get(node.id)
        if not values:
            continue

        group = _group_for(node, groups)
        minutes = backup_stats.window_minutes(
            group.start_time if group else None,
            group.end_time if group else None,
        )
        # The worst run is what decides whether a node fits: a median inside
        # the window is no comfort if one run in four overruns it.
        worst = max(values)
        usage = backup_stats.window_usage(worst, minutes)
        risky = usage is not None and usage >= backup_stats.WINDOW_WARN_FRACTION
        if risky:
            at_risk += 1

        entries.append(schemas.NodeDuration(
            node_id=node.id,
            hostname=node.hostname,
            runs=len(values),
            median_seconds=_round(backup_stats.median(values)),
            max_seconds=_round(worst),
            group_name=group.name if group else None,
            window_minutes=minutes,
            window_usage=_round(usage, 3),
            at_risk=risky,
        ))

    entries.sort(key=lambda e: e.max_seconds or 0, reverse=True)

    return schemas.DurationSection(
        measured_runs=len(all_durations),
        median_seconds=_round(backup_stats.median(all_durations)),
        p90_seconds=_round(backup_stats.percentile(all_durations, 90)),
        nodes_at_risk=at_risk,
        longest_nodes=entries[:_TOP_N],
    )


def _capacity(nodes, lifetime, in_window, days) -> schemas.CapacitySection:
    hostnames = {node.id: node.hostname for node in nodes}

    # Contribution, not occupancy: borg deduplicates across nodes, so the bytes
    # one node's archives reported are not bytes that would be freed by
    # deleting them. It is still the right ranking for "who drives growth".
    #
    # Summed in SQL (see _lifetime_stats) rather than by walking every archive
    # row: this is a lifetime figure, so doing it in Python meant loading the
    # entire table on every request.
    contributed = {
        node_id: s["contributed"]
        for node_id, s in lifetime.items() if s["contributed"]
    }
    total_contributed = sum(contributed.values())
    consumers = [
        schemas.NodeConsumption(
            node_id=node_id,
            hostname=hostnames.get(node_id, "—"),
            bytes=value,
            share=round(value / total_contributed, 4) if total_contributed else None,
            archives=lifetime[node_id]["archives"],
        )
        for node_id, value in contributed.items()
    ]
    consumers.sort(key=lambda c: c.bytes, reverse=True)

    by_day = defaultdict(int)
    for row in in_window:
        if row.status == "SUCCESS" and row.timestamp:
            by_day[row.timestamp.date()] += row.deduplicated_size or 0

    inflow = backup_stats.daily_inflow_bytes(by_day, days)
    disk = repo_usage.disk_usage()
    free = disk["disk_free_bytes"]

    return schemas.CapacitySection(
        repo_size_bytes=repo_usage.repo_size_bytes(),
        daily_inflow_bytes=_round(inflow),
        days_until_full=_round(backup_stats.days_until_full(free, inflow), 1),
        projected_full_date=backup_stats.projected_full_date(datetime.utcnow(), free, inflow),
        top_consumers=consumers[:_TOP_N],
        **disk,
    )


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)
