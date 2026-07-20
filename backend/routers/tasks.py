from typing import List, Optional
from math import ceil
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, defer
from database import get_db
import models
import schemas
from routers.users import require_admin, require_kiosk_or_admin

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
def get_task_logs(task_id: str, db: Session = Depends(get_db), current_user = Depends(require_kiosk_or_admin)):
    """
    Fetches execution logs and status of a background task.
    """
    task = db.query(models.TaskLog).filter(models.TaskLog.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task
