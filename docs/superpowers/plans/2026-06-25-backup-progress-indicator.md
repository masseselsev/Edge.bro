# Backup Progress Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a real-time progress indicator for running backups on the Fleet page that visualizes progress on the "Backup" button and redirects to task logs when clicked.

**Architecture:** Store the start timestamp and task ID in Redis under `backup_running:{node_id}` during task startup. The `GET /api/nodes` endpoint computes estimated progress asymptotically. The frontend button renders progress via a linear gradient background overlay and handles clicks by opening the task logs screen.

**Tech Stack:** Python 3.11, FastAPI, Celery, Redis, React, TypeScript, Tailwind CSS.

## Global Constraints
- Strict Python Type Hinting: Always use Pydantic models for request/response serialization.
- Maximum File Size: No single file must exceed 500 lines. Split router, tasks, and components when they grow.
- UI Styling: Use standard Tailwind CSS utilities, maintaining the dynamic premium aesthetic with transition animations.

---

### Task 1: Backend Integration (Redis, Schemas, Router)

**Files:**
- Modify: `backend/schemas.py:87-113`
- Modify: `backend/backup_tasks.py:244-257`
- Modify: `backend/routers/nodes.py:67-120`
- Modify: `backend/tests/test_db.py:330-365`

**Interfaces:**
- Consumes: None (uses existing `redis_client` and db session)
- Produces: Updated `/api/nodes` endpoint returning `is_backup_running: bool`, `backup_progress: int`, and `backup_task_id: Optional[str]`.

- [ ] **Step 1: Write the failing test**

Modify [backend/tests/test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py#L330-L365) to include assertions for the new fields `is_backup_running`, `backup_progress`, and `backup_task_id` when fetching nodes in tests:
```python
def test_node_response_backup_fields(db_session):
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    
    # login/setup mock node
    # assert node returned from GET /api/nodes has:
    # "is_backup_running", "backup_progress", "backup_task_id"
    # (Since no backup is running, they should default to False, 0, and None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. ./venv/bin/pytest -k test_node_response_backup_fields`
Expected: FAIL (or pydantic validation error due to missing fields in response schema)

- [ ] **Step 3: Implement schemas change**

Modify [backend/schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py#L87-L113):
```python
class NodeResponse(BaseModel):
    # ... existing fields ...
    is_backup_running: Optional[bool] = False
    backup_progress: Optional[int] = 0
    backup_task_id: Optional[str] = None
```

- [ ] **Step 4: Implement Redis tracking in task**

Modify [backend/backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py#L244-L257):
Add `import time` at the top of the file, then change the Redis set code:
```python
    import time
    redis_client.setex(f"backup_running:{node.id}", 14400, f"{int(time.time())}:{task_id}")
```

- [ ] **Step 5: Implement progress calculations in router**

Modify [backend/routers/nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py#L67-L120):
```python
        # Check if backup is running
        is_running = False
        progress = 0
        running_task_id = None
        try:
            val = redis_client.get(f"backup_running:{node.id}")
            if val:
                val_str = val.decode('utf-8') if isinstance(val, bytes) else str(val)
                is_running = True
                if ":" in val_str:
                    parts = val_str.split(":", 1)
                    start_time = int(parts[0])
                    running_task_id = parts[1]
                else:
                    start_time = int(val_str)
                
                import time
                import math
                elapsed = max(0, int(time.time()) - start_time)
                progress = max(0, min(99, int(100 * (1 - math.exp(-elapsed / 45.0)))))
        except Exception:
            pass

        node_dict = {
            # ... existing fields ...
            "is_backup_running": is_running,
            "backup_progress": progress,
            "backup_task_id": running_task_id,
        }
```

- [ ] **Step 6: Run tests and verify they pass**

Run: `PYTHONPATH=. ./venv/bin/pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/schemas.py backend/backup_tasks.py backend/routers/nodes.py backend/tests/test_db.py
git commit -m "feat(backend): add real-time backup progress tracking in Redis and schemas"
```

---

### Task 2: Frontend Progress Button and Redirect

**Files:**
- Modify: `frontend/src/components/NodeRow.tsx`
- Modify: `frontend/src/components/FleetTab.tsx`
- Modify: `frontend/src/components/ScheduleTab.tsx`
- Modify: `frontend/src/components/HistoryTab.tsx`
- Modify: `frontend/src/components/NodeDetailsModal.tsx`
- Modify: `frontend/src/components/NodeModals.tsx`

**Interfaces:**
- Consumes: Node response fields `is_backup_running`, `backup_progress`, and `backup_task_id` from Task 1.
- Produces: Visual progress bar overlay on the Backup button and log streaming redirection.

- [ ] **Step 1: Update Node interfaces**

In all modified frontend components, update `interface Node` definition:
```typescript
interface Node {
  // ... existing fields ...
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
}
```

- [ ] **Step 2: Modify Backup Button rendering and styling**

In [frontend/src/components/NodeRow.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeRow.tsx#L180-L186):
Replace the button rendering with:
```typescript
        <button
          onClick={() => onShowBackup(node)}
          disabled={node.status !== 'READY' && !node.is_backup_running}
          style={node.is_backup_running ? {
            background: `linear-gradient(to right, rgba(99, 102, 241, 0.25) ${node.backup_progress}%, transparent ${node.backup_progress}%)`
          } : undefined}
          className={`px-2.5 py-1.5 text-xs font-semibold rounded border transition-colors ${
            node.is_backup_running
              ? 'animate-pulse text-indigo-300 border-indigo-500 bg-indigo-500/5 hover:bg-indigo-500/10 cursor-pointer'
              : 'bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border-indigo-500/20 disabled:opacity-30'
          }`}
        >
          {node.is_backup_running
            ? `${t('backupAction')} (${node.backup_progress}%)`
            : t('backupAction')}
        </button>
```

- [ ] **Step 3: Modify backup click callback inside FleetTab**

In [frontend/src/components/FleetTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FleetTab.tsx#L168-L191):
Inside the `FleetTab` component rendering, modify `onShowBackup`:
```typescript
                onShowBackup={(node) => {
                  if (node.is_backup_running && node.backup_task_id) {
                    onViewLogs(node.backup_task_id, `Backing up ${node.hostname}`);
                  } else {
                    setShowBackupModal(node);
                  }
                }}
```

- [ ] **Step 4: Verify frontend builds successfully**

Run: `npm --prefix frontend run build`
Expected: Successful Vite compilation.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NodeRow.tsx frontend/src/components/FleetTab.tsx frontend/src/components/ScheduleTab.tsx frontend/src/components/HistoryTab.tsx frontend/src/components/NodeDetailsModal.tsx frontend/src/components/NodeModals.tsx
git commit -m "feat(frontend): implement visual backup progress and logs view redirection on click"
```
