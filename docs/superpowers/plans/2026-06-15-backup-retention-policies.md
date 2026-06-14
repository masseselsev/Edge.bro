# Customizable Backup Retention Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement global and group-level customizable backup retention policies (Interval, Count, Timeframe) backed by per-node native Borg prune operations.

**Architecture:**
- DB: Add `retention_policy` JSON column to Settings and BackupGroup tables, and `override_retention` boolean column to BackupGroup.
- Backend Schemas: Add `RetentionPolicySchema` to serialize and validate settings payloads.
- Backend Task: Refactor `global_daily_prune` to loop over all nodes, resolve the active policy per node, construct a prefix-specific `borg prune` CLI command (`--prefix "{node.hostname}-"`), and run `borg compact` once at the end.
- Frontend: Add UI strategy pickers and dynamic parameter fields to both `SettingsTab` and `BackupGroupModal`, and display active policies on `ScheduleTab` group cards.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy, Alembic, React, TypeScript, Tailwind CSS.

---

### Task 1: Database Migration
Add the JSON columns to `settings` and `backup_groups` tables.

**Files:**
- Modify: [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
- Create: Alembic migration file in `backend/alembic/versions`

- [ ] **Step 1: Update models.py**

Modify [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py):
Add `retention_policy` column to `Settings` (lines 5-22):
```python
    retention_policy = Column(JSON, nullable=True)
```
Add `override_retention` and `retention_policy` columns to `BackupGroup` (lines 25-39):
```python
    override_retention = Column(Boolean, default=False, nullable=False)
    retention_policy = Column(JSON, nullable=True)
```

- [ ] **Step 2: Generate Alembic migration**

Run: `docker compose exec backend alembic revision --autogenerate -m "add_retention_policy_to_settings_and_groups"`
Expected: Successful migration file creation.

- [ ] **Step 3: Edit and inspect generated migration file**

Open the new file in [backend/alembic/versions](file:///home/masse/projects/Backup-edge-Restore/backend/alembic/versions). Ensure it correctly specifies the changes. Add `server_default=sa.text('false')` for the boolean column.

- [ ] **Step 4: Run migration to upgrade DB**

Run: `docker compose exec backend alembic upgrade head`
Expected: Database schema upgraded successfully.

---

### Task 2: Pydantic Schemas and API Updates

**Files:**
- Modify: [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Modify: [groups.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/groups.py)
- Modify: [settings.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/settings.py)

- [ ] **Step 1: Add schemas.py classes**

Modify [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py):
Create `RetentionPolicySchema` and update `SettingsBase`, `BackupGroupBase`:
```python
class RetentionPolicySchema(BaseModel):
    type: str = Field(default='interval')  # 'interval', 'count', 'timeframe'
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_last: int = Field(default=5, ge=1)
    within_value: int = Field(default=3, ge=1)
    within_unit: str = Field(default='m')  # 'd', 'w', 'm', 'y'

class SettingsBase(BaseModel):
    ...
    retention_policy: Optional[RetentionPolicySchema] = None

class BackupGroupBase(BaseModel):
    ...
    override_retention: bool = False
    retention_policy: Optional[RetentionPolicySchema] = None
```

- [ ] **Step 2: Update settings router mapping**

Modify [settings.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/settings.py):
Set/get `settings.retention_policy` correctly in `get_settings` and `update_settings` endpoints.
Ensure `settings.retention_policy = payload.retention_policy.model_dump() if payload.retention_policy else None` is handled.

- [ ] **Step 3: Update groups router mapping**

Modify [groups.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/groups.py):
Set `override_retention` and `retention_policy` attributes inside `create_group` and `update_group`.

---

### Task 3: Refactor Pruning Logic in backend/backup_tasks.py

**Files:**
- Modify: [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)

- [ ] **Step 1: Re-write global_daily_prune**

Modify `global_daily_prune` in [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py) to:
1. Iterate over all nodes.
2. Resolve active retention policy per node.
3. Build the node-specific prefix `borg prune` CLI command:
   `borg prune --prefix "{node.hostname}-"`
4. Execute individual prunes.
5. Execute `borg compact /data/borg/fleet` exactly once at the end of the task.

Code content for the revised `global_daily_prune` loop:
```python
@shared_task
def global_daily_prune() -> Dict[str, Any]:
    db: Session = SessionLocal()
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings()

    nodes = db.query(Node).all()
    results = {"prunes": {}, "compact": "PENDING"}

    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        db.close()
        return {"error": "Repository path not found"}

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    # Pre-fetch groups
    groups = {g.id: g for g in db.query(models.BackupGroup).all()}

    for node in nodes:
        # Resolve policy
        policy = None
        group = groups.get(node.group_id) if node.group_id else None
        
        if group and group.override_retention and group.retention_policy:
            policy = group.retention_policy
        elif settings.retention_policy:
            policy = settings.retention_policy

        # Build command parameters
        prune_cmd = ["borg", "prune", "--prefix", f"{node.hostname}-"]

        if policy:
            p_type = policy.get("type", "interval")
            if p_type == "interval":
                prune_cmd.extend([
                    "--keep-daily", str(policy.get("keep_daily", 7)),
                    "--keep-weekly", str(policy.get("keep_weekly", 4)),
                    "--keep-monthly", str(policy.get("keep_monthly", 6))
                ])
            elif p_type == "count":
                prune_cmd.extend(["--keep-last", str(policy.get("keep_last", 5))])
            elif p_type == "timeframe":
                val = policy.get("within_value", 3)
                unit = policy.get("within_unit", "m")
                prune_cmd.extend([
                    "--keep-last", "1",
                    "--keep-within", f"{val}{unit}"
                ])
        else:
            # Fallback to legacy settings flat columns
            prune_cmd.extend([
                "--keep-daily", str(settings.keep_daily),
                "--keep-weekly", str(settings.keep_weekly),
                "--keep-monthly", str(settings.keep_monthly)
            ])

        prune_cmd.append(repo_path)

        try:
            logger.info(f"Executing Borg prune for node {node.hostname}...")
            res_prune = subprocess.run(prune_cmd, env=env, capture_output=True, text=True)
            if res_prune.returncode == 0:
                results["prunes"][node.hostname] = "SUCCESS"
            else:
                logger.error(f"Borg prune failed for node {node.hostname}: {res_prune.stderr}")
                results["prunes"][node.hostname] = f"FAILED: {res_prune.stderr}"
        except Exception as e:
            logger.error(f"Exception pruning node {node.hostname}: {str(e)}")
            results["prunes"][node.hostname] = f"ERROR: {str(e)}"

    # Compaction
    try:
        logger.info("Starting Borg repository compaction after daily prunes...")
        compact_cmd = ["borg", "compact", repo_path]
        res_compact = subprocess.run(compact_cmd, env=env, capture_output=True, text=True)
        if res_compact.returncode == 0:
            logger.info("Successfully compacted Borg repository.")
            results["compact"] = "SUCCESS"
        else:
            logger.error(f"Failed to compact Borg repository: {res_compact.stderr}")
            results["compact"] = f"FAILED: {res_compact.stderr}"
    except Exception as e:
        logger.error(f"Exception compacting Borg repository: {str(e)}")
        results["compact"] = f"ERROR: {str(e)}"

    fix_repo_permissions(repo_path)
    db.close()
    return results
```

---

### Task 4: Frontend Translation Keys and UI Implementation

**Files:**
- Modify: [translations.ts](file:///home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts)
- Modify: [SettingsTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/SettingsTab.tsx)
- Modify: [BackupGroupModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/BackupGroupModal.tsx)
- Modify: [ScheduleTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/ScheduleTab.tsx)

- [ ] **Step 1: Add translations**

Modify [translations.ts](file:///home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts):
Add keys for `retentionPolicy`, `overrideRetention`, `retentionType`, `policyInterval`, `policyCount`, `policyTimeframe`, `keepLastLabel`, `keepWithinLabel`, `timeframeUnitDays`, `timeframeUnitWeeks`, `timeframeUnitMonths`, `timeframeUnitYears`, `retentionInherit`, `retentionSummaryLast`, `retentionSummaryWithin`.

- [ ] **Step 2: Add dynamic policy inputs to SettingsTab.tsx**

Modify [SettingsTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/SettingsTab.tsx):
- Map `retention_policy` structure into local state variables: `policyType`, `keepDaily`, `keepWeekly`, `keepMonthly`, `keepLast`, `withinValue`, `withinUnit`.
- Render a drop-down selector for policy type.
- Conditionally render numeric input fields based on chosen type.
- Send the unified `retention_policy` JSON payload in `handleSave`.

- [ ] **Step 3: Add override controls to BackupGroupModal.tsx**

Modify [BackupGroupModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/BackupGroupModal.tsx):
- Add `overrideRetention` boolean state and inputs matching `SettingsTab.tsx` for group custom configs.
- Save config payload in `handleSubmit`.

- [ ] **Step 4: Render active policy summary in ScheduleTab.tsx**

Modify [ScheduleTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/ScheduleTab.tsx):
Under each group card details, render the active retention strategy name and settings summary (or showing "Inherited from global settings" if override is unchecked).

---

### Task 5: Testing & Verification

**Files:**
- Create: `backend/tests/test_pruning.py`

- [ ] **Step 1: Write test_pruning.py**

Create unit tests verifying:
1. `global_daily_prune` fetches settings policy.
2. Group overrides work (nodes in groups with overrides get correct command parameters).
3. Legacy columns are fallback values when no JSON is present.
4. Correct command arguments `--keep-last` and `--keep-within` are generated.

- [ ] **Step 2: Run tests**

Run: `docker compose exec backend env PYTHONPATH=. pytest`
Expected: All tests pass.

- [ ] **Step 3: Build & Deploy Frontend**

Run: `cd frontend && npm run build`
Then deploy built bundles into container volume:
Run: `docker cp frontend/dist/. backup-edge-restore-frontend-1:/usr/share/nginx/html/`

- [ ] **Step 4: Verify UI flows in browser**

Open the interface, go to Settings, adjust retention, save.
Go to Schedule, create a new group, test override checkbox, save group, and verify the summary text matches.
