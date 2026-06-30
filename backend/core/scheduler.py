import os
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import redis
from sqlalchemy.orm import Session
import models
from backup_tasks import run_backup_task
import zoneinfo

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)

def deterministic_hash(value: str) -> int:
    """
    Computes a deterministic integer hash from a string using MD5.
    Avoids Python's randomized built-in hash().
    """
    return int(hashlib.md5(value.encode('utf-8')).hexdigest(), 16)

def get_tzinfo(tz_name: str, db: Session) -> zoneinfo.ZoneInfo:
    if not tz_name or tz_name == 'Browser Local':
        settings = db.query(models.Settings).first()
        if settings and settings.timezone and settings.timezone != 'Browser Local':
            tz_name = settings.timezone
        else:
            tz_name = 'UTC'
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo('UTC')

def check_and_trigger_backups(db: Session, now: Optional[datetime] = None):
    """
    Evaluates all nodes and their assigned groups. Triggers backups in Celery
    if they are scheduled, retrying or flagged for manual "Backup Today" execution.
    It queues backups sequentially to distribute load and optimize bandwidth.
    """
    if now is None:
        now = datetime.utcnow()  # Naive UTC datetime to match db timestamps

    # 1. Fetch all nodes that are assigned to a group and not paused
    nodes = db.query(models.Node).filter(
        models.Node.group_id.isnot(None),
        models.Node.backup_paused == False
    ).all()

    if not nodes:
        return

    # Pre-fetch groups
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    # Concurrency tracker (currently running counts)
    group_running_counts = {}
    for gid in groups:
        group_running_counts[gid] = 0

    # Count currently running backups per group
    for node in nodes:
        if redis_client.get(f"backup_running:{node.id}"):
            group_running_counts[node.group_id] = group_running_counts.get(node.group_id, 0) + 1

    # Precompute group-level variables to optimize execution speed and implement dynamic concurrency
    group_cache = {}
    for gid, group in groups.items():
        # Determine group timezone
        group_tz = get_tzinfo(group.timezone, db)
        
        # Current local time for the group
        now_local = now.replace(tzinfo=timezone.utc).astimezone(group_tz)
        local_hour = now_local.hour
        local_minute = now_local.minute
        local_mins = local_hour * 60 + local_minute

        # Parse group time window (e.g. "02:00" -> hour=2, minute=0)
        try:
            start_h, start_m = map(int, group.start_time.split(":"))
            end_h, end_m = map(int, group.end_time.split(":"))
        except Exception:
            logger.error(f"Invalid window start/end time for group {group.name}: {group.start_time} - {group.end_time}")
            start_h, start_m = 2, 0
            end_h, end_m = 5, 0

        # Determine if current time falls within group's execution window
        start_mins = start_h * 60 + start_m
        end_mins = end_h * 60 + end_m

        if start_mins < end_mins:
            in_window = (start_mins <= local_mins < end_mins)
            window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            window_duration_minutes = end_mins - start_mins
        elif start_mins > end_mins:
            in_window = (local_mins >= start_mins or local_mins < end_mins)
            if local_mins >= start_mins:
                window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            else:
                window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0) - timedelta(days=1)
            window_duration_minutes = (1440 - start_mins) + end_mins
        else:
            in_window = True
            window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
            window_duration_minutes = 1440

        # Extract calendar logic relative to window start date to handle crossing midnight correctly
        local_day_of_week = window_start_local.weekday()
        local_week_of_month = min(4, ((window_start_local.day - 1) // 7) + 1)
        local_month = window_start_local.month
        
        # 10min / 30min test intervals override window start & durations
        if group.interval == "10min":
            window_start_dt = now - timedelta(minutes=10)
        elif group.interval == "30min":
            window_start_dt = now - timedelta(minutes=30)
        else:
            window_start_dt = window_start_local.astimezone(timezone.utc).replace(tzinfo=None)

        # Base concurrency limit (default to 5 if not set or 0)
        base_concurrency = group.concurrency_limit or 5

        # Capping concurrency based on upload rate limit (assume 2 MiB/s = 2048 KiB/s per stream)
        if group.upload_rate_limit:
            bandwidth_concurrency = max(1, group.upload_rate_limit // 2048)
            base_concurrency = min(base_concurrency, bandwidth_concurrency)

        # Calculate remaining time in current window
        if in_window:
            elapsed_minutes = (now_local - window_start_local).total_seconds() / 60
            remaining_minutes = max(1.0, window_duration_minutes - elapsed_minutes)
        else:
            remaining_minutes = 0.0

        group_cache[gid] = {
            "group": group,
            "now_local": now_local,
            "in_window": in_window,
            "window_start_local": window_start_local,
            "window_start_dt": window_start_dt,
            "start_h": start_h,
            "start_m": start_m,
            "end_h": end_h,
            "start_mins": start_mins,
            "end_mins": end_mins,
            "local_day_of_week": local_day_of_week,
            "local_week_of_month": local_week_of_month,
            "local_month": local_month,
            "base_concurrency": base_concurrency,
            "remaining_minutes": remaining_minutes,
            "window_duration_minutes": window_duration_minutes
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
        in_window = g_data["in_window"]
        window_start_local = g_data["window_start_local"]
        window_start_dt = g_data["window_start_dt"]
        start_h = g_data["start_h"]
        start_m = g_data["start_m"]
        end_h = g_data["end_h"]
        start_mins = g_data["start_mins"]
        end_mins = g_data["end_mins"]
        local_day_of_week = g_data["local_day_of_week"]
        local_week_of_month = g_data["local_week_of_month"]
        local_month = g_data["local_month"]
        base_concurrency = g_data["base_concurrency"]
        remaining_minutes = g_data["remaining_minutes"]
        window_duration_minutes = g_data["window_duration_minutes"]

        # Calculate duration window hours
        window_duration_hours = max(1, window_duration_minutes // 60)

        # 1. Out of Window marking
        if not in_window:
            for node in group_nodes:
                node_hash = deterministic_hash(node.hostname)
                if group.randomize_days:
                    day_index = node_hash % 7
                else:
                    day_index = 0

                is_scheduled_today = False
                if group.interval in ("10min", "30min"):
                    is_scheduled_today = True
                elif group.interval == "weekly":
                    is_scheduled_today = (day_index == local_day_of_week)
                elif group.interval == "monthly":
                    is_scheduled_today = (local_week_of_month == group.target_week and day_index == local_day_of_week)
                elif group.interval == "quarterly":
                    current_quarter_start = ((local_month - 1) // 3) * 3 + 1
                    is_scheduled_today = (local_month == current_quarter_start and local_week_of_month == group.target_week and day_index == local_day_of_week)
                elif group.interval == "yearly":
                    is_scheduled_today = (local_month == 1 and local_week_of_month == group.target_week and day_index == local_day_of_week)

                was_supposed_to_run = is_scheduled_today or node.backup_today
                
                is_past_window = False
                if start_mins < end_mins:
                    is_past_window = (local_mins >= end_mins)
                elif start_mins > end_mins:
                    is_past_window = (local_mins >= end_mins and local_mins < start_mins)

                if is_past_window and was_supposed_to_run:
                    # If currently executing, allow completion; do not mark missed yet!
                    if redis_client.get(f"backup_running:{node.id}"):
                        continue

                    # Did we complete a successful backup since window started?
                    successful_backup = db.query(models.BackupHistory).filter(
                        models.BackupHistory.node_id == node.id,
                        models.BackupHistory.status == "SUCCESS",
                        models.BackupHistory.timestamp >= window_start_dt
                    ).first()
                    
                    if not successful_backup:
                        if not node.missed_window:
                            logger.info(f"Node {node.hostname} missed its execution window. Marking missed_window=True.")
                            node.missed_window = True
                            node.backup_today = False
                            db.commit()
            continue

        # 2. Inside Window: Filter pending nodes and sort by stagger offset to build the queue
        pending_nodes_stagger = []
        for node in group_nodes:
            # Check if already completed successfully in this window
            node_hash = deterministic_hash(node.hostname)
            if group.randomize_days:
                day_index = node_hash % 7
            else:
                day_index = 0

            is_scheduled_today = False
            if group.interval in ("10min", "30min"):
                is_scheduled_today = True
                stagger_offset_mins = 0
            else:
                if group.interval == "weekly":
                    is_scheduled_today = (day_index == local_day_of_week)
                elif group.interval == "monthly":
                    is_scheduled_today = (local_week_of_month == group.target_week and day_index == local_day_of_week)
                elif group.interval == "quarterly":
                    current_quarter_start = ((local_month - 1) // 3) * 3 + 1
                    is_scheduled_today = (local_month == current_quarter_start and local_week_of_month == group.target_week and day_index == local_day_of_week)
                elif group.interval == "yearly":
                    is_scheduled_today = (local_month == 1 and local_week_of_month == group.target_week and day_index == local_day_of_week)

                # Stagger offset for scheduling queue ordering
                hour_offset = node_hash % window_duration_hours
                minute_offset = (node_hash // window_duration_hours) % 60
                stagger_offset_mins = hour_offset * 60 + minute_offset

            # Node needs to run inside the window?
            needs_to_run = is_scheduled_today or node.backup_today or node.missed_window

            if not needs_to_run:
                continue

            # Verify if already finished successfully
            successful_backup = db.query(models.BackupHistory).filter(
                models.BackupHistory.node_id == node.id,
                models.BackupHistory.status == "SUCCESS",
                models.BackupHistory.timestamp >= window_start_dt
            ).first()

            if successful_backup:
                # Successfully completed! Clean up flags
                if node.backup_today or node.missed_window:
                    logger.info(f"Backup succeeded for {node.hostname}. Resetting backup_today/missed_window flags.")
                    node.backup_today = False
                    node.missed_window = False
                    db.commit()
                continue

            # If not running yet, add to pending list with stagger offset for sorting
            if not redis_client.get(f"backup_running:{node.id}"):
                pending_nodes_stagger.append((node, stagger_offset_mins))

        # If no pending nodes, we are done with this group
        if not pending_nodes_stagger:
            continue

        # Dynamic Concurrency: adjust concurrency to guarantee completion before window end.
        # Assume an average backup takes 30 minutes to complete.
        pending_count = len(pending_nodes_stagger)
        required_concurrency = math.ceil(pending_count * 30 / remaining_minutes)
        effective_concurrency = max(base_concurrency, required_concurrency)

        # Sort queue sequentially by stagger offset (earlier staggered nodes first)
        pending_nodes_stagger.sort(key=lambda x: x[1])

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

            # Enforce hourly retry limit if failed recently in this window
            latest_fail = db.query(models.BackupHistory).filter(
                models.BackupHistory.node_id == node.id,
                models.BackupHistory.status == "FAILED",
                models.BackupHistory.timestamp >= window_start_dt
            ).order_by(models.BackupHistory.timestamp.desc()).first()

            if latest_fail:
                time_since_fail = (now - latest_fail.timestamp).total_seconds()
                if time_since_fail < 3600:
                    # Enforce hourly delay
                    continue
                else:
                    logger.info(f"Retrying failed backup for {node.hostname} (last failed at {latest_fail.timestamp})")

            # Set redis lock to mark running (this is released by the Celery task on completion)
            redis_client.setex(f"backup_running:{node.id}", 86400, "1")
            group_running_counts[gid] += 1
            triggered_count += 1

            # Trigger backup task
            logger.info(f"Queue scheduler triggering backup for node {node.hostname} (Group limit: {effective_concurrency}, running: {group_running_counts[gid]})")
            run_backup_task.delay(node.id, comment=f"Automated scheduler execution (Group: {group.name})")
