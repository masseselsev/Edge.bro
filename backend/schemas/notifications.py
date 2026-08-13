"""Alerts and their delivery preferences."""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
from schemas.base import UTCModel


class AlertResponse(UTCModel):
    id: int
    module: str
    node_id: Optional[int] = None
    node_hostname: Optional[str] = None
    dedup_key: str
    severity: str
    status: str
    title: str
    detail: Optional[dict] = None
    first_seen: datetime
    last_seen: datetime
    resolved_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None

class NotificationPreferences(BaseModel):
    telegram_enabled: bool = False
    min_severity: str = Field(default="WATCH", pattern="^(WATCH|ALERT)$")

class NotificationTestResult(BaseModel):
    success: bool
    detail: str

class NotificationStatus(BaseModel):
    telegram_configured: bool
