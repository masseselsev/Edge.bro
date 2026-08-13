"""Backup groups: the scheduling unit a node belongs to."""
from typing import Optional, List
from pydantic import BaseModel, Field
from schemas.settings import RetentionPolicySchema


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
