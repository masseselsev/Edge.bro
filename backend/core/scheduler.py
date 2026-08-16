import os
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import time
from sqlalchemy import func
from sqlalchemy.orm import Session
import models
from backup_tasks import run_backup_task
from core.clock import utcnow
import zoneinfo

logger = logging.getLogger(__name__)

from core.redis_client import make_client as make_redis_client

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = make_redis_client(REDIS_URL)

from core import repo_paths
from core.schedule_estimate import backup_lock_ttl_seconds, estimate_group_backup_minutes
from core.schedule_slots import (  # noqa: F401  (re-exported for existing importers)
    deterministic_hash,
    get_tzinfo,
    is_scheduled_on,
    node_slot,
    parse_window,
    TEST_INTERVALS,
    week_of_month,
)


def running_keys_for(node_ids) -> dict:
    """The `backup_running:` value for many nodes, in one round trip.

    The scheduler tick asks this question about every node it is considering,
    several times over, and used to issue a Redis GET each time — thousands of
    round trips per minute on a large fleet.
    """
    node_ids = list(node_ids)
    if not node_ids:
        return {}
    keys = [f"backup_running:{n}" for n in node_ids]
    try:
        values = redis_client.mget(keys)
    except Exception as e:
        logger.warning(f"Could not read backup locks in bulk: {e}")
        return {}
    return dict(zip(node_ids, values))


def is_backup_lock_live(node_id: int, raw=None, prefetched: bool = False) -> bool:
    """Whether a backup is genuinely still running for this node.

    The redis key alone is not enough: if a worker dies the key lingers until
    it expires, blocking the node for hours. The key carries the Celery task
    id, so ask Celery whether that task actually finished and clear the stale
    key if so.

    `prefetched` lets a caller supply a value already fetched via
    running_keys_for, avoiding a per-node GET. The Celery check below is only
    reached for nodes that actually hold a key, so it stays bounded by the
    number of running backups rather than by fleet size.
    """
    if not prefetched:
        raw = redis_client.get(f"backup_running:{node_id}")
    if not raw:
        return False

    value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    task_id = value.split(":", 1)[1] if ":" in value else None
    if not task_id:
        return True  # legacy value without a task id — assume live

    try:
        from celery_app import celery_app
        if celery_app.AsyncResult(task_id).ready():
            logger.info(f"Clearing stale backup lock for node {node_id}: task {task_id} already finished.")
            redis_client.delete(f"backup_running:{node_id}")
            return False
    except Exception as e:
        logger.warning(f"Could not verify backup task state for node {node_id}: {e}")

    return True

def check_and_trigger_backups(db: Session, now: Optional[datetime] = None):
    """
    Evaluates all nodes and their assigned groups. Triggers backups in Celery
    if they are scheduled, retrying or flagged for manual "Backup Today" execution.
    It queues backups sequentially to distribute load and optimize bandwidth.
    """
    if now is None:
        now = utcnow()  # Naive UTC datetime to match db timestamps

    # 1. Fetch all nodes that are assigned to a group, not paused, and fully ready
    nodes = db.query(models.Node).filter(
        models.Node.group_id.isnot(None),
        models.Node.backup_paused == False,
        models.Node.status == "READY"
    ).all()

    if not nodes:
        return

    # Pre-fetch groups
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    # Every Redis lock this tick will need, in one round trip. Read once and
    # reused for the running counts, the missed-window check and the
    # admission check below; each of those used to issue its own GET per node,
    # so a tick cost several thousand round trips on a large fleet.
    running_raw = running_keys_for(n.id for n in nodes)

    # Concurrency tracker (currently running counts)
    group_running_counts = {}
    for gid in groups:
        group_running_counts[gid] = 0

    # Count currently running backups per group.
    #
    # Through is_backup_lock_live, not on the presence of the key. A worker
    # that died leaves its `backup_running:` key behind until the TTL expires,
    # and that TTL is now sized from the node's own history — hours, on a slow
    # link. Counting the corpse throttled the entire group for that long, and
    # nothing in the logs said why. Admission control below already used the
    # live check; these two disagreeing was the bug.
    #
    # It costs nothing extra at fleet scale: the Celery round trip inside only
    # happens for nodes that actually hold a key, so it is bounded by the
    # number of running backups rather than by the size of the fleet.
    for node in nodes:
        raw = running_raw.get(node.id)
        if raw and is_backup_lock_live(node.id, raw=raw, prefetched=True):
            group_running_counts[node.group_id] = group_running_counts.get(node.group_id, 0) + 1

    # Precompute group-level variables to optimize execution speed and implement dynamic concurrency
    group_cache = {}
    for gid, group in groups.items():
        # Determine group timezone
        group_tz = get_tzinfo(group.timezone, db)

        # Current local time for the group
        now_local = now.replace(tzinfo=timezone.utc).astimezone(group_tz)
        local_mins = now_local.hour * 60 + now_local.minute

        window = parse_window(group.start_time, group.end_time)
        in_window = window.contains(local_mins)

        start_h, start_m = divmod(window.start_mins, 60)
        window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        # A window that crosses midnight and is currently in its "after midnight"
        # half actually started yesterday.
        if window.start_mins > window.end_mins and local_mins < window.end_mins:
            window_start_local -= timedelta(days=1)

        # 10min / 30min test intervals override window start & durations
        if group.interval == "10min":
            window_start_dt = now - timedelta(minutes=10)
        elif group.interval == "30min":
            window_start_dt = now - timedelta(minutes=30)
        else:
            window_start_dt = window_start_local.astimezone(timezone.utc).replace(tzinfo=None)

        # Base concurrency limit (default to 5 if not set or 0)
        base_concurrency = group.concurrency_limit or 5

        # Hard ceiling derived from the group's upload rate limit. Kept
        # SEPARATE from base_concurrency: the dynamic "finish before the window
        # closes" logic below may raise concurrency above the configured limit,
        # but it must never raise it above what the link can physically carry.
        #
        # 2048 is KiB/s per concurrent stream, i.e. an assumption that one
        # backup can sustain about 2 MiB/s. It is a floor on realistic
        # single-stream throughput over the DSL and LTE links these nodes sit
        # behind, not a measurement, and it is the only place the scheduler
        # converts bandwidth into a number of streams:
        #
        #   too low  -> the cap is generous, streams contend, every backup in
        #               the group slows down together and some miss the window
        #   too high -> the cap is stingy, the link idles, and the group takes
        #               longer than it needed to
        #
        # Erring low is the safer direction, which is why it is set where it
        # is. core/transfer_speed.py records what each backup actually
        # achieved; if the fleet's measured rate settles well above this, this
        # is the constant to revisit.
        BYTES_PER_STREAM_KIB = 2048
        bandwidth_cap = None
        if group.upload_rate_limit:
            bandwidth_cap = max(1, group.upload_rate_limit // BYTES_PER_STREAM_KIB)

        # Calculate remaining time in current window
        if in_window:
            elapsed_minutes = (now_local - window_start_local).total_seconds() / 60
            remaining_minutes = max(1.0, window.duration_minutes - elapsed_minutes)
        else:
            remaining_minutes = 0.0

        group_cache[gid] = {
            "group": group,
            "now_local": now_local,
            "local_mins": local_mins,
            "in_window": in_window,
            "window": window,
            "window_start_local": window_start_local,
            "window_start_dt": window_start_dt,
            "base_concurrency": base_concurrency,
            "bandwidth_cap": bandwidth_cap,
            "remaining_minutes": remaining_minutes,
        }

    # Group nodes by group_id for queue and batch processing
    nodes_by_group = {}
    for node in nodes:
        nodes_by_group.setdefault(node.group_id, []).append(node)

    # Process each group
    for gid, group_nodes in nodes_by_group.items():
        g_data = group_cache.get(gid)
        if not g_data:
            continue

        group = g_data["group"]
        now_local = g_data["now_local"]
        local_mins = g_data["local_mins"]
        in_window = g_data["in_window"]
        window = g_data["window"]
        window_start_local = g_data["window_start_local"]
        window_start_dt = g_data["window_start_dt"]
        base_concurrency = g_data["base_concurrency"]
        bandwidth_cap = g_data["bandwidth_cap"]
        remaining_minutes = g_data["remaining_minutes"]

        # Which of this group's nodes already have a successful backup inside
        # the current window. One grouped query for the group, rather than the
        # per-node `.first()` this replaces in both branches below.
        group_node_ids = [n.id for n in group_nodes]
        succeeded_in_window = {
            row[0] for row in db.query(models.BackupHistory.node_id)
            .filter(
                models.BackupHistory.node_id.in_(group_node_ids),
                models.BackupHistory.status == "SUCCESS",
                models.BackupHistory.timestamp >= window_start_dt,
            )
            .group_by(models.BackupHistory.node_id)
        }

        # 1. Out of Window marking
        if not in_window:
            # Judged against THIS group's own local clock — see local_mins above.
            is_past_window = window.is_past(local_mins)
            for node in group_nodes:
                is_scheduled_today = is_scheduled_on(group, node.hostname, window_start_local, window)
                was_supposed_to_run = is_scheduled_today or node.backup_today

                if is_past_window and was_supposed_to_run:
                    # If currently executing, allow completion; do not mark missed yet!
                    if running_raw.get(node.id):
                        continue

                    # Did we complete a successful backup since window started?
                    if node.id not in succeeded_in_window:
                        if not node.missed_window:
                            logger.info(f"Node {node.hostname} missed its execution window. Marking missed_window=True.")
                            node.missed_window = True
                            node.backup_today = False
                            db.commit()
            continue

        # 2. Inside Window: Filter pending nodes and sort by stagger offset to build the queue
        pending_nodes_stagger = []
        for node in group_nodes:
            is_scheduled_today = is_scheduled_on(group, node.hostname, window_start_local, window)
            stagger_offset_mins = node_slot(group, node.hostname, window).stagger_offset_mins

            # Node needs to run inside the window?
            needs_to_run = is_scheduled_today or node.backup_today or node.missed_window

            if not needs_to_run:
                continue

            # Verify if already finished successfully
            if node.id in succeeded_in_window:
                # Successfully completed! Clean up flags
                if node.backup_today or node.missed_window:
                    logger.info(f"Backup succeeded for {node.hostname}. Resetting backup_today/missed_window flags.")
                    node.backup_today = False
                    node.missed_window = False
                    db.commit()
                continue

            # Don't burn a concurrency slot and a retry cooldown on a node that
            # the last ping says is down — common on sites with unstable power.
            # None means "never pinged", which we treat as worth attempting.
            if node.last_ping_status is False:
                # debug, not info: this fires once per unreachable node per
                # tick — every 60 seconds, forever — and each INFO record
                # becomes a row in system_logs. A site with 200 nodes down
                # was writing ~288k rows a day to say nothing had changed.
                logger.debug(f"Skipping {node.hostname}: last ping failed, node appears offline.")
                continue

            # If not running yet, add to pending list with stagger offset for sorting
            if not is_backup_lock_live(
                node.id, raw=running_raw.get(node.id), prefetched=True
            ):
                pending_nodes_stagger.append((node, stagger_offset_mins))

        # If no pending nodes, we are done with this group
        if not pending_nodes_stagger:
            continue

        # Dynamic Concurrency: raise parallelism when the remaining window is
        # too short to finish the queue sequentially.
        pending_count = len(pending_nodes_stagger)
        avg_backup_minutes = estimate_group_backup_minutes(db, group, [n for n, _ in pending_nodes_stagger])
        required_concurrency = math.ceil(pending_count * avg_backup_minutes / remaining_minutes)
        effective_concurrency = max(base_concurrency, required_concurrency)

        # The link's carrying capacity is an absolute ceiling: finishing "on
        # time" is worthless if every stream is too slow to complete at all.
        if bandwidth_cap is not None and effective_concurrency > bandwidth_cap:
            logger.info(
                f"Group {group.name}: capping concurrency {effective_concurrency} -> {bandwidth_cap} "
                f"to stay within the configured {group.upload_rate_limit} KiB/s upload limit."
            )
            effective_concurrency = bandwidth_cap

        # So is the number of repositories, for the same reason and more
        # absolutely. Borg holds a repository's lock for the whole of
        # `borg create`, so backups landing on one shard run one at a time no
        # matter how many are dispatched. Anything past this does not run
        # sooner — it occupies a Celery worker doing nothing but waiting out
        # `--lock-wait`, and if the queue ahead of it is long enough it times
        # out and fails a backup that would have succeeded next tick.
        #
        # This is why the dynamic raise above cannot help a single-shard
        # install: there is no parallelism to buy.
        if effective_concurrency > repo_paths.SHARD_COUNT:
            logger.info(
                f"Group {group.name}: capping concurrency {effective_concurrency} -> "
                f"{repo_paths.SHARD_COUNT} — one writer per repository, and the fleet "
                f"has {repo_paths.SHARD_COUNT}."
            )
            effective_concurrency = repo_paths.SHARD_COUNT

        # Sort queue sequentially by stagger offset (earlier staggered nodes first)
        pending_nodes_stagger.sort(key=lambda x: x[1])

        # Most recent failure per pending node, for the retry cooldown below.
        # The cooldown differs by interval but is the same for every node in
        # the group, so one grouped query covers the whole queue instead of a
        # `.first()` per node inside the trigger loop.
        if group.interval == "10min":
            retry_cooldown_s = 600
        elif group.interval == "30min":
            retry_cooldown_s = 1800
        else:
            retry_cooldown_s = 3600

        # Use the *wider* lookback so the cooldown is not bypassed when
        # window_start_dt is a short rolling window (e.g. now-10min for
        # "10min" groups). Without this a failure 11 minutes old falls outside
        # window_start_dt, no row is found, and the node retries immediately.
        fail_lookback_dt = min(window_start_dt, now - timedelta(seconds=retry_cooldown_s))
        pending_ids = [n.id for n, _ in pending_nodes_stagger]
        latest_failure_at = dict(
            db.query(
                models.BackupHistory.node_id,
                func.max(models.BackupHistory.timestamp),
            )
            .filter(
                models.BackupHistory.node_id.in_(pending_ids),
                models.BackupHistory.status == "FAILED",
                models.BackupHistory.timestamp >= fail_lookback_dt,
            )
            .group_by(models.BackupHistory.node_id)
            .all()
        )

        # Current running backups in this group
        running_count = group_running_counts.get(gid, 0)
        free_slots = effective_concurrency - running_count

        if free_slots <= 0:
            # Queue is full, delay start
            continue

        # Trigger backups in order of the sorted queue up to the number of free slots
        triggered_count = 0
        for node, stagger_offset_mins in pending_nodes_stagger:
            if triggered_count >= free_slots:
                break

            # Retry cooldown, resolved from the batch fetched before this loop.
            last_failed_at = latest_failure_at.get(node.id)
            if last_failed_at:
                time_since_fail = (now - last_failed_at).total_seconds()
                if time_since_fail < retry_cooldown_s:
                    # Enforce cooldown delay — do not retry yet
                    continue
                else:
                    logger.info(f"Retrying failed backup for {node.hostname} (last failed at {last_failed_at})")


            # Trigger backup task
            logger.info(f"Queue scheduler triggering backup for node {node.hostname} (Group limit: {effective_concurrency}, running: {group_running_counts[gid]})")
            task = run_backup_task.delay(node.id, comment=f"Automated scheduler execution (Group: {group.name})")
            
            # Set redis lock to mark running (this is released by the Celery task on completion)
            # Store the current timestamp and task ID in the lock value.
            # TTL is sized from the node's own history so a slow backup cannot
            # outlive its lock and get killed by its own replacement.
            lock_ttl = backup_lock_ttl_seconds(db, node.id, group.upload_rate_limit)
            redis_client.setex(f"backup_running:{node.id}", lock_ttl, f"{int(time.time())}:{task.id}")
            group_running_counts[gid] += 1
            triggered_count += 1
