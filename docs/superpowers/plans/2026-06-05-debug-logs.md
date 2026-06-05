# Implementation Plan: Debug Logs in Logs Tab

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a "Debug" toggle in the Logs tab to view all system logs and exceptions (including background errors) in real time.

**Architecture:** 
1. Database: Add a `system_logs` table.
2. Backend: Implement a custom Python `logging.Handler` to save log lines into the database (filtering out circular database logs). Expose a `/api/tasks/debug-logs` endpoint.
3. Frontend: Add a "Debug Mode" toggle in `LogsTab.tsx` that fetches and displays the raw log output in a monospace console box.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, React, TypeScript.

---

### Task 1: Database Setup and Model

**Files:**
- Modify: [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
- Modify: [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Create: [Alembic Migration](file:///home/masse/projects/Backup-edge-Restore/backend/alembic/versions/add_system_logs.py)

- [ ] **Step 1: Add SystemLog model to models.py**
  Add the `SystemLog` database class:
  ```python
  class SystemLog(Base):
      __tablename__ = 'system_logs'

      id = Column(Integer, primary_key=True, index=True)
      level = Column(String, nullable=False)
      message = Column(Text, nullable=False)
      created_at = Column(DateTime, default=func.now(), nullable=False)
  ```

- [ ] **Step 2: Add SystemLogResponse to schemas.py**
  Add the Pydantic schema:
  ```python
  class SystemLogResponse(BaseModel):
      id: int
      level: str
      message: str
      created_at: datetime

      class Config:
          from_attributes = True
  ```

- [ ] **Step 3: Generate and run Alembic migration**
  Run commands:
  ```bash
  docker compose exec backend alembic revision --autogenerate -m "add_system_logs"
  docker compose exec backend alembic upgrade head
  ```

---

### Task 2: Backend DB Logging Handler & API Endpoint

**Files:**
- Modify: [database.py](file:///home/masse/projects/Backup-edge-Restore/backend/database.py)
- Modify: [main.py](file:///home/masse/projects/Backup-edge-Restore/backend/main.py)
- Modify: [tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tasks.py)
- Modify: [routers/tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/tasks.py)

- [ ] **Step 1: Implement DBLoggingHandler in database.py**
  Define a custom logging handler that filters out `sqlalchemy`, `urllib3`, `redis`, and its own INSERT statements, then logs records to the `system_logs` table.

- [ ] **Step 2: Call setup_db_logging on startup**
  Import and run `setup_db_logging()` in `backend/main.py` startup event and in `backend/tasks.py` module level (for workers).

- [ ] **Step 3: Expose GET /api/tasks/debug-logs in routers/tasks.py**
  Create an API endpoint:
  ```python
  @router.get("/debug-logs", response_model=List[schemas.SystemLogResponse])
  def get_debug_logs(db: Session = Depends(get_db)):
      return db.query(models.SystemLog).order_by(models.SystemLog.created_at.desc()).limit(200).all()
  ```

---

### Task 3: Frontend Debug Toggle terminal in LogsTab

**Files:**
- Modify: [LogsTab.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/LogsTab.tsx)

- [ ] **Step 1: Add Debug Mode Toggle**
  Add a checkbox/switch labeled "Debug Mode" at the top of the tab.
- [ ] **Step 2: Render Log Console**
  When "Debug Mode" is checked, render a monospace scrolling box displaying logs retrieved from `/api/tasks/debug-logs`. Polling interval: 3 seconds.
- [ ] **Step 3: Verify build and files size**
  Verify the typescript compiler has no warnings, the file size of `LogsTab.tsx` is under 500 lines, and run `npm run build`.
