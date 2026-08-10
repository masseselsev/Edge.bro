from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone


class UTCModel(BaseModel):
    """Base model: serializes naive datetime as UTC ('Z' suffix), supports ORM mode."""
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: (
                v.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
                if v.tzinfo is None
                else v.isoformat().replace('+00:00', 'Z')
            )
        }
    )


class RetentionPolicySchema(BaseModel):
    type: str = Field(default='interval')  # 'interval', 'count', 'timeframe'
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_last: int = Field(default=5, ge=1)
    within_value: int = Field(default=3, ge=1)
    within_unit: str = Field(default='m')  # 'd', 'w', 'm', 'y'


class ExclusionSchema(BaseModel):
    pattern: str
    comment: str


class CredentialSchema(BaseModel):
    id: str
    username: str
    password: str
    comment: str = ""


class SettingsBase(BaseModel):
    borg_ssh_port: int = Field(default=12345, ge=1, le=65535)
    borg_repo_path: str = Field(default='/data/borg')
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    global_exclusions: List[ExclusionSchema] = Field(default=[])
    orchestrator_ip: str = Field(default='')
    orchestrator_behind_nat: bool = Field(default=False)
    timezone: str = Field(default='Browser Local')
    language: str = Field(default='en')
    retention_policy: Optional[RetentionPolicySchema] = None
    default_compression: str = Field(default='zstd:3')
    default_cpu_quota: Optional[int] = Field(default=30, ge=0, le=400)
    server_ips: Optional[List[str]] = Field(default=[])
    max_kiosk_isos: int = Field(default=5, ge=1)
    server_name: str = Field(default='edge-bro')
    bootstrap_credentials: List[CredentialSchema] = Field(default=[])
    default_credentials_id: Optional[str] = Field(default='')
    server_net_capacity_mbps: int = Field(default=1000, ge=1)


    @field_validator('server_name')
    @classmethod
    def validate_server_name(cls, v: str) -> str:
        import re
        # Used as an ISO filename prefix, so it is normalised to lowercase and
        # kept free of dots and separators.
        v = v.lower()
        if not re.match(r'^[a-z0-9][a-z0-9_-]*$', v):
            raise ValueError(
                "Server name must start with a letter or digit and contain only "
                "letters, numbers, hyphens, and underscores — no dots or spaces."
            )
        return v




class SettingsResponse(SettingsBase):
    id: int
    available_ips: Optional[List[str]] = None
    borg_host_data_path: Optional[str] = None

    class Config:
        from_attributes = True


class BackupGroupBase(BaseModel):
    name: str
    interval: str  # weekly, monthly, quarterly, yearly
    target_week: int = 1
    start_time: str
    end_time: str
    concurrency_limit: int = 5
    randomize_days: bool = True
    timezone: str = Field(default='UTC')
    override_retention: bool = False
    retention_policy: Optional[RetentionPolicySchema] = None
    # None = inherit the global Settings value
    orchestrator_behind_nat: Optional[bool] = Field(
        default=None, description="None = inherit global setting"
    )
    # Resource limits (None = inherit global default / unlimited)
    upload_rate_limit: Optional[int] = Field(default=None, ge=0, description="KiB/s, None = unlimited")
    compression: Optional[str] = Field(default=None, description="e.g. 'zstd:3', None = global default")
    checkpoint_interval: Optional[int] = Field(default=None, ge=0, description="seconds, None = auto-calculate")
    cpu_quota: Optional[int] = Field(default=None, ge=0, le=400, description="% of 1 core, None = no limit")

class BackupGroupCreate(BackupGroupBase):
    pass

class BackupGroupResponse(BackupGroupBase):
    id: int

    class Config:
        from_attributes = True

class NodeCreate(BaseModel):
    hostname: str
    ip_address: str
    ssh_port: int = 22
    bootstrap_user: str = "root"
    bootstrap_password: str
    auto_detect_hostname: Optional[bool] = False
    force_orchestrator_proxy: Optional[bool] = False

class NodeResponse(BaseModel):
    id: int
    hostname: str
    ip_address: str
    ssh_port: int
    status: str
    last_backup: Optional[datetime] = None
    disk_type: str
    network_iface: Optional[str] = None
    efi_uuid: Optional[str] = None
    partition_layout: Optional[List[dict]] = None
    os_version: Optional[str] = None
    next_retry_at: Optional[datetime] = None
    repo_size_bytes: Optional[int] = None
    
    # Scheduler & Automated Backup fields
    group_id: Optional[int] = None
    backup_paused: bool
    backup_today: bool
    missed_window: bool
    
    # Hardware & Software attributes
    cpu_info: Optional[str] = None
    memory_info: Optional[str] = None
    edge_version: Optional[str] = None
    notes: Optional[str] = None
    hasp_runtime_version: Optional[str] = None
    is_backup_running: Optional[bool] = False
    backup_progress: Optional[int] = 0
    backup_task_id: Optional[str] = None
    last_ping_status: Optional[bool] = None
    last_available_at: Optional[datetime] = None
    # None = inherit from the node's group, then the global setting
    orchestrator_behind_nat: Optional[bool] = None
    # KiB/s. None = inherit the group limit, then unlimited.
    upload_rate_limit: Optional[int] = None

    class Config:
        from_attributes = True

class PaginatedNodesResponse(BaseModel):
    nodes: List[NodeResponse]
    total: int
    page: int
    limit: int
    pages: int

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

    class Config:
        from_attributes = True

class RestoreRequest(BaseModel):
    node_id: int
    archive_name: str
    target_dev: str
    override_mismatch: bool = False
    keep_network_configs: bool = True
    wipe_mac_bindings: bool = False


class SystemLogResponse(BaseModel):
    id: int
    level: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


class NodeNotesUpdate(BaseModel):
    notes: Optional[str] = None


class NodeNatOverrideUpdate(BaseModel):
    # None clears the override -> inherit from group, then global settings
    orchestrator_behind_nat: Optional[bool] = None


class NodeRateLimitUpdate(BaseModel):
    # KiB/s. None clears the override -> inherit the group limit, then unlimited.
    upload_rate_limit: Optional[int] = None


class NodeProvisionRequest(BaseModel):
    bootstrap_user: str = "root"
    bootstrap_password: str
    force_orchestrator_proxy: Optional[bool] = False


class DeviceResponse(BaseModel):
    name: str
    size: int
    model: str
    rotational: bool
    disk_type: str # SATA, NVME
    is_usb: bool = False


class GroupWindowFitResponse(BaseModel):
    """Whether a group's scheduled work actually fits its execution window."""
    group_id: int
    group_name: str
    nodes_per_run: int              # nodes that land on the busiest single day
    est_hours: float                # estimated transfer hours for that day
    window_hours: float             # length of the execution window
    concurrency: int                # effective parallel streams (bandwidth-capped)
    capacity_hours: float           # window_hours * concurrency
    fits: bool
    rate_limit_kib: Optional[int] = None
    has_estimate: bool = False      # False => est_hours is a default guess, not history


class SchedulerLoadResponse(BaseModel):
    day_load: List[int]
    week_load: List[int]
    month_load: List[int]
    # Estimated transfer hours per bucket — node counts alone say nothing about
    # whether the work fits when links are slow.
    day_hours: List[float] = []
    week_hours: List[float] = []
    month_hours: List[float] = []
    group_fit: List[GroupWindowFitResponse] = []


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


class AuditLogResponse(BaseModel):
    id: int
    username: str
    action: str
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


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


class HaspFeatureResponse(BaseModel):
    id: str
    name: Optional[str] = None
    product_name: Optional[str] = None
    product_id: Optional[str] = None
    lic_type: Optional[str] = None
    unusable: str
    key_id: str


class HaspStatusResponse(BaseModel):
    status: str
    features: List[HaspFeatureResponse] = []


class NodeCheckinRequest(BaseModel):
    hostname: str
    ip_address: Optional[str] = None







