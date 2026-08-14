"""Scheduled collection of node health telemetry.

Ties the pieces together: drain the node's buffer over SSH, difference the
counters, fit whatever windows the data supports, score the drive, and store
the results. Everything analytical lives in `core.telemetry`, `core.thermal`
and `core.smart`; everything about reaching the node lives in `core.harvest`.
This module is the wiring and the persistence.

One node's failure never affects another. A harvest that cannot reach its
node, or whose drive stopped answering, records what it did get and moves on —
across a thousand roadside units some are always unreachable, and a sweep that
aborted on the first would never finish.
"""
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from celery_app import celery_app
from core import harvest as harvest_io
from core import smart, telemetry, thermal
from core.db_session import session_scope
from models import Node, Settings, SmartSnapshot, TelemetryRollup, ThermalFit
import tasks

#: Length of one fitting window. Long enough to see a 35-minute heatsink
#: settle several times over, short enough that ambient does not wander far
#: within it — the linear drift term absorbs a slow ramp, not a whole day.
FIT_WINDOW_SECONDS = 4 * 3600

#: Rollups are cheap; a node buffering for months should not produce a single
#: enormous insert. Written in batches of this many.
ROLLUP_BATCH = 500

#: Harvests dispatched per wave, and the gap between waves. The sweep runs
#: hourly and a harvest takes seconds, so spreading 2000 nodes over ~13 minutes
#: still finishes long before the next sweep while keeping the number of
#: concurrent SSH sessions bounded.
HARVEST_BATCH_SIZE = int(os.getenv("HARVEST_BATCH_SIZE", "50"))
HARVEST_BATCH_INTERVAL_SECONDS = int(os.getenv("HARVEST_BATCH_INTERVAL_SECONDS", "20"))


def resolve_setting(node: Node, settings: Optional[Settings], name: str, fallback):
    """Per-node override, then the global default, then a hard fallback.

    NULL on the node means inherit, which is deliberately distinct from an
    explicit value that happens to equal the current global — the same
    precedence the rest of the node settings use.
    """
    node_value = getattr(node, name, None)
    if node_value is not None:
        return node_value
    if settings is not None:
        global_value = getattr(settings, name, None)
        if global_value is not None:
            return global_value
    return fallback


def monitoring_due(node: Node, settings: Optional[Settings], now: datetime) -> bool:
    """Whether this node is due for a scheduled harvest.

    A node that has never been harvested is always due. Disabled nodes never
    are — but note that provisioning and post-backup harvests call
    `harvest_node` directly and deliberately bypass this, since those are
    events rather than schedule.
    """
    if not resolve_setting(node, settings, "monitoring_enabled", True):
        return False
    if node.last_harvest_at is None:
        return True
    interval_days = resolve_setting(node, settings, "monitoring_interval_days", 30)
    return now - node.last_harvest_at >= timedelta(days=max(1, int(interval_days)))


def _store_rollups(db, node_id: int, rollups) -> int:
    """Upsert rollup buckets, so a re-harvest updates rather than duplicates.

    Overlap is normal: a harvest that failed after draining but before
    committing leaves the node's next buffer covering ground already seen.
    """
    if not rollups:
        return 0

    starts = [r.bucket_start for r in rollups]
    existing = {
        row.bucket_start: row
        for row in db.query(TelemetryRollup)
        .filter(TelemetryRollup.node_id == node_id, TelemetryRollup.bucket_start.in_(starts))
        .all()
    }

    written = 0
    for index, rollup in enumerate(rollups, start=1):
        row = existing.get(rollup.bucket_start)
        if row is None:
            row = TelemetryRollup(node_id=node_id, bucket_start=rollup.bucket_start)
            db.add(row)
        row.sample_count = rollup.sample_count
        row.power_w_mean = rollup.power_w_mean
        row.power_w_max = rollup.power_w_max
        row.cpu_temp_c_mean = rollup.cpu_temp_c_mean
        row.cpu_temp_c_max = rollup.cpu_temp_c_max
        row.board_temp_c_mean = rollup.board_temp_c_mean
        row.ssd_temp_c_mean = rollup.ssd_temp_c_mean
        row.cpu_util_mean = rollup.cpu_util_mean
        row.io_service_ms_mean = rollup.io_service_ms_mean
        row.throttled = rollup.throttled
        written += 1
        if index % ROLLUP_BATCH == 0:
            db.flush()

    return written


def _store_fits(db, node_id: int, readings) -> Dict[str, int]:
    """Fit every window the readings support and record the outcome of each.

    Rejected windows are stored too. A node with no theta must be
    distinguishable from a node nobody looked at, and "the load never varied
    enough" is the answer an operator will ask for.
    """
    counts = {"fitted": 0, "rejected": 0}
    windows = telemetry.windows(
        readings, window_s=FIT_WINDOW_SECONDS, min_samples=thermal.MIN_SAMPLES
    )

    for window in windows:
        samples = telemetry.thermal_samples(window)
        if not samples:
            continue

        window_start = window[0].moment
        window_end = window[-1].moment
        if db.query(ThermalFit).filter(
            ThermalFit.node_id == node_id, ThermalFit.window_start == window_start
        ).first():
            continue

        result = thermal.fit(samples)

        normalised = None
        if result.ok and result.mean_temp_c is not None and result.t_ambient_c is not None:
            # Comparing a node against its own past needs theta corrected back
            # to reference conditions; convection genuinely gets more effective
            # as the sink runs hotter above the air around it.
            normalised = thermal.normalise_theta(
                result.theta_c_per_w, result.mean_temp_c - result.t_ambient_c
            )

        db.add(ThermalFit(
            node_id=node_id,
            window_start=window_start,
            window_end=window_end,
            rejection=result.rejection.value,
            n_samples=result.n_samples,
            excitation=round(result.excitation, 4) if result.excitation else result.excitation,
            theta_c_per_w=result.theta_c_per_w,
            theta_normalised=normalised,
            tau_seconds=result.tau_seconds,
            t_ambient_c=result.t_ambient_c,
            mean_temp_c=result.mean_temp_c,
            r_squared=result.r_squared,
        ))
        counts["fitted" if result.ok else "rejected"] += 1

    return counts


def _store_smart(db, node: Node, settings: Optional[Settings], reports: dict, now: datetime) -> int:
    warn_c = int(resolve_setting(node, settings, "smart_temp_warn_c", smart.DEFAULT_TEMP_WARN_C))
    crit_c = int(resolve_setting(node, settings, "smart_temp_crit_c", smart.DEFAULT_TEMP_CRIT_C))

    stored = 0
    for device, report in reports.items():
        reading = smart.parse(report)
        health = smart.score(reading, temp_warn_c=warn_c, temp_crit_c=crit_c)

        db.add(SmartSnapshot(
            node_id=node.id,
            captured_at=now,
            device=device,
            protocol=reading.protocol.value,
            model=reading.model,
            serial=reading.serial,
            firmware=reading.firmware,
            health_passed=reading.health_passed,
            temperature_c=reading.temperature_c,
            power_on_hours=reading.power_on_hours,
            written_bytes=reading.written_bytes,
            percent_used=reading.percent_used,
            score=health.score,
            grade=health.grade.value,
            subscores=[
                {"name": s.name, "score": s.score, "evidence": s.evidence}
                for s in health.subscores
            ],
            overrides=list(health.overrides),
            advisories=list(health.advisories),
            raw=report,
        ))
        stored += 1

    return stored


def harvest_node(node_id: int, task_id: Optional[str] = None) -> Dict[str, Any]:
    """Harvest one node and persist everything it yielded.

    `task_id`, when given, streams progress into the TaskLog so a manual
    harvest from the UI shows its work.
    """
    def log(message: str) -> None:
        if task_id:
            tasks.log_to_task(task_id, message)

    try:
        # Look up the node, let go of the connection, then reach out to it.
        # Draining a node's buffer over SSH takes up to four minutes; a session
        # held across that sits idle in transaction for the whole harvest, and
        # the hourly sweep does this once per node. See core.db_session.
        with session_scope() as db:
            node = db.query(Node).filter(Node.id == node_id).first()
            if not node:
                return {"status": "FAILED", "error": "Node not found"}
            hostname, ip_address, ssh_port = node.hostname, node.ip_address, node.ssh_port

        summary: Dict[str, Any] = {"node": hostname, "status": "SUCCESS"}
        log(f"Harvesting monitoring data from {hostname} ({ip_address})")
        result = harvest_io.harvest(ip_address, ssh_port)

        if not result.reachable:
            log(f"Node unreachable: {'; '.join(result.errors)}")
            return {"status": "FAILED", "node": hostname, "error": "; ".join(result.errors)}

        parsed = telemetry.parse_buffer(result.buffer_text)
        log(f"Telemetry buffer: {parsed.summary()}")
        summary["samples"] = len(parsed.records)
        summary["dropped"] = parsed.dropped
        readings = telemetry.to_readings(parsed.records)

        # Everything the node had to say is now in memory; one session records
        # all of it. Fitting and scoring happen inside because they are pure
        # computation over `readings` — no I/O, no waiting.
        now = datetime.utcnow()
        with session_scope() as db:
            node = db.query(Node).filter(Node.id == node_id).first()
            if not node:
                return {"status": "FAILED", "error": "Node not found"}
            settings = db.query(Settings).first()

            if result.capabilities:
                node.monitoring_capabilities = result.capabilities
                summary["capabilities"] = result.capabilities

            summary["rollups"] = _store_rollups(db, node.id, telemetry.rollups(readings))
            summary.update(_store_fits(db, node.id, readings))
            summary["smart_devices"] = _store_smart(db, node, settings, result.smart_reports, now)
            node.last_harvest_at = now

        if result.capabilities and not result.capabilities.get("rapl"):
            log("No RAPL on this node — SMART will be collected but no thermal model")

        if summary.get("fitted") or summary.get("rejected"):
            log(
                f"Thermal windows: {summary.get('fitted', 0)} fitted, "
                f"{summary.get('rejected', 0)} rejected"
            )

        for device, report in result.smart_reports.items():
            reading = smart.parse(report)
            health = smart.score(reading)
            log(f"{device}: {reading.model or 'unknown'} — score {health.score} {health.grade.value}")

        if result.errors:
            summary["errors"] = result.errors
            summary["status"] = "PARTIAL"
            for error in result.errors:
                log(f"WARNING: {error}")

        log(f"Harvest complete: {summary}")
        return summary
    except Exception as e:
        log(f"Harvest failed: {e}")
        return {"status": "FAILED", "error": str(e)}


# ignore_result is load-bearing, not tidiness. Nothing ever awaits a harvest:
# the UI follows progress through TaskLog, and the sweep dispatches and forgets.
# Storing a result means every dispatch writes to the Redis result backend,
# whose retry policy is a separate 20-attempt loop from the broker's — so a
# briefly unreachable Redis turns a fire-and-forget call into a twenty-second
# block on whatever dispatched it, which for the post-backup harvest is the
# backup task itself.
@celery_app.task(name="tasks.harvest_node_task", ignore_result=True)
def harvest_node_task(node_id: int, task_id: Optional[str] = None) -> Dict[str, Any]:
    return harvest_node(node_id, task_id=task_id)


@celery_app.task(name="tasks.monitoring_sweep_task")
def monitoring_sweep_task() -> Dict[str, Any]:
    """Harvest every node whose interval has elapsed.

    Runs hourly and picks up whatever is due, rather than trying to fire at a
    per-node scheduled instant. With intervals measured in weeks, an hour of
    slack is irrelevant, and a sweep that simply asks "who is overdue?" needs
    no state and recovers by itself from an orchestrator that was down.
    """
    with session_scope() as db:
        settings = db.query(Settings).first()
        now = datetime.utcnow()
        due = [
            node.id for node in db.query(Node).all()
            if monitoring_due(node, settings, now)
        ]

    # Dispatched individually so one slow or unreachable node cannot hold up
    # the rest, and so each retries on its own schedule — but spread over the
    # hour rather than all at once. The first sweep after enabling monitoring
    # on a large fleet finds every node due simultaneously; queueing 2000
    # harvests in one burst puts thousands of SSH sessions behind a worker
    # pool sized for a handful, and starves everything else on that queue.
    for index, node_id in enumerate(due):
        harvest_node_task.apply_async(
            args=[node_id],
            retry=False,
            countdown=(index // HARVEST_BATCH_SIZE) * HARVEST_BATCH_INTERVAL_SECONDS,
        )

    return {"dispatched": len(due)}


@celery_app.task(name="tasks.monitoring_retention_task")
def monitoring_retention_task() -> Dict[str, Any]:
    """Age out bulky monitoring data, keeping the parts worth keeping.

    Three different policies, because the three tables age differently:

    * **Rollups** expire wholesale at the configured window.
    * **SMART snapshots** keep their parsed scalars — those are what the
      history graph plots and they are tiny — but shed their `raw` report,
      which is ~15 KB apiece and would otherwise dwarf everything else in this
      database within a year.
    * **Rejected thermal fits** expire on the same window as rollups. A node
      draining a month of buffer produces ~180 windows per harvest, and on a
      fleet whose load barely varies almost every one of them is a rejection.
      That is 2.16M rows a year across a thousand nodes for records whose
      entire content is "the load never varied enough" — worth keeping long
      enough to diagnose a node, worthless as history.

    Successful fits are deliberately **not** pruned by default. They are the
    long-term degradation trend the whole feature exists to produce, and at
    roughly a tenth the volume of the rejections they are affordable to keep —
    an operator who wants a hard ceiling anyway can set
    `Settings.thermal_fit_retention_days`, checked separately below on its own
    window rather than reusing `telemetry_retention_days`, since keeping years
    of degradation trend while discarding rollups after 90 days is exactly the
    tradeoff this feature is for.
    """
    try:
        with session_scope() as db:
            settings = db.query(Settings).first()
            days = int(getattr(settings, "telemetry_retention_days", None) or 90)
            cutoff = datetime.utcnow() - timedelta(days=days)

            rollups_removed = (
                db.query(TelemetryRollup)
                .filter(TelemetryRollup.bucket_start < cutoff)
                .delete(synchronize_session=False)
            )

            raw_cleared = (
                db.query(SmartSnapshot)
                .filter(SmartSnapshot.captured_at < cutoff, SmartSnapshot.raw.isnot(None))
                .update({SmartSnapshot.raw: None}, synchronize_session=False)
            )

            rejected_removed = (
                db.query(ThermalFit)
                .filter(ThermalFit.window_start < cutoff, ThermalFit.rejection != "OK")
                .delete(synchronize_session=False)
            )

            ok_fits_removed = 0
            thermal_fit_retention_days = getattr(settings, "thermal_fit_retention_days", None)
            if thermal_fit_retention_days:
                ok_cutoff = datetime.utcnow() - timedelta(days=int(thermal_fit_retention_days))
                ok_fits_removed = (
                    db.query(ThermalFit)
                    .filter(ThermalFit.window_start < ok_cutoff, ThermalFit.rejection == "OK")
                    .delete(synchronize_session=False)
                )

        return {
            "rollups_removed": rollups_removed,
            "raw_reports_cleared": raw_cleared,
            "rejected_fits_removed": rejected_removed,
            "ok_fits_removed": ok_fits_removed,
        }
    except Exception as e:
        return {"status": "FAILED", "error": str(e)}
