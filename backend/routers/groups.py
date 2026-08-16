import calendar
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import date, datetime, timezone, timedelta
import models
import schemas
from database import get_db
from tasks import run_backup_task

# Recurrence maths is shared with the scheduler so the projection below cannot
# drift from what actually runs.
from core.schedule_slots import (
    get_tzinfo,
    parse_window,
    week_of_month,
)
from core import repo_capacity, schedule_projection, scheduler

from auth import require_admin

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
        orchestrator_behind_nat=payload.orchestrator_behind_nat,
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
    group.orchestrator_behind_nat = payload.orchestrator_behind_nat
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

    # Respect the group's concurrency limit. This used to fire every node in
    # the group at once, bypassing the limit the scheduler works hard to
    # honour — on a large group that meant hundreds of simultaneous borg
    # streams over one uplink, each too slow to finish. Nodes past the limit
    # are left to the scheduler, which will pick them up as slots free.
    limit = group.concurrency_limit or 5
    already_running = sum(
        1 for n in nodes if scheduler.is_backup_lock_live(n.id)
    )
    free_slots = max(0, limit - already_running)
    to_trigger = nodes[:free_slots]

    task_ids = []
    for node in to_trigger:
        task = run_backup_task.delay(node.id, comment=f"Manual trigger for group: {group.name}")
        task_ids.append(task.id)

    deferred = len(nodes) - len(to_trigger)
    from database import log_user_action
    log_user_action(
        db, current_user.username, "Backup Group",
        f"Triggered manual backups for {len(to_trigger)} node(s) in group "
        f"'{group.name}'" + (f"; {deferred} deferred to the scheduler" if deferred else ""),
        request,
    )

    message = f"Triggered manual backups for {len(to_trigger)} node(s) in group '{group.name}'."
    if deferred:
        message += (
            f" {deferred} more queued behind the group's concurrency limit of "
            f"{limit}; the scheduler will start them as slots free up."
        )
    return {
        "message": message,
        "task_ids": task_ids,
        "deferred": deferred,
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

    A group's usable concurrency is capped by the number of repositories its
    nodes are actually spread across — not by `BORG_SHARD_COUNT`. Shards are
    assigned `node_id % SHARD_COUNT`, which knows nothing about group
    membership, so a group of five nodes can land entirely on one repository
    and serialize behind a single lock while the others sit idle. Reporting
    capacity the storage cannot deliver is how a plan that overruns its window
    looks fine on the calendar.
    """
    target_tz = get_tzinfo('Browser Local', db)
    now_target = datetime.now(target_tz)

    day_load = [0] * 24
    week_load = [0] * 7
    month_load = [0] * 4
    day_hours = [0.0] * 24
    week_hours = [0.0] * 7
    month_hours = [0.0] * 4

    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    today = now_target.date()
    week_start = today - timedelta(days=today.weekday())        # Monday
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    month_days = [date(today.year, today.month, d) for d in range(1, days_in_month + 1)]

    # One walk for both calendars; the same date appearing in each is projected
    # once and bucketed twice.
    runs = schedule_projection.project_runs(
        db, sorted(set(week_days) | set(month_days)), target_tz
    )

    week_index = {d: i for i, d in enumerate(week_days)}

    # gid -> day -> runs, so a group's real concurrency can be read off the
    # repositories its nodes occupy rather than assumed.
    per_group_day: dict = {}

    for run in runs:
        if run.day in week_index:
            i = week_index[run.day]
            week_load[i] += 1
            week_hours[i] += run.hours

            if run.day == today:
                day_load[run.run_at.hour] += 1
                day_hours[run.run_at.hour] += run.hours

        if run.day.year == today.year and run.day.month == today.month:
            w = week_of_month(run.day.day)
            month_load[w - 1] += 1
            month_hours[w - 1] += run.hours

        per_group_day.setdefault(run.group_id, {}).setdefault(run.day, []).append(run)

    # --- per-group verdict: does the busiest day fit the window? ---
    group_fit = []
    for gid, by_day in per_group_day.items():
        group = groups[gid]
        window = parse_window(group.start_time, group.end_time)
        window_hours = window.duration_minutes / 60.0

        busiest_day_runs: list = []
        busiest_hours = 0.0
        for day_runs in by_day.values():
            hours = sum(r.hours for r in day_runs)
            if hours > busiest_hours:
                busiest_hours, busiest_day_runs = hours, day_runs

        concurrency = group.concurrency_limit or 5
        if group.upload_rate_limit:
            concurrency = min(concurrency, max(1, group.upload_rate_limit // 2048))
        # Capped by the repositories this group's nodes genuinely occupy on its
        # busiest night. Borg holds a repository's lock for the whole of
        # `borg create`, so two of a group's nodes sharing one repository run
        # strictly one after the other however many repositories exist
        # elsewhere. Capping by SHARD_COUNT instead assumed a spread that the
        # id-based assignment does not provide.
        occupied = repo_capacity.distinct_shards_on(busiest_day_runs)
        if occupied:
            concurrency = min(concurrency, occupied)

        capacity_hours = window_hours * concurrency
        # A figure is a guess only when nothing measured went into it. A group
        # without a rate limit whose nodes have run before is no longer one.
        has_estimate = bool(group.upload_rate_limit) or any(
            r.measured for r in busiest_day_runs
        )

        group_fit.append({
            "group_id": gid,
            "group_name": group.name,
            "nodes_per_run": len(busiest_day_runs),
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
