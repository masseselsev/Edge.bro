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









# --- Archive statistics -----------------------------------------------------

class GlobalStatsResponse(BaseModel):
    """Fleet-wide totals for the Archives page header.

    The size fields cover every successful archive, not just each node's first
    one, and the physical figures come from the filesystem rather than from
    summing what backups reported. The two are not interchangeable: the sums
    describe what was written over time, the disk figures what is there now.
    """
    total_nodes: int
    nodes_with_archives: int

    total_archives: int
    successful_archives: int
    failed_archives: int
    success_rate: Optional[float] = None

    total_original_size_bytes: int
    total_deduplicated_size_bytes: int

    # The saving across nodes, measured on each node's base backup only. A node
    # re-backing up unchanged data is not deduplication, and counting it would
    # inflate the ratio into meaninglessness.
    base_original_size_bytes: int = 0
    base_deduplicated_size_bytes: int = 0
    base_nodes: int = 0
    saved_space_bytes: int
    deduplication_ratio: Optional[float] = None

    # Measured on disk. None when the repository is unreadable from here.
    repo_size_bytes: Optional[int] = None
    disk_total_bytes: Optional[int] = None
    disk_used_bytes: Optional[int] = None
    disk_free_bytes: Optional[int] = None


class NodeReliability(UTCModel):
    node_id: int
    hostname: str
    ip_address: Optional[str] = None
    group_name: Optional[str] = None
    last_success_at: Optional[datetime] = None
    days_since_success: Optional[float] = None
    expected_interval_days: int
    consecutive_failures: int
    last_error_category: Optional[str] = None
    runs_in_window: int
    is_stale: bool


class FailureCount(BaseModel):
    category: str
    count: int


class ReliabilitySection(BaseModel):
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: Optional[float] = None

    nodes_total: int
    nodes_never_succeeded: int
    nodes_stale: int

    stale_nodes: List[NodeReliability] = []
    failing_nodes: List[NodeReliability] = []
    top_failures: List[FailureCount] = []


class NodeSpeed(BaseModel):
    node_id: int
    hostname: str
    runs: int
    median_mbps: Optional[float] = None
    max_mbps: Optional[float] = None
    limit_kib: Optional[int] = None
    limit_source: Optional[str] = None
    limit_mbps: Optional[float] = None
    # True when the configured cap is what held the transfer back, False when
    # something else did, None when there is nothing to compare.
    limit_binding: Optional[bool] = None


class SpeedSection(BaseModel):
    measured_runs: int
    median_mbps: Optional[float] = None
    p10_mbps: Optional[float] = None
    p90_mbps: Optional[float] = None
    slowest_nodes: List[NodeSpeed] = []
    capped_nodes: int = 0


class NodeDuration(BaseModel):
    node_id: int
    hostname: str
    runs: int
    median_seconds: Optional[float] = None
    max_seconds: Optional[float] = None
    group_name: Optional[str] = None
    window_minutes: Optional[int] = None
    window_usage: Optional[float] = None
    at_risk: bool = False


class DurationSection(BaseModel):
    measured_runs: int
    median_seconds: Optional[float] = None
    p90_seconds: Optional[float] = None
    nodes_at_risk: int = 0
    longest_nodes: List[NodeDuration] = []


class NodeConsumption(BaseModel):
    node_id: int
    hostname: str
    bytes: int
    share: Optional[float] = None
    archives: int


class CapacitySection(UTCModel):
    repo_size_bytes: Optional[int] = None
    disk_total_bytes: Optional[int] = None
    disk_used_bytes: Optional[int] = None
    disk_free_bytes: Optional[int] = None

    daily_inflow_bytes: Optional[float] = None
    days_until_full: Optional[float] = None
    projected_full_date: Optional[datetime] = None

    top_consumers: List[NodeConsumption] = []


class StatsInsightsResponse(UTCModel):
    window_days: int
    generated_at: datetime
    reliability: ReliabilitySection
    speed: SpeedSection
    duration: DurationSection
    capacity: CapacitySection


class PurgeFailedRequest(BaseModel):
    """Which failed history rows to drop. Both filters are optional and AND
    together; sending neither clears every failed record in the fleet."""
    node_id: Optional[int] = None
    before: Optional[datetime] = None


class PurgeFailedResponse(BaseModel):
    deleted: int
    checkpoints_removed: int = 0


# --- Node health monitoring -------------------------------------------------

class SmartSubScore(BaseModel):
    name: str
    score: Optional[float] = None
    evidence: dict = Field(default_factory=dict)


class SmartHealthResponse(UTCModel):
    """The latest SMART reading for one device, scored."""
    captured_at: datetime
    device: str
    protocol: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None

    health_passed: Optional[bool] = None
    temperature_c: Optional[int] = None
    power_on_hours: Optional[int] = None
    written_bytes: Optional[int] = None
    percent_used: Optional[float] = None

    score: Optional[int] = None
    grade: Optional[str] = None
    subscores: List[SmartSubScore] = []
    overrides: List[str] = []
    advisories: List[str] = []

    # Endurance projection. Carries every input so the UI can show the
    # derivation instead of asking an operator to trust a date.
    projected_date: Optional[datetime] = None
    days_remaining: Optional[float] = None
    percent_used_per_day: Optional[float] = None
    bytes_per_day: Optional[float] = None
    observation_days: Optional[float] = None
    observation_points: int = 0
    projection_unavailable_reason: Optional[str] = None


class ThermalHealthResponse(UTCModel):
    """Thermal interface verdict for one node, from both detectors."""
    status: str
    #: Combined headline; the two below are what produced it.
    cohort_status: Optional[str] = None
    drift_status: Optional[str] = None

    theta_c_per_w: Optional[float] = None
    cohort_key: Optional[str] = None
    cohort_size: int = 0
    cohort_median: Optional[float] = None
    z_score: Optional[float] = None
    excess_ratio: Optional[float] = None

    baseline_theta: Optional[float] = None
    recent_theta: Optional[float] = None
    drift_ratio: Optional[float] = None

    reasons: List[str] = []
    #: Windows fitted vs rejected in the analysis period, so an empty verdict
    #: can be explained rather than just looking blank.
    windows_fitted: int = 0
    windows_rejected: int = 0
    last_rejection: Optional[str] = None


class NodeHealthResponse(UTCModel):
    node_id: int
    hostname: str
    last_harvest_at: Optional[datetime] = None
    monitoring_enabled: bool = True
    capabilities: Optional[dict] = None
    smart: List[SmartHealthResponse] = []
    thermal: Optional[ThermalHealthResponse] = None


class SmartHistoryPoint(UTCModel):
    captured_at: datetime
    device: str
    score: Optional[int] = None
    temperature_c: Optional[int] = None
    percent_used: Optional[float] = None
    power_on_hours: Optional[int] = None
    written_bytes: Optional[int] = None


class ThermalHistoryPoint(UTCModel):
    window_start: datetime
    rejection: str
    theta_c_per_w: Optional[float] = None
    theta_normalised: Optional[float] = None
    tau_seconds: Optional[float] = None
    t_ambient_c: Optional[float] = None
    excitation: Optional[float] = None
    mean_temp_c: Optional[float] = None


class TelemetryPoint(UTCModel):
    bucket_start: datetime
    power_w_mean: Optional[float] = None
    power_w_max: Optional[float] = None
    cpu_temp_c_mean: Optional[float] = None
    cpu_temp_c_max: Optional[float] = None
    board_temp_c_mean: Optional[float] = None
    ssd_temp_c_mean: Optional[float] = None
    cpu_util_mean: Optional[float] = None
    io_service_ms_mean: Optional[float] = None
    throttled: bool = False


class MonitoringThresholds(BaseModel):
    """Effective values with their origin, so the UI can show what is inherited."""
    monitoring_enabled: bool
    monitoring_interval_days: int
    smart_temp_warn_c: int
    smart_temp_crit_c: int
    #: Which of the above are set on the node rather than inherited.
    overridden: List[str] = []


class NodeMonitoringUpdate(BaseModel):
    """None means inherit the global value, which is distinct from setting it."""
    monitoring_enabled: Optional[bool] = None
    monitoring_interval_days: Optional[int] = Field(default=None, ge=1, le=365)
    smart_temp_warn_c: Optional[int] = Field(default=None, ge=0, le=120)
    smart_temp_crit_c: Optional[int] = Field(default=None, ge=0, le=120)


class UiPreferencesResponse(BaseModel):
    preferences: dict = Field(default_factory=dict)


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
