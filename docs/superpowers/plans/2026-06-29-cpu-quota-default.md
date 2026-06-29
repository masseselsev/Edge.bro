# Default CPU Quota 30% Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase the default CPU quota limit for Borg backups from 10% to 30% on both new and existing environments.

**Architecture:** Update the default CPU quota setting in SQLAlchemy Settings model and Pydantic schema, and add a database startup upgrade migration to transition existing 10% defaults to 30%.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Pytest

## Global Constraints
- Target CPU quota limit default must be exactly `30%`.
- Existing installations with CPU quota set to `10%` must be upgraded to `30%` automatically at startup.

---

### Task 1: Update Settings Model and Schema Defaults

**Files:**
- Modify: [backend/models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
- Modify: [backend/schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Modify: [backend/tests/test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py)

**Interfaces:**
- Consumes: None
- Produces: Default value `30` for `default_cpu_quota` inside database settings.

- [ ] **Step 1: Write the failing test**
  Modify [backend/tests/test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py#L338) to change the assertion that the default value is `None` to `30`.
  Target content:
  ```python
      assert settings.default_cpu_quota is None
  ```
  Replacement content:
  ```python
      assert settings.default_cpu_quota == 30
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_db.py::test_create_settings -v`
  Expected: FAIL with `AssertionError: assert None == 30`

- [ ] **Step 3: Write minimal implementation**
  Modify [backend/models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py#L24) to set the column default value to `30`:
  ```python
      default_cpu_quota = Column(Integer, default=30, nullable=True)   # % of one core, NULL = no limit
  ```
  Modify [backend/schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py#L27) to set the Pydantic default value to `30`:
  ```python
      default_cpu_quota: Optional[int] = Field(default=30, ge=0, le=400)
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_db.py::test_create_settings -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add backend/models.py backend/schemas.py backend/tests/test_db.py
  git commit -m "feat: set default CPU quota to 30% in models and schemas"
  ```

---

### Task 2: Implement Startup Database Settings Upgrade

**Files:**
- Modify: [backend/main.py](file:///home/masse/projects/Backup-edge-Restore/backend/main.py)
- Modify: [backend/tests/test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py)

**Interfaces:**
- Consumes: Default CPU quota configuration from Task 1.
- Produces: Startup database upgrade mechanism for `default_cpu_quota` settings.

- [ ] **Step 1: Write database upgrade logic in main.py**
  Modify `upgrade_settings(db: Session)` in [backend/main.py](file:///home/masse/projects/Backup-edge-Restore/backend/main.py#L123-L132) to automatically migrate database settings with `default_cpu_quota == 10` to `30`.
  Target content:
  ```python
  def upgrade_settings(db: Session):
      """
      Upgrade old default exclusions to the new default if unchanged by user.
      """
      settings = db.query(models.Settings).first()
      if not settings:
          settings = models.Settings()
          db.add(settings)
          db.commit()
      else:
  ```
  Replacement content:
  ```python
  def upgrade_settings(db: Session):
      """
      Upgrade old default exclusions to the new default if unchanged by user.
      """
      settings = db.query(models.Settings).first()
      if not settings:
          settings = models.Settings()
          db.add(settings)
          db.commit()
      else:
          if settings.default_cpu_quota == 10:
              settings.default_cpu_quota = 30
              db.commit()
              print("Upgraded default_cpu_quota setting from 10% to 30%.")
  ```

- [ ] **Step 2: Add test case for settings upgrade migration**
  Add the following test function at the end of [backend/tests/test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py):
  ```python
  def test_upgrade_settings_cpu_quota():
      from database import SessionLocal
      from main import upgrade_settings
      import models
  
      db = SessionLocal()
      try:
          settings = db.query(models.Settings).first()
          if not settings:
              settings = models.Settings()
              db.add(settings)
          settings.default_cpu_quota = 10
          db.commit()
  
          upgrade_settings(db)
  
          db.refresh(settings)
          assert settings.default_cpu_quota == 30
      finally:
          db.close()
  ```

- [ ] **Step 3: Run the test to verify it passes**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_db.py -k test_upgrade_settings_cpu_quota -v`
  Expected: PASS

- [ ] **Step 4: Commit**
  ```bash
  git add backend/main.py backend/tests/test_db.py
  git commit -m "feat: upgrade default_cpu_quota from 10 to 30 on startup"
  ```
