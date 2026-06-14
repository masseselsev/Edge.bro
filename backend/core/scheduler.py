import os
import hashlib
from datetime import datetime, timedelta, timezone
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

def check_and_trigger_backups(db: Session):
    """
    Evaluates all nodes and their assigned groups. Triggers backups in Celery
    if they are scheduled, retrying or flagged for manual "Backup Today" execution.
    """
    now = datetime.utcnow()  # Naive UTC datetime to match db timestamps

    # 1. Fetch all nodes that are assigned to a group and not paused
    nodes = db.query(models.Node).filter(
        models.Node.group_id.isnot(None),
        models.Node.backup_paused == False
    ).all()

    # Pre-fetch groups
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    # Concurrency tracker
    group_running_counts = {}
    for gid in groups:
        group_running_counts[gid] = 0

    # Count currently running backups per group
    for node in nodes:
        if redis_client.get(f"backup_running:{node.id}"):
            group_running_counts[node.group_id] = group_running_counts.get(node.group_id, 0) + 1

    for node in nodes:
        group = groups.get(node.group_id)
        if not group:
            continue

        # Determine group timezone
        group_tz = get_tzinfo(group.timezone, db)
        
        # Current local time for the group
        now_local = now.replace(tzinfo=timezone.utc).astimezone(group_tz)
        local_day_of_week = now_local.weekday()
        local_week_of_month = min(4, ((now_local.day - 1) // 7) + 1)
        local_month = now_local.month
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
        elif start_mins > end_mins:
            in_window = (local_mins >= start_mins or local_mins < end_mins)
        else:
            in_window = True

        # Calculate scheduling metrics
        node_hash = deterministic_hash(node.hostname)
        if group.randomize_days:
            day_index = node_hash % 7
        else:
            day_index = 0

        # Calculate time stagger within window
        window_duration_hours = end_h - start_h
        if window_duration_hours <= 0:
            window_duration_hours += 24
        window_duration_hours = max(1, window_duration_hours)

        hour_offset = node_hash % window_duration_hours
        minute_offset = (node_hash // window_duration_hours) % 60

        scheduled_hour = (start_h + hour_offset) % 24
        scheduled_minute = (start_m + minute_offset) % 60

        # Determine if scheduled for today
        is_scheduled_today = False
        if group.interval == "weekly":
            if day_index == local_day_of_week:
                is_scheduled_today = True
        elif group.interval == "monthly":
            if local_week_of_month == group.target_week and day_index == local_day_of_week:
                is_scheduled_today = True
        elif group.interval == "quarterly":
            current_quarter_start = ((local_month - 1) // 3) * 3 + 1
            if local_month == current_quarter_start:
                if local_week_of_month == group.target_week and day_index == local_day_of_week:
                    is_scheduled_today = True
        elif group.interval == "yearly":
            if local_month == 1:
                if local_week_of_month == group.target_week and day_index == local_day_of_week:
                    is_scheduled_today = True

        # Determine window start datetime in UTC (naive)
        window_start_local = now_local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        if start_mins > local_mins:
            window_start_local -= timedelta(days=1)
        window_start_dt = window_start_local.astimezone(timezone.utc).replace(tzinfo=None)

        # 1. Handle Out-Of-Window missed backup marking
        if not in_window:
            # Check if this node was supposed to run in the window that just completed
            was_supposed_to_run = is_scheduled_today or node.backup_today
            
            # Check if current time is past the window end
            is_past_window = False
            if start_mins < end_mins:
                is_past_window = (local_mins >= end_mins)
            elif start_mins > end_mins:
                is_past_window = (local_mins >= end_mins and local_mins < start_mins)

            if is_past_window and was_supposed_to_run:
                # Did we complete a successful backup since this window started?
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

        # 2. Inside Window: Evaluate execution
        # Check if already completed successfully in this window
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

        # Check if currently executing
        if redis_client.get(f"backup_running:{node.id}"):
            continue

        # Determine trigger conditions
        should_run = False
        if node.backup_today or node.missed_window:
            should_run = True
        elif is_scheduled_today:
            # Check if we are at/past staggered hour and minute
            if now_local.hour > scheduled_hour or (now_local.hour == scheduled_hour and now_local.minute >= scheduled_minute):
                should_run = True

        if should_run:
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

            # Check concurrency limit
            current_running = group_running_counts.get(group.id, 0)
            if current_running >= group.concurrency_limit:
                logger.warning(f"Concurrency limit ({group.concurrency_limit}) reached for group {group.name}. Delaying trigger for {node.hostname}.")
                continue

            # Increment group concurrency count
            group_running_counts[group.id] = current_running + 1
            
            # Set Redis lock and trigger Celery task
            logger.info(f"Triggering automatic scheduled backup for node: {node.hostname}")
            redis_client.setex(f"backup_running:{node.id}", 14400, "1")
            run_backup_task.delay(node.id, comment=f"Automated scheduler execution (Group: {group.name})")
