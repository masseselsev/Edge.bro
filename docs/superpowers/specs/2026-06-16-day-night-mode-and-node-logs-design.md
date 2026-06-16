# Design Spec: Day/Night Mode & Node Interaction Logs

## 1. Goal
Add support for:
- Redesigning the styling system using dynamic CSS variables mapped to Tailwind CSS standard `zinc` utility classes, enabling a premium light/dark mode switch.
- Linking background task logs (BOOTSTRAP, PREPARE, BACKUP) directly to nodes via a `node_id` foreign key.
- Displaying a "Console Logs" tab inside the Node Details modal with a dropdown to view past executions.

---

## 2. Database & Backend Changes

### 2.1 Database Schema (Models)
Modify `TaskLog` in [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py):
- Add a nullable foreign key column:
  `node_id = Column(Integer, ForeignKey('nodes.id', ondelete='CASCADE'), nullable=True)`

### 2.2 Alembic Migration
Create a new Alembic migration that adds the `node_id` column to the `task_logs` table.

### 2.3 Task Upgrades
Update [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py) and [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py):
- Pass `node_id` when instantiating `TaskLog(...)` inside `run_bootstrap_task`, `run_prepare_task`, and `run_backup_task`.

### 2.4 API Endpoint
Add the following endpoint to [nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py):
- `GET /api/nodes/{node_id}/task-logs`
  - Queries `TaskLog` records filtered by `node_id`.
  - Orders by `created_at DESC`, limiting output to the last 20 tasks.
  - Returns fields: `id`, `task_type`, `status`, `created_at`, `updated_at`, `log_output`.

---

## 3. Frontend & Theme Changes

### 3.1 Tailwind Configuration
Update [tailwind.config.js](file:///home/masse/projects/Backup-edge-Restore/frontend/tailwind.config.js):
- Map all `zinc` utility classes to standard RGB-separated CSS variables to enable dynamic rendering while preserving Tailwind opacity modifiers:
  ```javascript
  colors: {
    zinc: {
      50: 'rgb(var(--zinc-50) / <alpha-value>)',
      100: 'rgb(var(--zinc-100) / <alpha-value>)',
      200: 'rgb(var(--zinc-200) / <alpha-value>)',
      300: 'rgb(var(--zinc-300) / <alpha-value>)',
      400: 'rgb(var(--zinc-400) / <alpha-value>)',
      500: 'rgb(var(--zinc-500) / <alpha-value>)',
      700: 'rgb(var(--zinc-700) / <alpha-value>)',
      750: 'rgb(var(--zinc-750) / <alpha-value>)',
      800: 'rgb(var(--zinc-800) / <alpha-value>)',
      900: 'rgb(var(--zinc-900) / <alpha-value>)',
      950: 'rgb(var(--zinc-950) / <alpha-value>)',
    }
  }
  ```

### 3.2 CSS Variables (index.css)
Update [index.css](file:///home/masse/projects/Backup-edge-Restore/frontend/src/index.css):
- Define standard dark mode theme under `:root` using space-separated RGB values.
- Define inverted light mode values under the `.light` class to ensure cards appear white, borders light gray, and text dark gray.
- Replace hardcoded background `#0b0f19` and text `#f3f4f6` colors on the `body` tag with dynamic variables.

### 3.3 Theme Switcher Button
Update [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx):
- Add a theme toggle button next to the language dropdown selector.
- Use `Sun` and `Moon` Lucide icons.
- Persist selection via `localStorage.setItem('theme', ...)` on load and change.
- Toggle the `light` class on `document.documentElement` dynamically.

### 3.4 Modal Logs Component
Update [NodeDetailsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeDetailsModal.tsx):
- Add a "Console Logs" tab next to the metadata tabs.
- Fetch logs from the node endpoint when the tab is selected.
- Provide a log selector dropdown showing list of tasks with their execution times and outcomes.
- Render logs in a dark monospace block simulating a console container.
