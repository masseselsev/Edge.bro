"""Fleet-wide aggregates for the statistics dashboard."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from schemas.base import UTCModel


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


# --- Repository capacity ----------------------------------------------------

class ShardCapacity(BaseModel):
    """One borg repository, and how full its busiest night leaves it."""
    index: int
    path: str
    #: False until the first node assigned here has run `borg init`, which
    #: happens lazily on its first backup. An empty directory is not a fault.
    initialized: bool
    nodes: int
    busiest_night_hours: Optional[float] = None
    window_hours: Optional[float] = None
    utilization_pct: Optional[float] = None
    busiest_day: Optional[str] = None
    size_bytes: Optional[int] = None


class RepositoryPeak(BaseModel):
    """The single worst repository-night in the projection."""
    shard_index: Optional[int] = None
    utilization_pct: Optional[float] = None
    hours: Optional[float] = None
    window_hours: Optional[float] = None
    day: Optional[str] = None


class NodeCapacity(BaseModel):
    """How many nodes one repository can carry."""
    per_night: int
    #: Higher than `per_night`, and the more useful of the two: groups spread
    #: the fleet across weeks and months, so a node scheduled monthly occupies
    #: a fraction of a nightly slot.
    sustained: int
    median_node_hours: Optional[float] = None
    runs_per_node: Optional[float] = None
    #: New enrolments that fit before the fullest repository saturates.
    headroom_nodes: int = 0


class StorageCeiling(BaseModel):
    """What the recorded history says the shared storage path can carry.

    Repositories multiply locks, not bandwidth. Several of them behind one
    network mount are several writers sharing one pipe, so the count of
    repositories is an upper bound on useful parallelism rather than a promise
    of it.
    """
    #: False until two backups have genuinely overlapped. Nothing can be
    #: concluded from a deployment that has only run one at a time, and saying
    #: so is the correct output rather than a fabricated number.
    sufficient: bool
    ceiling_mbps: Optional[float] = None
    max_observed_writers: int = 0
    #: Aggregate throughput stopped improving as writers were added.
    saturated: bool = False
    supported_writers: Optional[int] = None


class RepositoryExpansion(BaseModel):
    """What a given repository count would deliver."""
    shard_count: int
    busiest_utilization_pct: Optional[float] = None
    #: Always false. A node's repository is fixed at enrolment, so raising the
    #: count cannot move anyone already placed — it only routes new enrolments.
    relieves_existing: bool = False
    new_node_headroom: int = 0


class RepositoryCapacityResponse(UTCModel):
    generated_at: datetime
    shard_count: int
    configured_shard_count: int
    #: True when the count was floored above what the environment asked for by
    #: repositories that already exist on disk.
    count_floored: bool
    storage_path: str
    #: The backups directory is not a plain Docker volume, so it may be a
    #: network mount where added repositories share one pipe.
    is_host_path: bool

    projection_nights: int
    shards: List[ShardCapacity] = []
    peak: RepositoryPeak
    capacity: NodeCapacity
    ceiling: StorageCeiling
    expansion: List[RepositoryExpansion] = []
    #: Which of the two actually limits parallel writers right now.
    binding_constraint: str
