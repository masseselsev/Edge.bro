# Decouple Celery App Initialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple Celery app instantiation to a dedicated module (`celery_app.py`) and replace `@shared_task` decorators with explicit `@celery_app.task` decorators. This ensures task triggers from FastAPI request threads are correctly dispatched to Redis instead of falling back to localhost AMQP.

**Architecture:** Moving the Celery app instantiation to the root of the import tree resolves the circular dependency between `tasks.py` and modules like `backup_tasks.py` and `restore_tasks.py`. This allows us to use explicit task decoration (`@celery_app.task`) instead of lazy shared tasks (`@shared_task`).

**Tech Stack:** Python 3.11/3.13, Celery, Redis, FastAPI

## Global Constraints
- Maximum file size: No single file must exceed 500 lines.
- Database credentials & Borg Passphrase must be retrieved from environment variables.
- Run stateless validation commands with `RunPersistent: false`.

---

### Task 1: Create celery_app.py module

**Files:**
- Create: [celery_app.py](file:///home/masse/projects/Backup-edge-Restore/backend/celery_app.py)

**Interfaces:**
- Produces: `celery_app` (Celery instance) and `REDIS_URL` (str)

- [ ] **Step 1: Create file [celery_app.py](file:///home/masse/projects/Backup-edge-Restore/backend/celery_app.py)**
  
  Write the following content to `backend/celery_app.py`:
  ```python
  import os
  from celery import Celery

  REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
  celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)
  ```

- [ ] **Step 2: Verify syntax**
  
  Run python compilation check inside the backend container:
  Run: `docker compose exec backend python -m py_compile celery_app.py`
  Expected: Command completes successfully with exit code 0.

- [ ] **Step 3: Commit changes**
  
  ```bash
  git add backend/celery_app.py
  git commit -m "feat(celery): create celery_app module to hold Celery instance"
  ```

---

### Task 2: Update tasks.py Celery initialization

**Files:**
- Modify: [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py#L44-L50)

**Interfaces:**
- Consumes: `celery_app` and `REDIS_URL` from `celery_app.py`

- [ ] **Step 1: Replace Celery initialization in [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py)**
  
  Remove the local Celery instantiation:
  ```python
  # Initialize Celery
  REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
  celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)
  ```
  And replace it with:
  ```python
  # Initialize Celery
  from celery_app import celery_app, REDIS_URL
  ```

- [ ] **Step 2: Verify syntax & Celery broker**
  
  Run: `docker compose exec backend python -c "from tasks import celery_app; print(celery_app.conf.broker_url)"`
  Expected: Outputs `redis://redis:6379/0` and completes with exit code 0.

- [ ] **Step 3: Commit changes**
  
  ```bash
  git add backend/tasks.py
  git commit -m "refactor(celery): import celery_app from celery_app.py inside tasks.py"
  ```

---

### Task 3: Refactor backup_tasks.py tasks to use celery_app

**Files:**
- Modify: [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)

**Interfaces:**
- Consumes: `celery_app` from `celery_app.py`

- [ ] **Step 1: Import celery_app and replace task decorators in [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)**
  
  Replace the import `from celery import shared_task` with:
  ```python
  from celery_app import celery_app
  ```
  Replace `@shared_task(bind=True)` on `run_prepare_task` and `run_backup_task` with:
  ```python
  @celery_app.task(bind=True)
  ```
  Replace `@shared_task` on `global_daily_prune` with:
  ```python
  @celery_app.task
  ```

- [ ] **Step 2: Verify tasks register on Celery**
  
  Run: `docker compose exec backend python -c "from tasks import celery_app; print(sorted(list(celery_app.tasks.keys())))"`
  Expected: Output list contains `'backup_tasks.run_backup_task'`, `'backup_tasks.run_prepare_task'`, and `'backup_tasks.global_daily_prune'`.

- [ ] **Step 3: Commit changes**
  
  ```bash
  git add backend/backup_tasks.py
  git commit -m "refactor(celery): decorate backup_tasks with explicit celery_app.task"
  ```

---

### Task 4: Refactor restore_tasks.py tasks to use celery_app

**Files:**
- Modify: [restore_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/restore_tasks.py)

**Interfaces:**
- Consumes: `celery_app` from `celery_app.py`

- [ ] **Step 1: Import celery_app and replace task decorators in [restore_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/restore_tasks.py)**
  
  Replace the import `from celery import shared_task` with:
  ```python
  from celery_app import celery_app
  ```
  Replace `@shared_task(bind=True)` on `flash_restore_device` and `purge_node_archives` with:
  ```python
  @celery_app.task(bind=True)
  ```

- [ ] **Step 2: Verify tasks register on Celery**
  
  Run: `docker compose exec backend python -c "from tasks import celery_app; print(sorted(list(celery_app.tasks.keys())))"`
  Expected: Output list contains `'restore_tasks.flash_restore_device'` and `'restore_tasks.purge_node_archives'`.

- [ ] **Step 3: Commit changes**
  
  ```bash
  git add backend/restore_tasks.py
  git commit -m "refactor(celery): decorate restore_tasks with explicit celery_app.task"
  ```

---

### Task 5: Update iso_tasks.py imports

**Files:**
- Modify: [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py#L7)

**Interfaces:**
- Consumes: `celery_app` from `celery_app.py`

- [ ] **Step 1: Update celery_app import in [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)**
  
  Replace:
  ```python
  from tasks import celery_app
  ```
  with:
  ```python
  from celery_app import celery_app
  ```

- [ ] **Step 2: Verify compilation**
  
  Run: `docker compose exec backend python -m py_compile iso_tasks.py`
  Expected: Command completes with exit code 0.

- [ ] **Step 3: Commit changes**
  
  ```bash
  git add backend/iso_tasks.py
  git commit -m "refactor(celery): import celery_app from celery_app.py in iso_tasks.py"
  ```

---

### Task 6: End-to-End Verification

- [ ] **Step 1: Run local backend tests**
  
  Run: `docker compose exec backend pytest`
  Expected: All unit/integration tests pass.

- [ ] **Step 2: Force add all new/modified files to Git (handling ignored docs if any)**
  
  ```bash
  git add -f docs/superpowers/plans/2026-06-23-decouple-celery-init.md
  git commit -m "docs: add Celery decoupling implementation plan"
  ```
