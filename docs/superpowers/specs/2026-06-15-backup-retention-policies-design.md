# Design Specification: Customizable Backup Retention Policies

Implement support for flexible, customizable backup retention policies in the Borg Backup Orchestrator. Users will be able to define retention globally and optionally override these settings at the backup group level. The scheduler's daily pruning task will execute `borg prune` per-node using the selected strategy: standard interval-based rotation, count-based rotation, or timeframe-based retention.

## User Review Required

> [!WARNING]
> This change introduces new fields (`retention_policy` as JSON) to settings and backup groups database tables, which will require running Alembic migrations.
>
> The daily prune execution task (`global_daily_prune`) will be updated to execute `borg prune` individually for each node using the `--prefix` flag, replacing the current global repository-wide prune command. This is critical to prevent nodes from pruning other nodes' backups.

## Proposed Changes

### [Backend Database & Models]

#### [MODIFY] [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
* Update `Settings` table to add JSON column:
  * `retention_policy = Column(JSON, nullable=True)` (Default: `None`)
* Update `BackupGroup` table to add:
  * `override_retention = Column(Boolean, default=False, nullable=False)`
  * `retention_policy = Column(JSON, nullable=True)` (Default: `None`)

#### [MODIFY] [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
* Add `RetentionPolicySchema` Pydantic model:
  ```python
  class RetentionPolicySchema(BaseModel):
      type: str = Field(default='interval')  # 'interval', 'count', 'timeframe'
      keep_daily: int = Field(default=7, ge=0)
      keep_weekly: int = Field(default=4, ge=0)
      keep_monthly: int = Field(default=6, ge=0)
      keep_last: int = Field(default=5, ge=1)
      within_value: int = Field(default=3, ge=1)
      within_unit: str = Field(default='m')  # 'd', 'w', 'm', 'y'
  ```
* Include `retention_policy` field in `SettingsBase` and `SettingsResponse`.
* Include `override_retention` and `retention_policy` fields in `BackupGroupBase` and `BackupGroupResponse`.

---

### [Backend Routers & Pruning Logic]

#### [MODIFY] [groups.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/groups.py)
* Map `override_retention` and `retention_policy` parameters in creation (`create_group`) and modification (`update_group`) endpoints.

#### [MODIFY] [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)
* Update `global_daily_prune` task logic:
  * Fetch all nodes.
  * Iterate over each node and resolve its active policy:
    * If the node has a group assigned, group has `override_retention == True`, and `group.retention_policy` is configured: use group's policy.
    * Otherwise: use global `Settings.retention_policy`.
    * Fallback: If no policy config is found, use settings' deprecated `keep_daily`, `keep_weekly`, and `keep_monthly` flat columns.
  * Construct and execute the individual `borg prune` command for each node:
    ```bash
    borg prune --prefix "{node.hostname}-" [policy flags] /data/borg/fleet
    ```
    * For `interval` type: `--keep-daily {keep_daily} --keep-weekly {keep_weekly} --keep-monthly {keep_monthly}`
    * For `count` type: `--keep-last {keep_last}`
    * For `timeframe` type: `--keep-last 1 --keep-within {within_value}{within_unit}`
  * Run `borg compact /data/borg/fleet` exactly once at the very end of the task.

---

### [Frontend React UI Panels]

#### [MODIFY] [translations.ts](file:///home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts)
* Add keys for all retention setting fields, strategies, and summary templates:
  * English, Russian, and Ukrainian translations for: `retentionPolicy`, `overrideRetention`, `retentionType`, `policyInterval`, `policyCount`, `policyTimeframe`, `keepLastLabel`, `keepWithinLabel`, `timeframeUnitDays`, `timeframeUnitWeeks`, `timeframeUnitMonths`, `timeframeUnitYears`, `retentionInherit`, `retentionSummaryLast`, `retentionSummaryWithin`.

#### [MODIFY] [SettingsTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/SettingsTab.tsx)
* Replace the legacy numerical inputs in "Global Pruning" section with a unified layout:
  * Strategy dropdown selector.
  * Conditional fields based on selection (Standard Daily/Weekly/Monthly numerical inputs, single count input, or duration/unit inputs).
  * Map saved settings payload and load state using unified JSON config object.

#### [MODIFY] [BackupGroupModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/BackupGroupModal.tsx)
* Add `overrideRetention` toggle checkbox.
* When checked, expand identical policy layout (Strategy selector + dynamic inputs).
* Bind inputs to state and submit them in JSON POST/PUT payload.

#### [MODIFY] [ScheduleTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/ScheduleTab.tsx)
* Update `BackupGroup` type definitions.
* Render a summary of the active pruning rule on each group's layout card (e.g. showing "Inherits global settings" or specific count/timeframe description).

---

## Verification Plan

### Automated Tests
* Create unit tests in `backend/tests/test_pruning.py` verifying:
  * `global_daily_prune` resolves setting policy correctly (handling group overrides and setting fallback).
  * Generated `borg prune` CLI command contains the correct flags based on the configured strategy type.
  * Correct prefix is passed for each individual node's hostname.

### Manual Verification
* Save various retention policies globally and on a backup group, verifying correct values are preserved in the DB and loaded back to UI.
* Trigger a dry-run or mock execution of the daily prune task to verify arguments are compiled correctly.
