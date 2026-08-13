from typing import List, Optional
from math import ceil
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, defer
from database import get_db
import models
import schemas
from auth import require_admin, require_kiosk_or_admin

router = APIRouter(prefix="/api/tasks")

@router.get("", response_model=schemas.PaginatedTaskLogResponse)
def get_all_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    db: Session = Depends(get_db), 
    current_user = Depends(require_kiosk_or_admin)
):
    """
    Lists paginated background task execution logs (excluding heavy log_output text) ordered by created_at desc.
    """
    query = db.query(models.TaskLog).options(defer(models.TaskLog.log_output))
    if search:
        s = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                models.TaskLog.id.ilike(s),
                models.TaskLog.task_type.ilike(s),
                models.TaskLog.status.ilike(s)
            )
        )
    
    total = query.count()
    pages = max(1, ceil(total / limit))
    offset = (page - 1) * limit
    items = query.order_by(models.TaskLog.created_at.desc()).offset(offset).limit(limit).all()
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages
    }

@router.get("/debug-logs", response_model=List[schemas.SystemLogResponse])
def get_debug_logs(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Fetches all system/application execution logs ordered by created_at desc.
    """
    return db.query(models.SystemLog).order_by(models.SystemLog.created_at.desc()).limit(500).all()

@router.get("/{task_id}", response_model=schemas.TaskLogResponse)
def get_task_logs(
    task_id: str,
    since: int = Query(0, ge=0, description="Return only log output past this character offset"),
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """Execution status and log output of a background task.

    `since` makes the console poll incremental. The log viewer refreshes once a
    second while a task runs, and re-sending the whole log each time is
    quadratic in its length — a long provision or restore would end up
    transferring hundreds of megabytes to display a few hundred kilobytes.
    The client tracks how much it already has and asks for the remainder.

    `log_length` is always the full length, so a client can tell whether it is
    behind and detect a log that was truncated or replaced underneath it.
    """
    task = db.query(models.TaskLog).filter(models.TaskLog.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    full = task.log_output or ""
    payload = schemas.TaskLogResponse.model_validate(task, from_attributes=True)
    # `since` past the end means the client is ahead of us — a restarted task
    # whose log was reset. Send the whole thing rather than an empty tail.
    payload.log_output = full[since:] if 0 < since <= len(full) else full
    payload.log_offset = since if 0 < since <= len(full) else 0
    payload.log_length = len(full)
    return payload
