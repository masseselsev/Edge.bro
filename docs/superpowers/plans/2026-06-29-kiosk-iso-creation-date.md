# Kiosk ISO Naming via Creation Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify Kiosk custom ISO filenames to contain the actual date/time of their creation/re-creation rather than the kiosk user record creation timestamp.

**Architecture:** Update the Celery repack task to use the current timestamp for the output filename, clear any existing ISOs for the target kiosk, and update kiosks and ISO routers to dynamically resolve the filename on disk matching the kiosk auth token.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Celery

## Global Constraints
- Custom ISO filenames must follow the pattern `{server_name}-kiosk-{iso_created_date}-{kiosk.auth_token}.iso`.
- The date component `{iso_created_date}` must represent the system date of the ISO creation/re-creation.
- Existing custom ISOs for a kiosk must be cleaned up before a new one is built.

---

### Task 1: Update Celery Repack Task Naming and Cleanup Logic

**Files:**
- Modify: [backend/iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)

**Interfaces:**
- Consumes: None
- Produces: Dynamic ISO generation using system datetime and cleaning up existing kiosk-specific ISO files.

- [ ] **Step 1: Write changes to backend/iso_tasks.py**
  Modify the `repack_kiosk_iso_task` function in [backend/iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py#L569-L572) to clear out existing custom ISOs matching the target kiosk's token and construct the new filename using the current date.
  Target content:
  ```python
          server_name = settings.server_name if (settings and settings.server_name) else "Edge.bro"
          created_date = kiosk.created_at.strftime("%Y%m%d") if kiosk.created_at else "unknown"
          output_kiosk_iso = os.path.join(history_dir, f"{server_name}-kiosk-{created_date}-{kiosk.auth_token}.iso")
  ```
  Replacement content:
  ```python
          server_name = settings.server_name if (settings and settings.server_name) else "Edge.bro"
          
          # Clean up any existing ISO files for this kiosk token first to ensure clean generation and save space
          for file in os.listdir(history_dir):
              if file.endswith(f"-{kiosk.auth_token}.iso") and "-kiosk-" in file:
                  try:
                      os.remove(os.path.join(history_dir, file))
                  except Exception:
                      pass
                      
          from datetime import datetime
          created_date = datetime.now().strftime("%Y%m%d")
          output_kiosk_iso = os.path.join(history_dir, f"{server_name}-kiosk-{created_date}-{kiosk.auth_token}.iso")
  ```

- [ ] **Step 2: Verify code changes compile**
  Run: `python -m py_compile backend/iso_tasks.py`
  Expected: Successful compilation (no output)

---

### Task 2: Update Routers to Dynamically Locate ISO Files on Disk

**Files:**
- Modify: [backend/routers/kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py)
- Modify: [backend/routers/iso.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/iso.py)

**Interfaces:**
- Consumes: Dynamic ISO generation from Task 1.
- Produces: Kiosk response structures and file responses matching the dynamic filenames.

- [ ] **Step 1: Update list_kiosks in backend/routers/kiosks.py**
  Modify [backend/routers/kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py#L76-L95) to scan the history directory for any ISO ending with the kiosk's token.
  Target content:
  ```python
      for k in kiosks:
          if k.auth_token:
              created_date = k.created_at.strftime("%Y%m%d") if k.created_at else "unknown"
              iso_name = f"{server_name}-kiosk-{created_date}-{k.auth_token}.iso"
              iso_path = os.path.join(CACHE_DIR, "history", iso_name)
              exists = os.path.exists(iso_path)
              k.iso_exists = exists
              if exists:
                  k.iso_path = iso_path
                  k.iso_name = iso_name
                  k.iso_size = os.path.getsize(iso_path)
              else:
                  k.iso_path = None
                  k.iso_name = None
                  k.iso_size = None
          else:
              k.iso_exists = False
              k.iso_path = None
              k.iso_name = None
              k.iso_size = None
  ```
  Replacement content:
  ```python
      for k in kiosks:
          if k.auth_token:
              iso_name = None
              iso_path = None
              exists = False
              history_dir = os.path.join(CACHE_DIR, "history")
              if os.path.exists(history_dir):
                  suffix = f"-{k.auth_token}.iso"
                  for file in os.listdir(history_dir):
                      if file.endswith(suffix) and "-kiosk-" in file:
                          iso_name = file
                          iso_path = os.path.join(history_dir, file)
                          exists = True
                          break
              k.iso_exists = exists
              if exists:
                  k.iso_path = iso_path
                  k.iso_name = iso_name
                  k.iso_size = os.path.getsize(iso_path)
              else:
                  k.iso_path = None
                  k.iso_name = None
                  k.iso_size = None
          else:
              k.iso_exists = False
              k.iso_path = None
              k.iso_name = None
              k.iso_size = None
  ```

- [ ] **Step 2: Update download_kiosk_iso in backend/routers/iso.py**
  Modify [backend/routers/iso.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/iso.py#L351-L357) to dynamically resolve the output filename based on the token pattern.
  Target content:
  ```python
      from iso_tasks import CACHE_DIR
      created_date = kiosk.created_at.strftime("%Y%m%d") if kiosk.created_at else "unknown"
      filename = f"{server_name}-kiosk-{created_date}-{kiosk.auth_token}.iso"
      iso_path = os.path.join(CACHE_DIR, "history", filename)
      if not os.path.exists(iso_path):
          raise HTTPException(status_code=404, detail="ISO image has been pruned from cache. Re-create it first.")
  ```
  Replacement content:
  ```python
      from iso_tasks import CACHE_DIR
      filename = None
      history_dir = os.path.join(CACHE_DIR, "history")
      if os.path.exists(history_dir):
          suffix = f"-{kiosk.auth_token}.iso"
          for file in os.listdir(history_dir):
              if file.endswith(suffix) and "-kiosk-" in file:
                  filename = file
                  break
      if not filename:
          raise HTTPException(status_code=404, detail="ISO image has been pruned from cache. Re-create it first.")
      iso_path = os.path.join(history_dir, filename)
  ```

- [ ] **Step 3: Verify code compiles**
  Run: `python -m py_compile backend/routers/kiosks.py backend/routers/iso.py`
  Expected: Successful compilation (no output)

---

### Task 3: Verify and Test the Whole Flow

**Files:**
- Modify: [backend/tests/test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py)

**Interfaces:**
- Consumes: Dynamic ISO resolution logic from Task 2.
- Produces: Clean passing test suite.

- [ ] **Step 1: Run standard kiosk unit tests**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_kiosks.py -v`
  Expected: PASS

- [ ] **Step 2: Add integration test for dynamic download/listing**
  Add a new test `test_kiosk_iso_dynamic_naming_and_download` at the end of [backend/tests/test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py):
  ```python
  def test_kiosk_iso_dynamic_naming_and_download(db_session):
      import os
      import shutil
      from fastapi.testclient import TestClient
      from main import app
      import models
      from iso_tasks import CACHE_DIR
  
      client = TestClient(app)
      
      # Mock base settings and kiosk
      settings = db_session.query(models.Settings).first()
      if not settings:
          settings = models.Settings(server_name="Edge.bro")
          db_session.add(settings)
      else:
          settings.server_name = "Edge.bro"
      
      kiosk = models.Kiosk(
          name="Test Dynamic Kiosk",
          uuid="KS8888",
          key="8888KS",
          auth_token="TEST88",
          status="APPROVED"
      )
      db_session.add(kiosk)
      db_session.commit()
  
      # 1. Create a dummy file in the history cache matching today's date
      history_dir = os.path.join(CACHE_DIR, "history")
      os.makedirs(history_dir, exist_ok=True)
      
      dummy_filename = "Edge.bro-kiosk-20260629-TEST88.iso"
      dummy_path = os.path.join(history_dir, dummy_filename)
      with open(dummy_path, "w") as f:
          f.write("mock iso content")
  
      try:
          # Verify list_kiosks dynamically locates the file
          from routers.kiosks import list_kiosks
          kiosks_list = list_kiosks(db=db_session)
          target = next(k for k in kiosks_list if k.id == kiosk.id)
          assert target.iso_exists is True
          assert target.iso_name == dummy_filename
          assert target.iso_size == len("mock iso content")
          
          # Verify download_kiosk_iso serves the dynamically matched file
          from routers.iso import download_kiosk_iso
          resp = download_kiosk_iso(id=kiosk.id, db=db_session)
          assert resp.path == dummy_path
          assert resp.filename == dummy_filename
      finally:
          if os.path.exists(dummy_path):
              os.remove(dummy_path)
  ```

- [ ] **Step 3: Run the new test case**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_kiosks.py -k test_kiosk_iso_dynamic_naming_and_download -v`
  Expected: PASS

- [ ] **Step 4: Run the complete test suite**
  Run: `PYTHONPATH=. venv/bin/pytest tests/ -v`
  Expected: PASS (All tests pass successfully)

- [ ] **Step 5: Commit**
  ```bash
  git add backend/iso_tasks.py backend/routers/kiosks.py backend/routers/iso.py backend/tests/test_kiosks.py
  git commit -m "feat: use system creation date in kiosk ISO naming and dynamically locate files by token"
  ```
