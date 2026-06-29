# Design Spec: Kiosk ISO Naming via Creation Date

## Goal
Make Kiosk custom ISO filenames reflect the date the ISO was actually generated (or re-created) instead of the date the kiosk pairing record was created.

## Architecture & Components

1. **Celery Repack Task (`backend/iso_tasks.py`)**:
   - Clean up any existing `.iso` files matching `*-{kiosk.auth_token}.iso` in the `history/` directory before generating a new one to avoid leaving stale or duplicated images.
   - Use `datetime.now().strftime("%Y%m%d")` to format the current date.
   - Build the output filename using this current date.

2. **Kiosks Router (`backend/routers/kiosks.py`)**:
   - In `list_kiosks`, search the `history/` directory for any file ending with `-{k.auth_token}.iso`.
   - If found, extract its name, verify it exists, and get its file size dynamically.

3. **ISO Router (`backend/routers/iso.py`)**:
   - In `download_kiosk_iso`, search the `history/` directory for any file ending with `-{kiosk.auth_token}.iso` and return that file.

## Verification Plan
1. Run `pytest tests/test_kiosks.py` to ensure baseline kiosk CRUD tests pass.
2. Verify that creating a kiosk initiates the repack task with the correct date.
3. Verify that re-creating the ISO updates the filename date to the current date.
