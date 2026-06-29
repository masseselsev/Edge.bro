# WireGuard VPN Configuration & QR Code Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable support technicians to configure and run WireGuard VPN connections on the LiveCD kiosk by scanning a QR code with their camera or pasting the `.conf` profile manually, persisting the configuration to the USB drive.

**Architecture:** The React frontend captures video frames using `getUserMedia` and parses standard WireGuard `.conf` profiles from scanned QR codes in the browser via `jsqr`. The shared network router handles tunnel creation, configuration management, state queries, and systemd autostart at boot.

**Tech Stack:** React, TypeScript, Tailwind CSS, Lucide Icons, FastAPI (Python), `jsqr` (JS), `wireguard-tools` (Linux).

## Global Constraints
- Target python version: Python 3.13-slim
- All dropdowns and modal components must use transition animations
- All configuration files must support persistence on the USB drive

---

### Task 1: Add QR Scanning Library to Frontend

**Files:**
- Modify: [frontend/package.json](file:///home/masse/projects/Backup-edge-Restore/frontend/package.json)
- Modify: [frontend/package-lock.json](file:///home/masse/projects/Backup-edge-Restore/frontend/package-lock.json)

**Interfaces:**
- Consumes: None
- Produces: `jsqr` dependency in `node_modules`

- [ ] **Step 1: Edit package.json**
  Add `"jsqr": "^1.4.0"` to `"dependencies"` in [frontend/package.json](file:///home/masse/projects/Backup-edge-Restore/frontend/package.json).

- [ ] **Step 2: Run npm install**
  Run `npm install` inside `/home/masse/projects/Backup-edge-Restore/frontend` to download and lock dependencies.
  Run: `npm run build` to verify compiling works.

- [ ] **Step 3: Commit changes**
  ```bash
  git add frontend/package.json frontend/package-lock.json
  git commit -m "chore(deps): add jsqr dependency for client QR code scanner"
  ```

---

### Task 2: Implement QR Code Scanner & WireGuard UI Tab

**Files:**
- Modify: [frontend/src/components/NetworkSettingsModal.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/NetworkSettingsModal.tsx)

**Interfaces:**
- Consumes: `jsqr`
- Produces: `VPN (WireGuard)` tab in `NetworkSettingsModal`

- [ ] **Step 1: Import dependencies & declare interface types**
  Add `jsQR` import at the top of the file:
  ```typescript
  import jsQR from 'jsqr';
  ```
  Define states for WireGuard status:
  ```typescript
  interface VpnStatus {
    connected: boolean;
    ip: string | null;
    endpoint: string | null;
    allowed_ips: string | null;
    received_bytes: number;
    sent_bytes: number;
    last_handshake: number;
  }
  ```

- [ ] **Step 2: Add UI tab layout and rendering logic**
  Add `"vpn"` to `activeTab` union state:
  ```typescript
  const [activeTab, setActiveTab] = useState<'wired' | 'wifi' | 'vpn'>('wired');
  ```
  Render the vpn configuration interface in the main modal body. Include custom camera preview video elements and a `<canvas>` element for rendering scanned frames.

- [ ] **Step 3: Implement QR frame capture loop**
  Implement browser webcam activation using `navigator.mediaDevices.getUserMedia` and a processing loop utilizing `jsQR` to search for QR codes on the hidden canvas. Parse standard Ini-like files containing `[Interface]` and `[Peer]` blocks.

- [ ] **Step 4: Verify frontend build compiles successfully**
  Run: `npm run build` inside `/home/masse/projects/Backup-edge-Restore/frontend`.
  Expected: Success without TypeScript or bundling warnings.

- [ ] **Step 5: Commit changes**
  ```bash
  git add frontend/src/components/NetworkSettingsModal.tsx
  git commit -m "feat(ui): add WireGuard configuration tab with QR scanner and manual text parser"
  ```

---

### Task 3: Backend WireGuard Status & Configuration APIs

**Files:**
- Modify: [backend/routers/network.py](file:///home/masse/projects/Backup-edge-Restore/backend/routers/network.py)
- Create: [backend/tests/test_wireguard.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_wireguard.py)

**Interfaces:**
- Consumes: None
- Produces: FastAPI router paths under `/api/network/vpn`

- [ ] **Step 1: Write failing unit test**
  Create [backend/tests/test_wireguard.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_wireguard.py) to test that:
  - `GET /api/network/vpn/status` correctly handles status check requests.
  - `POST /api/network/vpn` accepts config content, stores it on the filesystem, and connects.
  - `DELETE /api/network/vpn` cleans up profile paths and turns off the interface.

- [ ] **Step 2: Run test and verify it fails**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_wireguard.py -v`
  Expected: Fail with `404 Not Found` for routes.

- [ ] **Step 3: Implement endpoints in network.py**
  Add endpoints `/vpn/status`, `/vpn` (POST), `/vpn/connect` (POST), `/vpn/disconnect` (POST), `/vpn` (DELETE) inside `routers/network.py`.
  Read and write configurations to `/media/usb-data/wg0.conf` or `/etc/wireguard/wg0.conf`. Use shell sub-processes calling `wg show wg0 dump`, `wg-quick up wg0`, and `wg-quick down wg0` to check and change statuses.

- [ ] **Step 4: Run unit tests to verify they pass**
  Run: `PYTHONPATH=. venv/bin/pytest tests/test_wireguard.py -v`
  Expected: Pass.

- [ ] **Step 5: Commit changes**
  ```bash
  git add backend/routers/network.py backend/tests/test_wireguard.py
  git commit -m "feat(api): add backend API endpoints for WireGuard status and tunnel control"
  ```

---

### Task 4: Autostart Service Configuration & Kiosk ISO Integration

**Files:**
- Create: [payload_client/systemd/kiosk-vpn-setup.service](file:///home/masse/projects/Backup-edge-Restore/payload_client/systemd/kiosk-vpn-setup.service)
- Modify: [backend/iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)
- Modify: [docker/backend/Dockerfile](file:///home/masse/projects/Backup-edge-Restore/docker/backend/Dockerfile)

**Interfaces:**
- Consumes: Backend api and backend configuration setup
- Produces: `kiosk-vpn-setup.service` symlink in LiveCD systemd target

- [ ] **Step 1: Create systemd autostart unit**
  Create [payload_client/systemd/kiosk-vpn-setup.service](file:///home/masse/projects/Backup-edge-Restore/payload_client/systemd/kiosk-vpn-setup.service):
  ```ini
  [Unit]
  Description=Restore WireGuard VPN Connection
  After=kiosk-storage-setup.service
  
  [Service]
  Type=oneshot
  RemainAfterExit=yes
  ExecStart=/bin/sh -c 'if [ -f /media/usb-data/wg0.conf ]; then cp /media/usb-data/wg0.conf /etc/wireguard/wg0.conf && wg-quick up wg0; fi'
  
  [Install]
  WantedBy=multi-user.target
  ```

- [ ] **Step 2: Inject systemd service and packages into ISO build task**
  Modify [backend/iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py) to copy `kiosk-vpn-setup.service` into the payload `etc/systemd/system/` folder and symlink it to `multi-user.target.wants/`.

- [ ] **Step 3: Update Dockerfile to download offline packages**
  Add `wireguard-tools` and `openresolv` to the `apt-get download` list inside [docker/backend/Dockerfile](file:///home/masse/projects/Backup-edge-Restore/docker/backend/Dockerfile).

- [ ] **Step 4: Run full test suite compliance**
  Run: `PYTHONPATH=. venv/bin/pytest tests/ -v`
  Expected: All 67 tests pass.

- [ ] **Step 5: Commit and push**
  ```bash
  git add payload_client/systemd/kiosk-vpn-setup.service backend/iso_tasks.py docker/backend/Dockerfile
  git commit -m "feat(iso): configure WireGuard boot-time systemd autostart and offline packages download"
  git push origin master
  ```
