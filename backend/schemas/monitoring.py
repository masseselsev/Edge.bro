"""SMART, thermal and telemetry health reporting."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from schemas.base import UTCModel


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
