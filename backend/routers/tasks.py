from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas

router = APIRouter(prefix="/api/tasks")

@router.get("", response_model=List[schemas.TaskLogResponse])
def get_all_tasks(db: Session = Depends(get_db)):
    """
    Lists all background task execution logs ordered by created_at desc.
    """
    return db.query(models.TaskLog).order_by(models.TaskLog.created_at.desc()).all()

@router.get("/debug-logs", response_model=List[schemas.SystemLogResponse])
def get_debug_logs(db: Session = Depends(get_db)):
    """
    Fetches all system/application execution logs ordered by created_at desc.
    """
    return db.query(models.SystemLog).order_by(models.SystemLog.created_at.desc()).limit(200).all()

@router.get("/{task_id}", response_model=schemas.TaskLogResponse)
def get_task_logs(task_id: str, db: Session = Depends(get_db)):
    """
    Fetches execution logs and status of a background task.
    """
    task = db.query(models.TaskLog).filter(models.TaskLog.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task
