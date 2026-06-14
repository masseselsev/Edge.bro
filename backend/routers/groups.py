import hashlib
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import zoneinfo
import models
import schemas
from database import get_db
from tasks import run_backup_task

router = APIRouter(prefix="/api/groups")

def deterministic_hash(value: str) -> int:
    """
    Computes a deterministic integer hash from a string using MD5.
    Avoids Python's randomized built-in hash().
    """
    return int(hashlib.md5(value.encode('utf-8')).hexdigest(), 16)

def get_tzinfo(tz_name: str, db_session: Session) -> zoneinfo.ZoneInfo:
    if not tz_name or tz_name == 'Browser Local':
        settings = db_session.query(models.Settings).first()
        if settings and settings.timezone and settings.timezone != 'Browser Local':
            tz_name = settings.timezone
        else:
            tz_name = 'UTC'
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo('UTC')

@router.get("", response_model=List[schemas.BackupGroupResponse])
def get_groups(db: Session = Depends(get_db)):
    """
    Retrieves all backup groups.
    """
    return db.query(models.BackupGroup).all()

@router.post("", response_model=schemas.BackupGroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: schemas.BackupGroupCreate, db: Session = Depends(get_db)):
    """
    Creates a new backup group.
    """
    existing = db.query(models.BackupGroup).filter(models.BackupGroup.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A backup group with this name already exists."
        )
    
    group = models.BackupGroup(
        name=payload.name,
        interval=payload.interval,
        target_week=payload.target_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        concurrency_limit=payload.concurrency_limit,
        randomize_days=payload.randomize_days,
        timezone=payload.timezone
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group

@router.put("/{group_id}", response_model=schemas.BackupGroupResponse)
def update_group(group_id: int, payload: schemas.BackupGroupCreate, db: Session = Depends(get_db)):
    """
    Updates configuration parameters of a backup group.
    """
    group = db.query(models.BackupGroup).filter(models.BackupGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup group not found.")
    
    group.name = payload.name
    group.interval = payload.interval
    group.target_week = payload.target_week
    group.start_time = payload.start_time
    group.end_time = payload.end_time
    group.concurrency_limit = payload.concurrency_limit
    group.randomize_days = payload.randomize_days
    group.timezone = payload.timezone
    
    db.commit()
    db.refresh(group)
    return group

@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, db: Session = Depends(get_db)):
    """
    Deletes a backup group. Any nodes in this group will be unassigned.
    """
    group = db.query(models.BackupGroup).filter(models.BackupGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup group not found.")
    
    # Unassign nodes first
    db.query(models.Node).filter(models.Node.group_id == group_id).update({"group_id": None})
    db.delete(group)
    db.commit()

@router.post("/{group_id}/backup-now")
def trigger_group_backup(group_id: int, db: Session = Depends(get_db)):
    """
    Immediately triggers background Borg backups in parallel for all unpaused nodes inside the group.
    """
    group = db.query(models.BackupGroup).filter(models.BackupGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup group not found.")
    
    nodes = db.query(models.Node).filter(
        models.Node.group_id == group_id,
        models.Node.backup_paused == False
    ).all()
    
    task_ids = []
    for node in nodes:
        task = run_backup_task.delay(node.id, comment=f"Manual trigger for group: {group.name}")
        task_ids.append(task.id)
        
    return {
        "message": f"Triggered manual backups for {len(nodes)} node(s) in group '{group.name}'.",
        "task_ids": task_ids
    }

@router.get("/scheduler-load", response_model=schemas.SchedulerLoadResponse)
def get_scheduler_load(db: Session = Depends(get_db)):
    """
    Computes the load distribution:
    - day_load: 24 hourly buckets for today's backup starts
    - week_load: 7 daily buckets for the current week's backup starts (Mon-Sun)
    - month_load: 4 weekly buckets for the current month's backup starts (Week 1-4)
    """
    # Load settings timezone or fallback to UTC
    target_tz = get_tzinfo('Browser Local', db)
    now_target = datetime.now(target_tz)
    
    # 1. Initialize empty buckets
    day_load = [0] * 24
    week_load = [0] * 7
    month_load = [0] * 4
    
    # Determine current context metrics in target timezone
    current_day_of_week = now_target.weekday()  # Monday = 0, Sunday = 6
    current_week_of_month = min(4, ((now_target.day - 1) // 7) + 1)
    current_month = now_target.month
    
    nodes = db.query(models.Node).filter(
        models.Node.group_id.isnot(None),
        models.Node.backup_paused == False
    ).all()
    
    # Pre-fetch groups
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}
    
    for node in nodes:
        group = groups.get(node.group_id)
        if not group:
            continue
            
        group_tz = get_tzinfo(group.timezone, db)
        node_hash = deterministic_hash(node.hostname)
        
        # Day of week staggering in group's local timezone (0 to 6)
        if group.randomize_days:
            day_index = node_hash % 7
        else:
            day_index = 0  # Default to Monday if not randomized
            
        # Parse window hours
        try:
            start_h, start_m = map(int, group.start_time.split(":"))
            end_h = int(group.end_time.split(":")[0])
        except Exception:
            start_h, start_m, end_h = 2, 0, 5
            
        window_duration_hours = end_h - start_h
        if window_duration_hours <= 0:
            window_duration_hours += 24
        window_duration_hours = max(1, window_duration_hours)
        
        # Stagger offsets
        hour_offset = node_hash % window_duration_hours
        minute_offset = (node_hash // window_duration_hours) % 60
        
        scheduled_local_hour = (start_h + hour_offset) % 24
        scheduled_local_minute = (start_m + minute_offset) % 60
        
        # Calculate when it is scheduled in target timezone
        # Let's target this week. Find diff between scheduled local day and current local day
        days_diff = day_index - now_target.weekday()
        # Scheduled run time in group's local timezone
        local_run_dt = datetime.now(group_tz).replace(
            hour=scheduled_local_hour,
            minute=scheduled_local_minute,
            second=0,
            microsecond=0
        ) + timedelta(days=days_diff)
        
        # Convert local run datetime to target timezone
        run_dt_target = local_run_dt.astimezone(target_tz)
        target_day_index = run_dt_target.weekday()
        target_hour = run_dt_target.hour
        target_week_of_month = min(4, ((run_dt_target.day - 1) // 7) + 1)
        target_month = run_dt_target.month
        
        # Evaluate Scheduler Recurrence Active Windows using local scheduled date context
        is_active_this_month = False
        if group.interval == "weekly":
            is_active_this_month = True
        elif group.interval == "monthly":
            is_active_this_month = True
        elif group.interval == "quarterly":
            current_quarter_start = ((target_month - 1) // 3) * 3 + 1
            if target_month == current_quarter_start:
                is_active_this_month = True
        elif group.interval == "yearly":
            if target_month == 1:
                is_active_this_month = True
                
        # --- 1. Month Load (Weeks 1 to 4) ---
        for w in range(1, 5):
            runs_in_week = False
            if group.interval == "weekly":
                runs_in_week = True
            elif group.interval == "monthly" and group.target_week == w:
                runs_in_week = True
            elif group.interval == "quarterly" and group.target_week == w and is_active_this_month:
                runs_in_week = True
            elif group.interval == "yearly" and group.target_week == w and is_active_this_month:
                runs_in_week = True
                
            if runs_in_week:
                month_load[w - 1] += 1
                
        # --- 2. Week Load (Days 0 to 6 of current week) ---
        runs_this_week = False
        if group.interval == "weekly":
            runs_this_week = True
        elif group.interval == "monthly" and group.target_week == target_week_of_month:
            runs_this_week = True
        elif group.interval == "quarterly" and group.target_week == target_week_of_month and is_active_this_month:
            runs_this_week = True
        elif group.interval == "yearly" and group.target_week == target_week_of_month and is_active_this_month:
            runs_this_week = True
            
        if runs_this_week:
            # We must verify if the target day is within the current week scope
            # Simple check: we index by target_day_index (0 to 6)
            week_load[target_day_index] += 1
            
            # --- 3. Day Load (Hours 0 to 23 of today) ---
            if target_day_index == current_day_of_week:
                day_load[target_hour] += 1
                
    return {
        "day_load": day_load,
        "week_load": week_load,
        "month_load": month_load
    }
