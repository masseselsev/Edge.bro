from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SettingsBase(BaseModel):
    borg_ssh_port: int = Field(default=12345, ge=1, le=65535)
    borg_repo_path: str = Field(default='/data/borg')
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    global_exclusions: str = Field(default='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1')
    orchestrator_ip: str = Field(default='')

class SettingsResponse(SettingsBase):
    id: int

    class Config:
        from_attributes = True

class NodeCreate(BaseModel):
    hostname: str
    ip_address: str
    ssh_port: int = 22
    bootstrap_user: str = "root"
    bootstrap_password: str

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

    class Config:
        from_attributes = True

class TaskLogResponse(BaseModel):
    id: str
    task_type: str
    status: str
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


class DeviceResponse(BaseModel):
    name: str
    size: int
    model: str
    rotational: bool
    disk_type: str # SATA, NVME
    is_usb: bool = False

