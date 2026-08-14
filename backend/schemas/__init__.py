"""Request and response models, split by the part of the system they describe.

This was one 833-line module holding 80 classes. Everything imports it as
`import schemas` and reaches models as `schemas.NodeResponse`, so the package
re-exports the whole surface and no call site had to change.

The split follows the section comments the original already carried — they were
the de facto structure, just not enforced by anything. Cross-module references
form a DAG: `base` at the bottom, `settings` feeding `groups`, and nothing
pointing back up. Keep it that way; a cycle here surfaces as an import error at
startup rather than anywhere useful.

Adding a model means adding it to its module and to the matching `from .x
import` line below. `tests/test_schema_package.py` fails if a class is defined
and not exported.
"""
from schemas.base import UTCModel

from schemas.settings import (
    RetentionPolicySchema, ExclusionSchema, CredentialSchema, CredentialSummary,
    SettingsBase, SettingsResponse,
)
from schemas.groups import (
    BackupGroupBase, BackupGroupCreate, BackupGroupResponse,
    GroupWindowFitResponse, SchedulerLoadResponse,
)
from schemas.nodes import (
    NodeCreate, NodeResponse, PaginatedNodesResponse, NodeNotesUpdate,
    NodeNatOverrideUpdate, NodeRateLimitUpdate, NodeProvisionRequest,
    DeviceResponse, NodeCheckinRequest, HaspFeatureResponse, HaspStatusResponse,
)
from schemas.backups import (
    BackupHistoryResponse, PaginatedBackupHistoryResponse, ArchiveFileInfo,
    ArchiveFileListResponse, ArchiveFileContentResponse, BackupTriggerRequest,
    RestoreRequest, PurgeFailedRequest, PurgeFailedResponse,
)
from schemas.tasks import (
    TaskLogSummaryResponse, PaginatedTaskLogResponse, TaskLogResponse,
    SystemLogResponse, AuditLogResponse,
)
from schemas.kiosks import (
    KioskBase, KioskCreate, KioskResponse, HandshakeRequest, KioskEnrollRequest,
    KioskIssueRequest, KioskUpdate, KioskIpUpdateRequest, AutoHandshakeRequest,
    RequestActivationRequest,
)
from schemas.users import (
    UserBase, UserCreate, UserUpdate, UserSelfUpdate, UserResponse,
    LoginPayload, SshKeyFindingResponse,
)
from schemas.stats import (
    GlobalStatsResponse, NodeReliability, FailureCount, ReliabilitySection,
    NodeSpeed, SpeedSection, NodeDuration, DurationSection, NodeConsumption,
    CapacitySection, StatsInsightsResponse,
)
from schemas.monitoring import (
    SmartSubScore, SmartHealthResponse, ThermalHealthResponse, NodeHealthResponse,
    SmartHistoryPoint, ThermalHistoryPoint, TelemetryPoint, MonitoringThresholds,
    NodeMonitoringUpdate, UiPreferencesResponse,
)
from schemas.notifications import (
    AlertResponse, NotificationPreferences, NotificationTestResult, NotificationStatus,
)
