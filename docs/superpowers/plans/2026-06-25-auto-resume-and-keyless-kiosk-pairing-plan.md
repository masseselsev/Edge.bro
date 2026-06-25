# Auto-Resume of Base ISO Downloads & Key-less Kiosk Pairing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure interrupted Base ISO downloads automatically resume on startup without losing partial progress, and simplify kiosk registration by moving from a pairing-key-based flow to a server-side click approval flow.

**Architecture:**
- **Auto-Resume**: Remove `.tmp` cleanup in `main.py`'s startup. Add checks for `base.iso.tmp` existence when `base.iso` is missing, re-locking and re-queueing the download task.
- **Key-less Pairing**: Update `/api/kiosks/enroll` to generate and return `auth_token` immediately in `PENDING` state. Update kiosk client backend to save this token, update state, and start check-in loop. Update orchestrator UI to approve via click, authorizing the SSH key in `authorized_keys`.

---

## Proposed Changes

### Task 1: Auto-Resume Logic in Backend Startup and Cache Clear
- [ ] **Step 1: Modify backend/main.py**
  Remove lines deleting `/opt/data/iso_cache/base.iso.tmp` on startup. Add code checking if `base.iso` is missing and `base.iso.tmp` exists, creating `download.lock` and triggering `download_base_iso_task.delay()`.
- [ ] **Step 2: Modify backend/routers/iso.py**
  Update `clear_base_iso` to delete `/opt/data/iso_cache/base.iso.tmp` if present.

### Task 2: Backend Key-less Enrollment and Activation Approval
- [ ] **Step 1: Modify backend/routers/kiosks.py**
  - Update `enroll_kiosk` to generate `auth_token` immediately and return it under `auth_token` key.
  - Update `toggle_kiosk_active`:
    - When status changes to `APPROVED`, authorize the kiosk's SSH public key using `authorize_ssh_key()`.
    - When status changes to `DISABLED`, revoke the kiosk's SSH public key using `revoke_ssh_key()`.
- [ ] **Step 2: Update backend/tests/test_kiosks.py**
  Ensure tests pass and verify behavior.

### Task 3: Kiosk Client Backend Enrollment Logic
- [ ] **Step 1: Modify payload_client/backend/main.py**
  In `enroll_client_kiosk`, parse `auth_token` from response, store in state and `config.json`, and start the check-in thread.

### Task 4: Translations and Frontend Refactoring
- [ ] **Step 1: Modify frontend/src/i18n/translations.ts**
  - Update `enrollStatusPending` translations.
  - Update `registerKioskHint` translations.
  - Add `kioskApprovePrompt` translations.
- [ ] **Step 2: Modify frontend/src/App.tsx**
  - In `handleEnrollSubmit`: do not set pairingMode to `'connect'`.
  - In pairing modal: render `enrollMsg` if `pairingMode === 'enroll'`.
  - In pending connection review modal: replace key display with connection approval prompt. Add "Approve & Activate" button calling `toggle-active` endpoint.
- [ ] **Step 3: Modify KioskManagementSection.tsx**
  - Remove "+ Register Kiosk" button.
  - Remove the Add Kiosk Modal.
  - Update empty table hint to use `registerKioskHint`.

---

## Verification Plan

### Automated Tests
- Run:
  ```bash
  pytest backend/tests/test_kiosks.py
  ```

### Manual Verification
- Compile/lint frontend to verify compile success:
  ```bash
  npm run build --prefix frontend
  ```
