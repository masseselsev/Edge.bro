# Auto-Resume of Base ISO Downloads & Key-less Kiosk Pairing

## Goal Description

This design specification details two key improvements to the Edge B.R.O. system:
1. **Auto-Resume of Base ISO Download on Startup**: Ensure that if the backend container or server restarts during a Base ISO download, the downloaded data is not deleted, and the download task is automatically resumed from where it left off.
2. **Key-less Dynamic Kiosk Connection Flow**: Simplify the dynamic kiosk enrollment process by removing the manually entered pairing key entirely. When a kiosk client requests enrollment, the server registers it as `PENDING` and returns an authentication token immediately. The kiosk client automatically begins polling, and once the administrator approves/activates the connection request via the server dashboard, SSH access is authorized, and the kiosk transitions to `APPROVED` / online mode.

---

## Proposed Changes

### 1. Backend Auto-Resume Logic

We will modify how the backend startup routine handles the cached temporary ISO files:
- **Location**: [backend/main.py](file:///home/masse/projects/Backup-edge-Restore/backend/main.py)
- **Change**: Stop deleting the `base.iso.tmp` file during startup.
- **Trigger**: Check if `base.iso` does not exist but `base.iso.tmp` exists. If so, create the `download.lock` file and call `download_base_iso_task.delay()` automatically.
- **Clear Endpoint**: In [backend/routers/iso.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/iso.py), update the cache clearing endpoint (`DELETE /api/iso/base`) to explicitly delete `base.iso.tmp` when the user requests clearing the cache.

### 2. Key-less Dynamic Kiosk Connection Flow

We will transition the dynamic enrollment flow from a pairing-key-based system to an approval-based token validation system:
- **Server Enrollment API**: In [backend/routers/kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py), update the `/api/kiosks/enroll` endpoint to generate a unique `auth_token` immediately for the enrolling kiosk and return it in the response (alongside status `PENDING`).
- **Client Enrollment Backend**: In [payload_client/backend/main.py](file:///home/masse/projects/Backup-edge-Restore/payload_client/backend/main.py), update the `/api/kiosk/enroll` endpoint to read the `auth_token` from the response, save it to `config.json`, configure the client's runtime state to `PENDING` (in `offline` mode), and start the background auto-handshake check-in thread.
- **Approval Logic**: In [backend/routers/kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py)'s `/api/kiosks/{id}/toggle-active` endpoint:
  - If a kiosk is approved (moved to `APPROVED`), authorize its SSH key using `authorize_ssh_key()`.
  - If a kiosk is disabled (moved to `DISABLED`), revoke its SSH key using `revoke_ssh_key()`.
- **Server UI**:
  - Remove the "+ Register Kiosk" button and its associated Add Modal code in [KioskManagementSection.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/KioskManagementSection.tsx).
  - Update `registerKioskHint` translations to guide the user on how to connect new kiosks by initiating requests from the kiosk client screen.
- **Client UI**:
  - In the connection request modal within [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), display a success message and keep the user on the enrollment page instead of prompting them to enter a pairing key.
- **Server Dashboard Request Review Modal**:
  - In [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), add an "Approve & Activate" button to the pending connection modal that calls the `toggle-active` endpoint, allowing the administrator to authorize the kiosk directly.

---

## Verification Plan

### Automated Tests
- Run backend tests in [backend/tests/test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py) to ensure registration and authorization work as expected.

### Manual Verification
1. Simulate a container restart during a Base ISO download. Verify that the `.tmp` file size is preserved and the Celery task auto-resumes the download.
2. Open a generic kiosk client, enter the server IP, and submit an enrollment request.
3. Verify that the dashboard shows the pending notification banner and the review modal.
4. Verify that clicking "Approve" authorized the kiosk's SSH public key on the server.
5. Verify that the kiosk automatically updates its status to active/online upon the next poll.
