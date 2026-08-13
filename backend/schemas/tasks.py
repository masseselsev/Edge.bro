"""Background task logs, system logs and the audit trail."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class TaskLogSummaryResponse(BaseModel):
    id: str
    task_type: str
    status: str
    node_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class PaginatedTaskLogResponse(BaseModel):
    items: List[TaskLogSummaryResponse]
    total: int
    page: int
    limit: int
    pages: int

class TaskLogResponse(BaseModel):
    id: str
    task_type: str
    status: str
    node_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    log_output: str

    # Set by the single-task endpoint when the caller passes ?since=. The
    # console polls once a second, so it asks only for the part it does not
    # already have; these let it splice the tail on and notice a log that was
    # reset underneath it.
    log_offset: int = 0
    log_length: Optional[int] = None

    class Config:
        from_attributes = True

class SystemLogResponse(BaseModel):
    id: int
    level: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True

class AuditLogResponse(BaseModel):
    id: int
    username: str
    action: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
