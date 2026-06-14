# Design Specification: Scheduled & Automated Edge Node Backups

Implement group-based backup scheduling, time-window constraints, load balancing via hostname-based micro-staggering, automated retry strategies for failed runs, hardware details collection, and visual scheduling dashboards.

## User Review Required

> [!WARNING]
> This change introduces a new database table `backup_groups` and updates the `nodes` table. Running Alembic migrations will be required to update the schema in production.

## Proposed Changes

### [Backend Database & Models]

#### [MODIFY] [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
* Create `BackupGroup` model:
  ```python
  class BackupGroup(Base):
      __tablename__ = 'backup_groups'
      
      id = Column(Integer, primary_key=True, index=True)
      name = Column(String, unique=True, index=True, nullable=False)
      interval = Column(String, nullable=False)  # weekly, monthly, quarterly, yearly
      target_week = Column(Integer, default=1, nullable=False)
      start_time = Column(String, nullable=False)  # "02:00"
      end_time = Column(String, nullable=False)    # "05:00"
      concurrency_limit = Column(Integer, default=5, nullable=False)
      randomize_days = Column(Boolean, default=True, nullable=False)
  ```
* Update `Node` model to include:
  ```python
  group_id = Column(Integer, ForeignKey('backup_groups.id'), nullable=True)
  backup_paused = Column(Boolean, default=False, nullable=False)
  backup_today = Column(Boolean, default=False, nullable=False)
  missed_window = Column(Boolean, default=False, nullable=False)
  cpu_info = Column(String, nullable=True)
  memory_info = Column(String, nullable=True)
  edge_version = Column(String, nullable=True)
  notes = Column(Text, nullable=True)
  ```

---

### [Ansible Playbook & Hardware Collection]

#### [MODIFY] [prepare.yml](file:///home/masse/projects/Backup-edge-Restore/backend/playbooks/prepare.yml)
* Add shell commands to gather system details:
  * Extract CPU model using `lscpu` or `/proc/cpuinfo`.
  * Extract total memory using `free -h` or `/proc/meminfo`.
  * Parse EDGE version from `/etc/motd` under the `EDGE:` label.
  * Print these values as stdout logs prefixed with `CPU_INFO:`, `MEM_INFO:`, and `EDGE_VERSION:`.

#### [MODIFY] [ansible_utils.py](file:///home/masse/projects/Backup-edge-Restore/backend/ansible_utils.py)
* Add logic in `run_ansible_playbook` output scanning loop to detect:
  * `CPU_INFO:` -> store in `parsed_data["cpu_info"]`.
  * `MEM_INFO:` -> store in `parsed_data["memory_info"]`.
  * `EDGE_VERSION:` -> store in `parsed_data["edge_version"]`.

#### [MODIFY] [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)
* Update `run_prepare_task` to read `cpu_info`, `memory_info`, and `edge_version` from `parsed_data` and write them to the `Node` database record.

---

### [Scheduler Engine (Celery Beat Task)]

#### [NEW] [scheduler.py](file:///home/masse/projects/Backup-edge-Restore/backend/core/scheduler.py)
* Implement the core evaluation algorithm:
  * Run a Celery Beat task every minute (`tasks.scheduler_tick`).
  * Check current UTC/local time.
  * For each active `BackupGroup`, evaluate if the current time falls inside the window (`start_time` to `end_time` of the group).
  * Calculate target execution days for each node using deterministic hashing:
    * `day_index = hash(node.hostname) % 7` (0 = Monday, 6 = Sunday).
    * `hour_offset = hash(node.hostname) % window_duration_hours`
    * `minute_offset = (hash(node.hostname) // window_duration_hours) % 60`
  * Identify nodes that need backup today (or have `backup_today == True`), are not paused (`backup_paused == False`), and do not have successful backups for the current recurrence period.
  * Trigger backups in parallel up to `concurrency_limit`.
  * Handle retries: If a node's backup fails, retry hourly inside the active window. If the window closes, mark `missed_window = True` and retry during the next day's window.

---

### [API Routes]

#### [NEW] [groups.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/groups.py)
* Implement REST endpoints for `BackupGroup` models:
  * `GET /api/groups` — List all backup groups.
  * `POST /api/groups` — Create a backup group.
  * `PUT /api/groups/{id}` — Edit configuration parameters.
  * `DELETE /api/groups/{id}` — Delete a group.
* Add endpoints to get computed scheduler loads:
  * `GET /api/scheduler/load` — Returns the daily (24 hour dots), weekly (7 days), and monthly (4 weeks) planned backup counts.

#### [MODIFY] [nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py)
* Implement actions:
  * `POST /api/nodes/{id}/notes` — Update note comment text.
  * `POST /api/nodes/{id}/backup-today` — Set `backup_today = True`.
  * `POST /api/nodes/{id}/toggle-pause` — Toggles `backup_paused` state.
  * `POST /api/nodes/{id}/assign-group/{group_id}` — Assigns or changes a node's backup group.

---

### [Frontend React UI Panels]

#### [NEW] [ScheduleTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/ScheduleTab.tsx)
* Add a dedicated "Schedule" tab in the header navigation.
* Displays group summaries: recurrence intervals, window hours, total active/paused nodes.
* Adds a load visualization section:
  * Day view: 24 horizontal indicators colored from green to red based on hourly scheduled starts count.
  * Week view: 7 day blocks.
  * Month view: 4 week blocks.
* Integrates "Backup Group Now" trigger button for each group.
* Includes modal dialog for creating/editing scheduling parameters.

#### [MODIFY] [FleetTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FleetTab.tsx)
* Show assigned Backup Group name and next calculated backup date/time on each node row.
* Add warning badges: "Backup Paused" (yellow), "Missed Window" (red).
* Add a "Details" button to open the node card modal.

#### [NEW] [NodeDetailsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeDetailsModal.tsx)
* Visual overlay displaying:
  * Hardware stats: hostname, CPU model, total RAM, OS version, Edge version, IP, disk type.
  * Group scheduling details + Next scheduled run time.
  * Text area for node notes.
  * Operational actions: "Pause/Resume Auto-Backups", "Backup Today" (runs in next window), "Provision" (re-triggers Auto-Prepare).
  * Datatable containing specific backup history and logs for this device.

---

## Verification Plan

### Automated Tests
* Create unit tests in `backend/tests/test_scheduler.py` verifying:
  * Day of week and stagger time calculations are deterministic and correct.
  * Staggering logic spreads start times evenly across the allowed window.
  * Failed backups trigger hourly retry tasks inside the time window.
  * Pause flags and concurrency limits are respected.

### Manual Verification
* Verify that assigning nodes to a group generates correct load indicators on the "Schedule" tab.
* Verify that clicking "Backup Today" queues the node for backup in the next available window.
* Verify that running "Provision" updates the node's hardware details and Edge version successfully.
