# Day/Night Mode & Node Interaction Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement full interaction logs for nodes (linking tasks like bootstrap/prepare/backup to nodes and rendering them in the details modal) and a dynamic light/dark mode theme switch.

**Architecture:** 
- Add a nullable `node_id` foreign key to `TaskLog` database schema with Alembic migration.
- Associate Celery tasks with the target `node_id` during creation and implement an endpoint `GET /api/nodes/{node_id}/task-logs`.
- Configure custom theme colors in `tailwind.config.js` to map to space-separated CSS variables inside `index.css`.
- Add a Sun/Moon toggle button in the frontend header to toggle the `light` class on the document root and save the preference in `localStorage`.
- Integrate a console logs log-view component inside `NodeDetailsModal.tsx`.

**Tech Stack:** Python 3.11/3.13, FastAPI, SQLAlchemy, Alembic, React, TypeScript, Tailwind CSS, Lucide Icons.

---

### Task 1: Add node_id column to TaskLog database model

**Files:**
- Modify: [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
- Test: [test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py)

- [ ] **Step 1: Write a failing test verifying task log node association**
  Open [test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py) and add the following test at the end of the file:
  ```python
  def test_task_log_node_association(db_session):
      """
      Verify that TaskLog can be associated with a Node and queried.
      """
      node = models.Node(
          hostname="test-node-logs-assoc",
          ip_address="192.168.1.99",
          status="READY"
      )
      db_session.add(node)
      db_session.commit()
      db_session.refresh(node)

      task_log = models.TaskLog(
          id="test-task-uuid-1234",
          task_type="BOOTSTRAP",
          status="SUCCESS",
          node_id=node.id,
          log_output="Task completed successfully"
      )
      db_session.add(task_log)
      db_session.commit()

      retrieved = db_session.query(models.TaskLog).filter(models.TaskLog.node_id == node.id).first()
      assert retrieved is not None
      assert retrieved.id == "test-task-uuid-1234"
      assert retrieved.node_id == node.id
      assert retrieved.log_output == "Task completed successfully"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run:
  ```bash
  pytest backend/tests/test_db.py::test_task_log_node_association -v
  ```
  Expected: FAIL (AttributeError: 'TaskLog' object has no attribute 'node_id')

- [ ] **Step 3: Modify TaskLog model to add node_id field**
  Open [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py) and modify `TaskLog` class (lines 102-114) to include `node_id`:
  ```python
  class TaskLog(Base):
      """
      TaskLog model storing execution progress and logs for frontend console streaming.
      """
      __tablename__ = 'task_logs'

      id = Column(String, primary_key=True, index=True) # UUID string representation
      task_type = Column(String, nullable=False) # BOOTSTRAP, PREPARE, BACKUP, RESTORE
      status = Column(String, default='PENDING', nullable=False) # PENDING, RUNNING, SUCCESS, FAILED
      node_id = Column(Integer, ForeignKey('nodes.id', ondelete='CASCADE'), nullable=True)
      created_at = Column(DateTime, default=func.now(), nullable=False)
      updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
      log_output = Column(Text, default='', nullable=False)
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run:
  ```bash
  pytest backend/tests/test_db.py::test_task_log_node_association -v
  ```
  Expected: PASS

- [ ] **Step 5: Run all database tests to ensure no regression**
  Run:
  ```bash
  pytest backend/tests -v
  ```
  Expected: All tests PASS

- [ ] **Step 6: Commit changes**
  ```bash
  git add backend/models.py backend/tests/test_db.py
  git commit -m "feat(backend): add node_id field to TaskLog model"
  ```

---

### Task 2: Create and apply Alembic migration for TaskLog node_id

**Files:**
- Create: `backend/alembic/versions/<revision_id>_add_node_id_to_task_logs.py`

- [ ] **Step 1: Generate Alembic migration file**
  Run:
  ```bash
  docker compose exec backend alembic revision -m "add_node_id_to_task_logs"
  ```
  Identify the newly created revision python file path under `backend/alembic/versions/`.

- [ ] **Step 2: Define upgrade and downgrade in the migration file**
  Open the newly created migration file and define the commands using Alembic operations:
  ```python
  def upgrade() -> None:
      op.add_column('task_logs', sa.Column('node_id', sa.Integer(), nullable=True))
      op.create_foreign_key(
          'fk_task_logs_node_id_nodes',
          'task_logs', 'nodes',
          ['node_id'], ['id'],
          ondelete='CASCADE'
      )

  def downgrade() -> None:
      op.drop_constraint('fk_task_logs_node_id_nodes', 'task_logs', type_='foreignkey')
      op.drop_column('task_logs', 'node_id')
  ```

- [ ] **Step 3: Run the database migration**
  Run:
  ```bash
  docker compose exec backend alembic upgrade head
  ```
  Expected: Migration executes successfully.

- [ ] **Step 4: Commit migration**
  ```bash
  git add backend/alembic/versions/*_add_node_id_to_task_logs.py
  git commit -m "migration: add node_id to task_logs table"
  ```

---

### Task 3: Associate node_id in Celery tasks

**Files:**
- Modify: [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py)
- Modify: [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py)

- [ ] **Step 1: Assign node_id in run_bootstrap_task**
  Open [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py) and modify `run_bootstrap_task` at line 161 to save `node_id`:
  ```python
      task_log = TaskLog(id=task_id, task_type="BOOTSTRAP", status="RUNNING", node_id=node_id, log_output="")
  ```

- [ ] **Step 2: Assign node_id in run_prepare_task**
  Open [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py) and modify `run_prepare_task` at line 109 to save `node_id`:
  ```python
      task_log = TaskLog(id=task_id, task_type="PREPARE", status="RUNNING", node_id=node_id, log_output="")
  ```

- [ ] **Step 3: Assign node_id in run_backup_task**
  Open [backup_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/backup_tasks.py) and modify `run_backup_task` at line 180 to save `node_id`:
  ```python
      task_log = TaskLog(id=task_id, task_type="BACKUP", status="RUNNING", node_id=node_id, log_output="")
  ```

- [ ] **Step 4: Commit changes**
  ```bash
  git add backend/tasks.py backend/backup_tasks.py
  git commit -m "feat(tasks): associate task log creation with node_id"
  ```

---

### Task 4: Add schemas and nodes API endpoint for task logs

**Files:**
- Modify: [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Modify: [routers/nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py)

- [ ] **Step 1: Add node_id field to TaskLogResponse schema**
  Open [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py) and modify `TaskLogResponse` (lines 119-129) to add `node_id`:
  ```python
  class TaskLogResponse(BaseModel):
      id: str
      task_type: str
      status: str
      node_id: Optional[int] = None
      created_at: datetime
      updated_at: datetime
      log_output: str

      class Config:
          from_attributes = True
  ```

- [ ] **Step 2: Add GET /api/nodes/{node_id}/task-logs endpoint**
  Open [routers/nodes.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/nodes.py) and append the endpoint at the bottom of the file:
  ```python
  @router.get("/{node_id}/task-logs", response_model=List[schemas.TaskLogResponse])
  def get_node_task_logs(node_id: int, db: Session = Depends(get_db)):
      """
      Retrieves background execution logs associated with a specific node.
      """
      node = db.query(models.Node).filter(models.Node.id == node_id).first()
      if not node:
          raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
      return db.query(models.TaskLog).filter(models.TaskLog.node_id == node_id).order_by(models.TaskLog.created_at.desc()).limit(20).all()
  ```

- [ ] **Step 3: Test API endpoint using Swagger / local curl**
  Trigger a local check or verify backend tests pass.
  Run:
  ```bash
  pytest backend/tests -v
  ```
  Expected: PASS

- [ ] **Step 4: Commit API changes**
  ```bash
  git add backend/schemas.py backend/routers/nodes.py
  git commit -m "feat(api): add API endpoint to fetch task logs for a specific node"
  ```

---

### Task 5: Upgrade Tailwind theme to map zinc classes to CSS variables

**Files:**
- Modify: [tailwind.config.js](file:///home/masse/projects/Backup-edge-Restore/frontend/tailwind.config.js)
- Modify: [index.css](file:///home/masse/projects/Backup-edge-Restore/frontend/src/index.css)

- [ ] **Step 1: Map standard zinc colors in tailwind.config.js**
  Open [tailwind.config.js](file:///home/masse/projects/Backup-edge-Restore/frontend/tailwind.config.js) and modify the `theme` object to include custom `colors` mappings:
  ```javascript
  /** @type {import('tailwindcss').Config} */
  export default {
    content: [
      "./index.html",
      "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
      extend: {
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
        },
        keyframes: {
          modalEnter: {
            '0%': { opacity: 0, transform: 'scale(0.95)' },
            '100%': { opacity: 1, transform: 'scale(1)' },
          },
          dropdownEnter: {
            '0%': { opacity: 0, transform: 'translateY(-5px)' },
            '100%': { opacity: 1, transform: 'translateY(0)' },
          },
          fadeIn: {
            '0%': { opacity: 0 },
            '100%': { opacity: 1 },
          }
        },
        animation: {
          'modal-in': 'modalEnter 0.2s ease-out forwards',
          'dropdown-in': 'dropdownEnter 0.15s ease-out forwards',
          'fade-in': 'fadeIn 0.2s ease-out forwards',
        }
      },
    },
    plugins: [],
  }
  ```

- [ ] **Step 2: Add theme-specific CSS variables to index.css**
  Open [index.css](file:///home/masse/projects/Backup-edge-Restore/frontend/src/index.css). Override `body` styles and insert theme variables at the beginning of the file:
  ```css
  @tailwind base;
  @tailwind components;
  @tailwind utilities;

  :root {
    --zinc-50: 250 250 250;
    --zinc-100: 244 244 245;
    --zinc-200: 228 228 231;
    --zinc-300: 212 212 216;
    --zinc-400: 161 161 170;
    --zinc-500: 113 113 122;
    --zinc-700: 63 63 70;
    --zinc-750: 48 48 54;
    --zinc-800: 39 39 42;
    --zinc-900: 24 24 27;
    --zinc-950: 9 9 11;
    --body-bg: 11 15 25;      /* #0b0f19 */
    --body-text: 243 244 246; /* #f3f4f6 */
  }

  .light {
    --zinc-950: 255 255 255;  /* #ffffff */
    --zinc-900: 248 250 252;  /* #f8fafc */
    --zinc-800: 226 232 240;  /* #e2e8f0 */
    --zinc-750: 241 245 249;  /* #f1f5f9 */
    --zinc-700: 203 213 225;  /* #cbd5e1 */
    --zinc-500: 100 116 139;  /* #64748b */
    --zinc-400: 71 85 105;    /* #475569 */
    --zinc-300: 30 41 59;     /* #1e293b */
    --zinc-200: 15 23 42;     /* #0f172a */
    --zinc-100: 2 6 23;       /* #020617 */
    --zinc-50: 0 0 0;
    --body-bg: 248 250 252;   /* #f8fafc */
    --body-text: 15 23 42;    /* #0f172a */
  }

  html {
    scrollbar-gutter: stable;
  }

  /* Kiosk Adaptive Scaling for HD-Ready, Full-HD, 2K, and 4K displays */
  @media (max-width: 1366px) {
    body {
      zoom: 0.85;
    }
  }

  @media (min-width: 1920px) and (max-width: 2559px) {
    body {
      zoom: 1.15;
    }
  }

  @media (min-width: 2560px) and (max-width: 3839px) {
    body {
      zoom: 1.5;
    }
  }

  @media (min-width: 3840px) {
    body {
      zoom: 2.0;
    }
  }

  body {
    margin: 0;
    background-color: rgb(var(--body-bg));
    color: rgb(var(--body-text));
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
    transition: background-color 0.2s ease, color 0.2s ease;
  }
  ```

- [ ] **Step 3: Commit styling configurations**
  ```bash
  git add frontend/tailwind.config.js frontend/src/index.css
  git commit -m "style: map Tailwind zinc colors to dynamic CSS variables"
  ```

---

### Task 6: Implement Light/Dark mode switcher button in Header

**Files:**
- Modify: [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx)

- [ ] **Step 1: Implement Theme toggle switcher component**
  Open [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx). Add a `ThemeSwitcher` element inside `App` initialization or adjacent to language selection:
  ```typescript
  import { Sun, Moon } from 'lucide-react';
  ```
  Inside the main `App` component setup:
  ```typescript
    const [theme, setTheme] = useState<'dark' | 'light'>(() => {
      const saved = localStorage.getItem('theme');
      return (saved === 'light' || saved === 'dark') ? saved : 'dark';
    });

    useEffect(() => {
      if (theme === 'light') {
        document.documentElement.classList.add('light');
      } else {
        document.documentElement.classList.remove('light');
      }
      localStorage.setItem('theme', theme);
    }, [theme]);
  ```
  Add the button right next to the `LanguageSelector` dropdown (inside lines 500-580 of the layout rendering):
  ```tsx
  <button
    onClick={() => setTheme(prev => prev === 'dark' ? 'light' : 'dark')}
    className="p-2 bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-400 hover:text-zinc-200 transition-all cursor-pointer"
    title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
  >
    {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
  </button>
  ```

- [ ] **Step 2: Commit theme switcher**
  ```bash
  git add frontend/src/App.tsx
  git commit -m "feat(ui): add theme switcher toggle to header"
  ```

---

### Task 7: Implement Node Interaction Logs viewer in Details Modal

**Files:**
- Modify: [NodeDetailsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeDetailsModal.tsx)

- [ ] **Step 1: Add logs state and fetching implementation**
  Open [NodeDetailsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NodeDetailsModal.tsx). In the imports, add `Terminal`:
  ```typescript
  import { X, Play, Pause, Edit, Cpu, HardDrive, Cpu as MemIcon, Info, RefreshCw, Save, Database, History, Terminal, Calendar } from 'lucide-react';
  ```
  Add `TaskLog` interface (similar to other interfaces at lines 27-37):
  ```typescript
  interface TaskLog {
    id: string;
    task_type: string;
    status: string;
    created_at: string;
    log_output: string;
  }
  ```
  Inside the component, define log states:
  ```typescript
    const [activeTab, setActiveTab] = useState<'info' | 'logs'>('info');
    const [taskLogs, setTaskLogs] = useState<TaskLog[]>([]);
    const [selectedLogId, setSelectedLogId] = useState<string>('');
    const [loadingLogs, setLoadingLogs] = useState(false);
  ```
  Update `fetchNodeDetails` to query task logs as well:
  ```typescript
    const fetchNodeDetails = async () => {
      setLoading(true);
      try {
        const [nRes, hRes, gRes, tlRes] = await Promise.all([
          fetch('/api/nodes'),
          fetch(`/api/nodes/${nodeId}/history`),
          fetch('/api/groups'),
          fetch(`/api/nodes/${nodeId}/task-logs`)
        ]);

        if (nRes.ok) {
          const allNodes: Node[] = await nRes.json();
          const found = allNodes.find(n => n.id === nodeId);
          if (found) {
            setNode(found);
            setNotes(found.notes || '');
            setGroupId(found.group_id || 0);
          }
        }
        
        if (hRes.ok) {
          const histData = await hRes.json();
          histData.sort((a: any, b: any) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
          setHistory(histData);
        }
        
        if (gRes.ok) {
          setGroups(await gRes.json());
        }

        if (tlRes.ok) {
          const logsData: TaskLog[] = await tlRes.json();
          setTaskLogs(logsData);
          if (logsData.length > 0) {
            setSelectedLogId(logsData[0].id);
          }
        }
      } catch (err) {
        console.error("Failed to load node details:", err);
      } finally {
        setLoading(false);
      }
    };
  ```

- [ ] **Step 2: Add tab selection menu and custom console renderer**
  Add tab header switches in the modal layout:
  ```tsx
  <div className="flex gap-4 border-b border-zinc-800 pb-3 mb-4 font-sans text-xs">
    <button
      onClick={() => setActiveTab('info')}
      className={`pb-2 px-1 font-bold transition-all cursor-pointer ${activeTab === 'info' ? 'text-indigo-400 border-b-2 border-indigo-400' : 'text-zinc-400 hover:text-zinc-200'}`}
    >
      System Info & Settings
    </button>
    <button
      onClick={() => setActiveTab('logs')}
      className={`pb-2 px-1 font-bold transition-all cursor-pointer ${activeTab === 'logs' ? 'text-indigo-400 border-b-2 border-indigo-400' : 'text-zinc-400 hover:text-zinc-200'}`}
    >
      Console Logs
    </button>
  </div>
  ```
  Conditionally render the tab body. Inside the 'logs' tab content, add a selection dropdown and log viewer:
  ```tsx
  {activeTab === 'logs' && (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label className="text-xs font-semibold text-zinc-400">Select Session:</label>
          <select
            value={selectedLogId}
            onChange={(e) => setSelectedLogId(e.target.value)}
            className="px-3 py-1.5 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-100 text-xs focus:outline-none focus:border-indigo-500"
          >
            {taskLogs.map(tl => (
              <option key={tl.id} value={tl.id}>
                {tl.task_type} — {new Date(tl.created_at).toLocaleString()} ({tl.status})
              </option>
            ))}
            {taskLogs.length === 0 && <option value="">No log sessions recorded</option>}
          </select>
        </div>
        {selectedLogId && (
          <button
            onClick={() => {
              const currentLog = taskLogs.find(x => x.id === selectedLogId);
              if (currentLog) {
                navigator.clipboard.writeText(currentLog.log_output);
                alert("Log copied to clipboard!");
              }
            }}
            className="px-3 py-1.5 bg-zinc-850 hover:bg-zinc-800 text-zinc-200 border border-zinc-700/80 rounded-lg text-xs font-semibold transition"
          >
            Copy Log
          </button>
        )}
      </div>

      <div className="bg-zinc-950 border border-zinc-850 rounded-xl p-4 font-mono text-xs overflow-hidden">
        <pre className="text-emerald-400 bg-black p-4 rounded-lg overflow-y-auto max-h-[350px] whitespace-pre-wrap leading-relaxed">
          {taskLogs.find(x => x.id === selectedLogId)?.log_output || "Select a session from the list to display console logs."}
        </pre>
      </div>
    </div>
  )}
  ```

- [ ] **Step 3: Commit logs component**
  ```bash
  git add frontend/src/components/NodeDetailsModal.tsx
  git commit -m "feat(ui): render monospace Ansible/Borg console logs inside NodeDetailsModal"
  ```
