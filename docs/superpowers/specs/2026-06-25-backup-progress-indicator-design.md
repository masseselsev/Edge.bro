# Design Spec: Real-time Backup Progress Indicator

We will introduce a real-time progress indicator for running backups on the Fleet page. The indicator will be shown on the "Backup" action button itself as a pulsing, filling progress bar that updates dynamically and remains visible even on page refresh.

## Proposed Changes

### Backend

#### [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Extend `NodeResponse` schema to include:
  * `is_backup_running: Optional[bool] = False`
  * `backup_progress: Optional[int] = 0`
  * `backup_task_id: Optional[str] = None`

#### [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)
- Import `time` module.
- Modify `redis_client.setex(f"backup_running:{node.id}", 14400, ...)` inside `run_backup_task` to store the creation timestamp and task ID:
  ```python
  redis_client.setex(f"backup_running:{node.id}", 14400, f"{int(time.time())}:{task_id}")
  ```

#### [routers/nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py)
- In the `get_nodes` endpoint:
  * Look up `backup_running:{node.id}` from Redis.
  * If it exists, split it into start timestamp and task ID.
  * Calculate elapsed seconds: `elapsed = max(0, int(time.time()) - start_time)`.
  * Calculate an asymptotic progress percentage using: `progress = int(100 * (1 - math.exp(-elapsed / 45.0)))`, bounded within `[0, 99]`.
  * Populate `is_backup_running`, `backup_progress`, and `backup_task_id` in the returned node dictionary.

---

### Frontend

#### Types
- Update the `Node` interface to include optional fields:
  ```typescript
  is_backup_running?: boolean;
  backup_progress?: number;
  backup_task_id?: string | null;
  ```
  across the following components:
  * `frontend/src/components/NodeRow.tsx`
  * `frontend/src/components/FleetTab.tsx`
  * `frontend/src/components/ScheduleTab.tsx`
  * `frontend/src/components/HistoryTab.tsx`
  * `frontend/src/components/NodeDetailsModal.tsx`
  * `frontend/src/components/NodeModals.tsx`

#### [NodeRow.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeRow.tsx)
- Modify the "Backup" button:
  * Do not disable it if `is_backup_running` is true.
  * When `is_backup_running` is true:
    * Apply style `background: linear-gradient(to right, rgba(99, 102, 241, 0.3) {progress}%, transparent {progress}%)`.
    * Apply `animate-pulse` class for the pulsing visual cue.
    * Adjust border/text colors to reflect active operation: `border-indigo-500 text-indigo-300 bg-indigo-500/5 hover:bg-indigo-500/10 cursor-pointer`.
    * Change button label to: `Backup (X%)`.

#### [FleetTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/FleetTab.tsx)
- Inside the `onShowBackup` callback handler:
  * Check if the node has `is_backup_running` and `backup_task_id`.
  * If so, redirect the action to trigger logs view instead: `onViewLogs(node.backup_task_id, `Backing up ${node.hostname}`)`.
  * Otherwise, open the normal backup options modal.
