import calendar
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import models
import schemas
from database import get_db
from tasks import run_backup_task

# Recurrence maths is shared with the scheduler so the projection below cannot
# drift from what actually runs.
from core.schedule_slots import (
    deterministic_hash,
    get_tzinfo,
    is_scheduled_on,
    node_slot,
    parse_window,
    week_of_month,
)
from core.schedule_estimate import DEFAULT_BACKUP_MINUTES, estimate_node_backup_minutes

from routers.users import require_admin

router = APIRouter(prefix="/api/groups", dependencies=[Depends(require_admin)])

@router.get("", response_model=List[schemas.BackupGroupResponse])
def get_groups(db: Session = Depends(get_db)):
    """
    Retrieves all backup groups.
    """
    return db.query(models.BackupGroup).all()

@router.post("", response_model=schemas.BackupGroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: schemas.BackupGroupCreate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
        timezone=payload.timezone,
        override_retention=payload.override_retention,
        retention_policy=payload.retention_policy.model_dump() if payload.retention_policy else None,
        upload_rate_limit=payload.upload_rate_limit,
        compression=payload.compression,
        checkpoint_interval=payload.checkpoint_interval,
        cpu_quota=payload.cpu_quota
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    from database import log_user_action
    log_user_action(db, current_user.username, "Create Backup Group", f"Created backup group '{group.name}' (interval={group.interval})", request)
    return group

@router.put("/{group_id}", response_model=schemas.BackupGroupResponse)
def update_group(group_id: int, payload: schemas.BackupGroupCreate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
    group.override_retention = payload.override_retention
    group.retention_policy = payload.retention_policy.model_dump() if payload.retention_policy else None
    group.upload_rate_limit = payload.upload_rate_limit
    group.compression = payload.compression
    group.checkpoint_interval = payload.checkpoint_interval
    group.cpu_quota = payload.cpu_quota
    
    db.commit()
    db.refresh(group)
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Backup Group", f"Updated configuration of backup group '{group.name}'", request)
    return group

@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
    from database import log_user_action
    log_user_action(db, current_user.username, "Delete Backup Group", f"Deleted backup group '{group.name}'", request)

@router.post("/{group_id}/backup-now")
def trigger_group_backup(group_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
        
    from database import log_user_action
    log_user_action(db, current_user.username, "Backup Group", f"Triggered manual backups for {len(nodes)} node(s) in group '{group.name}'", request)

    return {
        "message": f"Triggered manual backups for {len(nodes)} node(s) in group '{group.name}'.",
        "task_ids": task_ids
    }

@router.get("/scheduler-load", response_model=schemas.SchedulerLoadResponse)
def get_scheduler_load(db: Session = Depends(get_db)):
    """
    Projects upcoming scheduler load.

    Buckets are reported both as node counts (day_load / week_load / month_load)
    and as estimated transfer hours (day_hours / week_hours / month_hours). On
    slow links a count says nothing useful — five nodes may be twenty minutes or
    twenty hours of transfer — so `group_fit` additionally reports whether each
    group's busiest day actually fits inside its execution window.

    Recurrence is evaluated with core.schedule_slots, the same module the real
    scheduler uses, so this projection cannot drift from what will actually run.
    """
    target_tz = get_tzinfo('Browser Local', db)
    now_target = datetime.now(target_tz)

    day_load = [0] * 24
    week_load = [0] * 7
    month_load = [0] * 4
    day_hours = [0.0] * 24
    week_hours = [0.0] * 7
    month_hours = [0.0] * 4

    nodes = db.query(models.Node).filter(
        models.Node.group_id.isnot(None),
        models.Node.backup_paused == False
    ).all()

    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    today = now_target.date()
    week_start = today - timedelta(days=today.weekday())        # Monday
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # gid -> weekday index -> [node_count, hours]
    per_group_day: dict = {}

    for node in nodes:
        group = groups.get(node.group_id)
        if not group:
            continue

        group_tz = get_tzinfo(group.timezone, db)
        window = parse_window(group.start_time, group.end_time)
        slot = node_slot(group, node.hostname, window)
        start_h, start_m = divmod(window.start_mins, 60)

        est_minutes = estimate_node_backup_minutes(db, node.id, group.upload_rate_limit)
        est_h = (est_minutes if est_minutes is not None else DEFAULT_BACKUP_MINUTES) / 60.0

        bucket = per_group_day.setdefault(group.id, {})

        # --- current week (Mon..Sun), evaluated on the group's own calendar ---
        for i in range(7):
            d = week_start + timedelta(days=i)
            window_start_local = datetime(d.year, d.month, d.day, start_h, start_m, tzinfo=group_tz)
            if not is_scheduled_on(group, node.hostname, window_start_local, window):
                continue

            week_load[i] += 1
            week_hours[i] += est_h
            slot_counts = bucket.setdefault(i, [0, 0.0])
            slot_counts[0] += 1
            slot_counts[1] += est_h

            # --- today's hourly distribution, shown in the dashboard timezone ---
            if d == today:
                run_local = window_start_local + timedelta(minutes=slot.stagger_offset_mins)
                run_target = run_local.astimezone(target_tz)
                day_load[run_target.hour] += 1
                day_hours[run_target.hour] += est_h

        # --- current month, bucketed into weeks 1-4 ---
        for day_num in range(1, days_in_month + 1):
            window_start_local = datetime(today.year, today.month, day_num, start_h, start_m, tzinfo=group_tz)
            if not is_scheduled_on(group, node.hostname, window_start_local, window):
                continue
            w = week_of_month(day_num)
            month_load[w - 1] += 1
            month_hours[w - 1] += est_h

    # --- per-group verdict: does the busiest day fit the window? ---
    group_fit = []
    for gid, bucket in per_group_day.items():
        group = groups[gid]
        window = parse_window(group.start_time, group.end_time)
        window_hours = window.duration_minutes / 60.0

        concurrency = group.concurrency_limit or 5
        if group.upload_rate_limit:
            concurrency = min(concurrency, max(1, group.upload_rate_limit // 2048))

        busiest_count, busiest_hours = 0, 0.0
        for count, hours in bucket.values():
            if hours > busiest_hours:
                busiest_count, busiest_hours = count, hours

        capacity_hours = window_hours * concurrency
        has_estimate = bool(group.upload_rate_limit)

        group_fit.append({
            "group_id": gid,
            "group_name": group.name,
            "nodes_per_run": busiest_count,
            "est_hours": round(busiest_hours, 2),
            "window_hours": round(window_hours, 2),
            "concurrency": concurrency,
            "capacity_hours": round(capacity_hours, 2),
            "fits": busiest_hours <= capacity_hours,
            "rate_limit_kib": group.upload_rate_limit,
            "has_estimate": has_estimate,
        })

    group_fit.sort(key=lambda g: g["group_name"])

    return {
        "day_load": day_load,
        "week_load": week_load,
        "month_load": month_load,
        "day_hours": [round(h, 2) for h in day_hours],
        "week_hours": [round(h, 2) for h in week_hours],
        "month_hours": [round(h, 2) for h in month_hours],
        "group_fit": group_fit,
    }
