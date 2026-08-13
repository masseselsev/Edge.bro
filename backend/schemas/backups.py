"""Backup history, archive contents, and restore requests."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class BackupHistoryResponse(BaseModel):
    id: int
    node_id: int
    archive_name: str
    timestamp: datetime
    original_size: int
    deduplicated_size: int
    status: str
    log_output: Optional[str] = None
    comment: Optional[str] = None
    # Transfer throughput to the repository, Mbit/s. None for older rows.
    avg_speed_mbps: Optional[float] = None
    max_speed_mbps: Optional[float] = None
    duration_seconds: Optional[float] = None
    error_category: Optional[str] = None

    class Config:
        from_attributes = True

class PaginatedBackupHistoryResponse(BaseModel):
    history: List[BackupHistoryResponse]
    total: int
    page: int
    limit: int
    pages: int

class ArchiveFileInfo(BaseModel):
    path: str
    size: int
    mtime: Optional[str] = None
    mode: Optional[str] = None
    is_dir: bool = False

class ArchiveFileListResponse(BaseModel):
    archive_name: str
    files: List[ArchiveFileInfo]

class ArchiveFileContentResponse(BaseModel):
    path: str
    is_text: bool
    size: int
    content: Optional[str] = None
    message: Optional[str] = None

class BackupTriggerRequest(BaseModel):
    comment: Optional[str] = None

class RestoreRequest(BaseModel):
    node_id: int
    archive_name: str
    target_dev: str
    override_mismatch: bool = False
    keep_network_configs: bool = True
    wipe_mac_bindings: bool = False

class PurgeFailedRequest(BaseModel):
    """Which failed history rows to drop. Both filters are optional and AND
    together; sending neither clears every failed record in the fleet."""
    node_id: Optional[int] = None
    before: Optional[datetime] = None

class PurgeFailedResponse(BaseModel):
    deleted: int
    checkpoints_removed: int = 0
