# Scheduled & Automated Edge Node Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated, group-based backup scheduling, time-window constraints, load balancing via micro-staggering, automated retries, hardware collection, and visual scheduling dashboards.

**Architecture:** We will store backup group definitions and node assignments in the database, execute periodic scheduler evaluations via a minute-level Celery Beat tick task, stagger backup start offsets deterministically using hostname hashes to prevent peak loads, and integrate load charts and detailed node card modals in the React frontend.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Celery, React, TypeScript, Tailwind CSS.

---

### Task 1: Database Models & Migrations

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/schemas.py`
- Create: `backend/alembic/versions/<migration_script>.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: Write database schema updates in models.py**
  Add the `BackupGroup` model and update the `Node` model fields in [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py).
  ```python
  # Add BackupGroup class to models.py
  class BackupGroup(Base):
      __tablename__ = 'backup_groups'
      
      id = Column(Integer, primary_key=True, index=True)
      name = Column(String, unique=True, index=True, nullable=False)
      interval = Column(String, nullable=False)  # weekly, monthly, quarterly, yearly
      target_week = Column(Integer, default=1, nullable=False)
      start_time = Column(String, nullable=False)  # e.g. "02:00"
      end_time = Column(String, nullable=False)    # e.g. "05:00"
      concurrency_limit = Column(Integer, default=5, nullable=False)
      randomize_days = Column(Boolean, default=True, nullable=False)

  # Add fields to Node class in models.py
  # group_id = Column(Integer, ForeignKey('backup_groups.id'), nullable=True)
  # backup_paused = Column(Boolean, default=False, nullable=False)
  # backup_today = Column(Boolean, default=False, nullable=False)
  # missed_window = Column(Boolean, default=False, nullable=False)
  # cpu_info = Column(String, nullable=True)
  # memory_info = Column(String, nullable=True)
  # edge_version = Column(String, nullable=True)
  # notes = Column(Text, nullable=True)
  ```

- [ ] **Step 2: Add Pydantic Schemas**
  Create schemas in [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py):
  ```python
  from pydantic import BaseModel, Field
  
  class BackupGroupBase(BaseModel):
      name: str
      interval: str
      target_week: int = 1
      start_time: str
      end_time: str
      concurrency_limit: int = 5
      randomize_days: bool = True

  class BackupGroupCreate(BackupGroupBase):
      pass

  class BackupGroupResponse(BackupGroupBase):
      id: int

      class Config:
          from_attributes = True
  ```
  Update `NodeResponse` in [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py) to include `group_id`, `backup_paused`, `backup_today`, `missed_window`, `cpu_info`, `memory_info`, `edge_version`, and `notes`.

- [ ] **Step 3: Generate and Run Alembic Migration**
  Run: `docker compose exec backend alembic revision --autogenerate -m "add backup groups and node details"`
  Apply: `docker compose exec backend alembic upgrade head`

- [ ] **Step 4: Write Database Tests**
  Add unit tests in [test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py) verifying group creation and node linking relationships.

- [ ] **Step 5: Run tests and commit**
  Run: `docker compose exec backend env PYTHONPATH=. pytest tests/test_db.py`
  Expected: PASS.
  Commit.

---

### Task 2: Playbook Modifications & Hardware Parsing

**Files:**
- Modify: `backend/playbooks/prepare.yml`
- Modify: `backend/ansible_utils.py`
- Modify: `backend/backup_tasks.py`
- Test: `backend/tests/test_cli_parsing.py`

- [ ] **Step 1: Update prepare.yml to gather system details**
  Add bash logic to [prepare.yml](file:///home/masse/projects/Backup-edge-Restore/backend/playbooks/prepare.yml) shell task:
  ```bash
  # CPU
  cpu_info=$(lscpu | grep 'Model name' | cut -d: -f2- | xargs || grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | xargs)
  # Memory
  mem_info=$(free -h | awk '/^Mem:/ {print $2}')
  # OS
  os_ver=$(lsb_release -d 2>/dev/null | cut -d: -f2- | xargs || cat /etc/debian_version 2>/dev/null || echo "Debian")
  # EDGE Version from MOTD
  edge_ver="UNKNOWN"
  if [ -f /etc/motd ]; then
    edge_ver=$(awk '/EDGE:/ {getline; print; exit}' /etc/motd | xargs)
  fi

  echo "CPU_INFO:$cpu_info"
  echo "MEM_INFO:$mem_info"
  echo "OS_VERSION:$os_ver"
  echo "EDGE_VERSION:$edge_ver"
  ```

- [ ] **Step 2: Update ansible_utils.py to parse outputs**
  In [ansible_utils.py](file:///home/masse/projects/Backup-edge-Restore/backend/ansible_utils.py), parse new lines:
  ```python
  if "CPU_INFO:" in line:
      parsed_data["cpu_info"] = line.split("CPU_INFO:")[1].strip()
  if "MEM_INFO:" in line:
      parsed_data["memory_info"] = line.split("MEM_INFO:")[1].strip()
  if "EDGE_VERSION:" in line:
      parsed_data["edge_version"] = line.split("EDGE_VERSION:")[1].strip()
  ```

- [ ] **Step 3: Update run_prepare_task**
  In `run_prepare_task` in [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py), populate the database columns `cpu_info`, `memory_info`, and `edge_version` with the parsed dictionary values.

- [ ] **Step 4: Verify and commit**
  Run: `docker compose exec backend env PYTHONPATH=. pytest`
  Commit.

---

### Task 3: Backend REST API for Backup Groups & Load Maps

**Files:**
- Create: `backend/routers/groups.py`
- Modify: `backend/main.py`
- Modify: `backend/routers/nodes.py`

- [ ] **Step 1: Create backup groups endpoints**
  Implement GET, POST, PUT, DELETE operations for `/api/groups` in [groups.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/groups.py).

- [ ] **Step 2: Implement load map calculation API**
  Add endpoint `GET /api/groups/scheduler-load` returning the load map count arrays:
  * `daily_load`: `List[int]` (24 values, representing hourly start counts calculated using deterministic hash offsets: `hash(node.hostname) % 7`, `hour_offset = hash(node.hostname) % duration`).
  * `weekly_load`: `List[int]` (7 values).
  * `monthly_load`: `List[int]` (4 values).

- [ ] **Step 3: Implement Node updates API**
  In [nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py):
  * `POST /api/nodes/{id}/notes` — update `notes` comment text.
  * `POST /api/nodes/{id}/backup-today` — set `backup_today = True`.
  * `POST /api/nodes/{id}/toggle-pause` — toggle `backup_paused`.
  * `POST /api/nodes/{id}/assign-group/{group_id}` — update `group_id`.

- [ ] **Step 4: Register Router**
  Mount router in [main.py](file:///home/masse/projects/Backup-edge-Restore/backend/main.py):
  ```python
  from routers import groups
  app.include_router(groups.router, prefix="/api", tags=["groups"])
  ```

- [ ] **Step 5: Run tests and commit**

---

### Task 4: Scheduler Core Logic & Retries

**Files:**
- Create: `backend/core/scheduler.py`
- Modify: `backend/tasks.py`

- [ ] **Step 1: Write scheduler core algorithm**
  Create [scheduler.py](file:///home/masse/projects/Backup-edge-Restore/backend/core/scheduler.py) to parse groups and run hourly/daily retries:
  * For each node in an active window, stagger its start time:
    `start_minute = (hash(node.hostname) // window_hours) % 60`
  * If a node is scheduled for today (or `backup_today == True`), trigger the Celery `run_backup_task`.
  * Do not exceed `concurrency_limit` per group.

- [ ] **Step 2: Add Celery Beat scheduler tick**
  In [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py), register the scheduled tick:
  ```python
  @celery_app.task
  def scheduler_tick():
      from core.scheduler import run_schedule_evaluation
      run_schedule_evaluation()
  ```

- [ ] **Step 3: Add unit tests for the scheduler logic**
  Create `backend/tests/test_scheduler.py` testing retry conditions, load offsets, and concurrency checks.

- [ ] **Step 4: Run tests and commit**

---

### Task 5: Frontend Schedule Tab (Load Visualization)

**Files:**
- Create: `frontend/src/components/ScheduleTab.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create ScheduleTab component**
  Write [ScheduleTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/ScheduleTab.tsx) displaying group list, edit forms, and the load chart bars (24 hourly circles, 7 daily blocks, 4 monthly week blocks).

- [ ] **Step 2: Mount tab in App.tsx**
  Add `ScheduleTab` import in [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), and register the nav option.

- [ ] **Step 3: Test front-end compilation**
  Run: `npm run build --prefix frontend`
  Commit.

---

### Task 6: Frontend Fleet Tab updates & Node Details Modal

**Files:**
- Create: `frontend/src/components/NodeDetailsModal.tsx`
- Modify: `frontend/src/components/FleetTab.tsx`

- [ ] **Step 1: Create NodeDetailsModal component**
  Write [NodeDetailsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeDetailsModal.tsx) showcasing hardware attributes, note comments editor, pause toggler, "Backup Today" button, and specific node backup history table.

- [ ] **Step 2: Integrate badges and Details button in FleetTab**
  Update [FleetTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FleetTab.tsx) node rows/cards to show the "Backup Paused" or "Missed Window" badges and the details modal trigger.

- [ ] **Step 3: Verify and restart frontend container**
  Run: `npm run build --prefix frontend && docker compose up -d --build frontend`
  Commit.
