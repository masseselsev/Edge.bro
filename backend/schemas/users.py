"""Accounts, authentication, and the SSH key audit."""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = None
    telegram_id: Optional[str] = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=6)
    comment: Optional[str] = None
    is_admin_plus: Optional[bool] = False

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    telegram_id: Optional[str] = None
    password: Optional[str] = None
    comment: Optional[str] = None
    is_admin_plus: Optional[bool] = None

class UserSelfUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    telegram_id: Optional[str] = None
    password: Optional[str] = None

class UserResponse(UserBase):
    id: int
    is_superadmin: bool
    is_admin_plus: bool
    comment: Optional[str] = None

    class Config:
        from_attributes = True

class LoginPayload(BaseModel):
    username: str
    password: str

class SshKeyFindingResponse(BaseModel):
    id: int
    location: str
    host: str
    node_id: Optional[int] = None
    fingerprint: str
    key_type: Optional[str] = None
    comment: Optional[str] = None
    options: Optional[str] = None
    classification: str
    reason: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    resolved_at: Optional[datetime] = None
    orphan_since: Optional[datetime] = None
    orphan_scan_count: int
    pruned_at: Optional[datetime] = None

    class Config:
        from_attributes = True
