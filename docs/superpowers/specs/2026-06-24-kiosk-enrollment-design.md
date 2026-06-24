# Server-Kiosk Dynamic Enrollment and Persistent USB Storage Spec

## Goal Description

This specification outlines the enhancements to the Server-Kiosk connection system and the Live-USB client storage persistence. The goals are:
1. **USB persistence on first boot (Option 1)**: Format all unused space on the USB drive dynamically on the first boot to mount a persistent ext4 partition (`/media/usb-data`) for storing the pairing config and offline backups.
2. **Simplified, case-insensitive pairing key**: Change the 8-character `XXXX-XXXX` pairing code to a 6-character `1234AB` (4 digits + 2 letters) format, verified case-insensitively.
3. **Multiple Server IP Address selection**: Allow administrators to configure multiple server IP addresses in the global settings, baked into the ISO, letting the kiosk operator choose an IP from a dropdown or enter a custom one.
4. **Dynamic Enrollment Flow**: Enable new, unapproved kiosks to dynamically request connection to the server by providing their Name, Phone, and Comment. The server displays a global dashboard notification banner and approval modal containing the generated pairing key.

---

## Proposed Design Details

### 1. USB Storage Auto-Partitioning & Mounting (Kiosk Client-side)
A new script `/opt/offline-client/kiosk-storage-setup.sh` will run before the main kiosk backend (`offline-backend.service`) starts.
- **Check existing partition**: Look for a partition labeled `kiosk-data` (via `/dev/disk/by-label/kiosk-data`).
  - If found: Mount it to `/media/usb-data`, verify/restore configuration symlinks, and exit.
  - If not found:
    - Locate the boot media partition by reading the mount source of `/run/live/medium` (e.g., `/dev/sdb1`).
    - Extract the parent block device (e.g., `/dev/sdb`).
    - Run `parted -s /dev/sdb print` to fix/relocate backup GPT headers if needed.
    - Read partition bounds, then create a primary `ext4` partition starting from the end of the last partition to `100%`.
    - Run `partprobe` and format the new partition as `ext4` with label `kiosk-data`.
    - Mount `/dev/disk/by-label/kiosk-data` to `/media/usb-data` and create required folders (`/media/usb-data/.ssh` and `/media/usb-data/borg/fleet`).
    - Move existing `config.json` to `/media/usb-data/config.json` and create a symlink `/opt/offline-client/backend/config.json -> /media/usb-data/config.json`.
    - Symlink `/opt/offline-client/backend/id_ed25519` to `/media/usb-data/.ssh/id_ed25519` to persist the kiosk's SSH identity.
- **Integration**:
  - Run via a new systemd unit `/etc/systemd/system/kiosk-storage-setup.service` ordered `Before=offline-backend.service`.

### 2. Multi-IP Server Settings (Orchestrator & Kiosk Side)
- **Database Schema**: Add `server_ips` (JSON column, default `[]`) to the `settings` table.
- **Settings UI**: Allow admins to manage a list of server IPs/domains under the Orchestrator settings tab.
- **ISO Generation**: Read `server_ips` from settings and bake it into the client's `config.json` as `available_server_ips`.
- **Kiosk UI**: The "Orchestrator IP" input will be a dropdown list containing `available_server_ips`, with an option to type in a custom address.

### 3. Simplified Case-Insensitive Pairing Key
- **Format**: 4 numbers followed by 2 letters (e.g., `4819XB`).
- **Logic**: Use `random.choice` on digits and alphabets, excluding ambiguous chars (O, 0, I, 1, L).
- **Handshake API**: Clean user input key, convert to uppercase (`req.key.strip().upper()`), and compare case-insensitively against the DB record.

### 4. Dynamic Kiosk Connection Request API
- **Model additions**: Add `phone` (String) and `comment` (Text) columns to the `kiosks` table.
- **API `POST /api/kiosks/enroll`**:
  - Receives `uuid`, `name`, `phone`, `comment`, and `ssh_pub_key`.
  - Creates a new kiosk record in `PENDING` state with a newly generated simplified pairing key.
- **Handshake API**: Once the user provides the pairing key from the server dashboard, the kiosk issues `POST /api/kiosks/handshake` to retrieve the `auth_token` and complete connection.

### 5. Server Dashboard Approval Banner & Modal
- **Polling**: Main orchestrator frontend polls `/api/kiosks` to detect any pending kiosks.
- **Banner**: Displays a header banner alert: *"Kiosk connection request from [Name] ([Phone])."*
- **Approval Modal**: Opens to show the operator's details and the pairing key (e.g., `4819XB`) in large monospace font with a copy button.

---

## Verification Plan

### Automated Tests
- Write python unit tests in `backend/tests/test_kiosks.py` verifying:
  - Dynamic enrollment request inserts kiosk record with status `PENDING` and correct metadata.
  - Generates simplified key of type `\d{4}[A-Z]{2}`.
  - Handshake verification matches key case-insensitively and updates status to `APPROVED`.
  - Settings CRUD endpoints support saving and retrieving `server_ips` array.

### Manual Verification
- Deploy local dev docker environment.
- Access Orchestrator UI, add secondary IP address to Settings.
- Trigger ISO generation, verify that generated configuration payload contains multiple IPs.
- Issue mock enrollment request from curl to ensure notification banner and modal appear on the dashboard.
- Verify partitioning script behaves correctly when simulated on a virtual block device.
