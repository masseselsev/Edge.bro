"""Managed nodes, their hardware, and the licence runtime on them."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


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
    os_arch: Optional[str] = None
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
    # Measured while the backup runs, in Mbit/s. None until borg's rolling
    # window can state a rate — the first seconds of every transfer.
    current_speed_mbps: Optional[float] = None
    # The upload limit that applies to the running backup, for context next to
    # the measured figure. None when the node is transferring uncapped.
    current_speed_limit_mbps: Optional[float] = None
    backup_task_id: Optional[str] = None
    last_ping_status: Optional[bool] = None
    last_available_at: Optional[datetime] = None
    # None = inherit from the node's group, then the global setting
    orchestrator_behind_nat: Optional[bool] = None
    # KiB/s. None = inherit the group limit, then unlimited.
    upload_rate_limit: Optional[int] = None
    # None = inherit from the node's group, then the global default. 0 =
    # explicit override to no limit.
    cpu_quota: Optional[int] = None
    # Which repository holds this node's archives, resolved from its shard (see
    # models.Node.borg_repo_path). Carried in the response because the restore
    # kiosk builds its own borg URL and has no way to derive the shard layout —
    # before this it assumed every node lived in the pre-sharding repository.
    borg_repo_path: Optional[str] = None

    class Config:
        from_attributes = True

class PaginatedNodesResponse(BaseModel):
    nodes: List[NodeResponse]
    total: int
    page: int
    limit: int
    pages: int

class NodeNotesUpdate(BaseModel):
    notes: Optional[str] = None

class NodeNatOverrideUpdate(BaseModel):
    # None clears the override -> inherit from group, then global settings
    orchestrator_behind_nat: Optional[bool] = None

class NodeRateLimitUpdate(BaseModel):
    # KiB/s. None clears the override -> inherit the group limit, then unlimited.
    upload_rate_limit: Optional[int] = None

class NodeCpuQuotaOverrideUpdate(BaseModel):
    # Percent of one core. None clears the override -> inherit the group's
    # value, then the global default. 0 is a distinct, valid value meaning
    # "explicit no limit" for this node — see models.Node.cpu_quota.
    cpu_quota: Optional[int] = Field(default=None, ge=0, le=400)

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
    is_system: bool = False

class NodeCheckinRequest(BaseModel):
    hostname: str
    ip_address: Optional[str] = None

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
