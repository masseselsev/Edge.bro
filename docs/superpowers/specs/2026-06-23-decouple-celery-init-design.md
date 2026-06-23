# Spec: Decouple Celery App Initialization to Resolve Thread-Local AMQP Fallback Issue

## Context & Problem
During file splitting (commit `c9ff572`) to keep backend files under the 500-line limit, remote backup tasks (`run_backup_task`) were moved to `backup_tasks.py`. Because `tasks.py` imports `backup_tasks.py`, decorating tasks inside `backup_tasks.py` with `@celery_app.task` would introduce a circular import.

To avoid this circular import, tasks in `backup_tasks.py` (and `restore_tasks.py`) were decorated with `@shared_task(bind=True)`. However, `@shared_task` relies on a thread-local/context-local variable for the active Celery application. 

When a HTTP request is processed by FastAPI/uvicorn, the route handles are executed inside threadpool threads. In these threads, Celery's default/fallback application is used because the thread-local state hasn't been set. The fallback application defaults to an AMQP broker (`amqp://localhost`), leading to `Connection refused` (error 111) when trying to trigger backups.

## Proposed Design
We will introduce a dedicated module to hold the Celery application instantiation, completely decoupling task decorators from circular dependencies.

### 1. New Module: [celery_app.py](file:///home/masse/projects/Backup-edge-Restore/backend/celery_app.py) [NEW]
This file will be at the root of the import hierarchy and will only instantiate the Celery app:
```python
import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)
```

### 2. Update [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py)
- Import `celery_app` and `REDIS_URL` from the new `celery_app.py`.
- Remove the local initialization block:
  ```python
  # Initialize Celery
  REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
  celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)
  ```

### 3. Update [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)
- Import `celery_app` from `celery_app.py`.
- Replace `@shared_task(bind=True)` with `@celery_app.task(bind=True)` for `run_prepare_task` and `run_backup_task`.
- Replace `@shared_task` with `@celery_app.task` for `global_daily_prune`.

### 4. Update [restore_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/restore_tasks.py)
- Import `celery_app` from `celery_app.py`.
- Replace `@shared_task(bind=True)` with `@celery_app.task(bind=True)` for `flash_restore_device` and `purge_node_archives`.

### 5. Update [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)
- Import `celery_app` from `celery_app.py` instead of `tasks.py`.

## Verification Plan

### Automated Tests
- Run `pytest` inside the backend container to verify that migrations, schema validations, and basic database unit tests pass.

### Manual Verification
- Deploy/restart the remote services on `gomari.zt.cyni.cc`.
- Trigger backup on node `192.168.222.95` (Node ID 8) and verify it registers, queues, and successfully finishes without any 500 or Connection refused errors.
