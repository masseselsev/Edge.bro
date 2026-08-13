"""Technician kiosks and the enrollment handshake."""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from schemas.base import UTCModel


class KioskBase(BaseModel):
    name: Optional[str] = None
    kiosk_id: Optional[str] = None
    contact: Optional[str] = None
    comment: Optional[str] = None
    target_ip: Optional[str] = None
    rebuild_required: bool = False

class KioskCreate(KioskBase):
    pass

class KioskResponse(UTCModel, KioskBase):
    id: int
    key: str
    status: str
    ip_address: Optional[str] = None
    ssh_pub_key: Optional[str] = None
    auth_token: Optional[str] = None
    iso_exists: Optional[bool] = None
    iso_path: Optional[str] = None
    iso_name: Optional[str] = None
    iso_size: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    is_online: Optional[bool] = None
    iso_built_at: Optional[datetime] = None
    is_rebuilding: bool = False
    payload_outdated: Optional[bool] = False

class HandshakeRequest(BaseModel):
    kiosk_id: Optional[str] = None
    uuid: Optional[str] = None
    key: str
    ssh_pub_key: str

class KioskEnrollRequest(BaseModel):
    kiosk_id: Optional[str] = None
    uuid: Optional[str] = None
    name: str
    contact: str
    comment: str
    ssh_pub_key: str

class KioskIssueRequest(BaseModel):
    name: str
    contact: str
    comment: Optional[str] = None
    target_ip: Optional[str] = None

class KioskUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    comment: Optional[str] = None

class KioskIpUpdateRequest(BaseModel):
    target_ip: str

class AutoHandshakeRequest(BaseModel):
    kiosk_id: Optional[str] = None
    uuid: Optional[str] = None
    ssh_pub_key: str
    payload_hash: Optional[str] = None

class RequestActivationRequest(BaseModel):
    token: str
