from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class RetentionPolicySchema(BaseModel):
    type: str = Field(default='interval')  # 'interval', 'count', 'timeframe'
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_last: int = Field(default=5, ge=1)
    within_value: int = Field(default=3, ge=1)
    within_unit: str = Field(default='m')  # 'd', 'w', 'm', 'y'


class SettingsBase(BaseModel):
    borg_ssh_port: int = Field(default=12345, ge=1, le=65535)
    borg_repo_path: str = Field(default='/data/borg')
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    global_exclusions: str = Field(default='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/blobstore/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1')
    orchestrator_ip: str = Field(default='')
    timezone: str = Field(default='Browser Local')
    language: str = Field(default='en')
    retention_policy: Optional[RetentionPolicySchema] = None
    default_compression: str = Field(default='zstd:3')
    default_cpu_quota: Optional[int] = Field(default=None, ge=0, le=400)
    server_ips: Optional[List[str]] = Field(default=[])



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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

class BackupTriggerRequest(BaseModel):
    comment: Optional[str] = None

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


class SchedulerLoadResponse(BaseModel):
    day_load: List[int]
    week_load: List[int]
    month_load: List[int]


class KioskBase(BaseModel):
    name: Optional[str] = None
    uuid: Optional[str] = None
    phone: Optional[str] = None
    comment: Optional[str] = None



class KioskCreate(KioskBase):
    pass


class KioskResponse(KioskBase):
    id: int
    key: str
    status: str
    ip_address: Optional[str] = None
    ssh_pub_key: Optional[str] = None
    auth_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HandshakeRequest(BaseModel):
    uuid: str
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
    uuid: str
    name: str
    phone: str
    comment: str
    ssh_pub_key: str



