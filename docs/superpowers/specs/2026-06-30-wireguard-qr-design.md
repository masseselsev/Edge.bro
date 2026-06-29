# WireGuard VPN Configuration & QR Code Scanner Design Spec

Support technicians boot physical kiosks from the LiveCD and need to connect via a WireGuard tunnel. They must be able to scan a QR code representing a standard `.conf` file to configure the tunnel on the fly.

## User Review Required

> [!IMPORTANT]
> The browser webcam feed requires an HTTPS context or localhost to function due to browser security restrictions (`getUserMedia` API). Since the kiosk client is accessed via `http://127.0.0.1:8000` (localhost) when run locally on the kiosk, it will work perfectly. If accessed remotely over the network, it must be accessed via an HTTPS connection (or browser flags must be set to allow insecure origins for media capture).

---

## Architectural Proposal

### 1. Frontend Modifications
- **Network Settings Modal:** Add a new tab `VPN (WireGuard)` to the `NetworkSettingsModal.tsx` component.
- **QR Code Scanner UI:**
  - When no configuration is loaded, show options to "Scan QR Code" or "Paste Config Text".
  - If "Scan QR Code" is clicked, render a `<video>` preview element inline.
  - Use `jsqr` (a lightweight JS QR-code reader library) to analyze the video canvas frames at 10-15 FPS.
  - On successful scan, parse the decoded standard `.conf` structure, show a brief validation confirmation, and close the camera stream.
- **Manual Input:** Fallback `<textarea>` for pasting the configuration file manually.
- **Active Dashboard:**
  - Displays the active tunnel status: State (Connected/Disconnected), Interface IP, Peer Endpoint, Allowed IPs, Sent/Received bytes, and Last Handshake.

### 2. Backend Integration (`backend/routers/network.py`)
Add the following endpoints to the shared network router:
- **`GET /api/network/vpn/status`**: 
  - Retrieves current tunnel status. Parses output of `wg show wg0 dump` or `/sys/class/net/wg0/` configuration details to output:
    - `connected` (bool)
    - `ip` (str)
    - `endpoint` (str)
    - `allowed_ips` (str)
    - `received_bytes` (int)
    - `sent_bytes` (int)
    - `last_handshake` (int)
- **`POST /api/network/vpn`**:
  - Accept `config_text: str`.
  - Write it to the persistent USB directory `/media/usb-data/wg0.conf` (if running in kiosk mode) or `/data/wg0.conf`.
  - Copy it to `/etc/wireguard/wg0.conf` and bring the tunnel up via `wg-quick up wg0`.
- **`POST /api/network/vpn/connect`** / **`POST /api/network/vpn/disconnect`**:
  - Toggles the tunnel up/down state using `wg-quick up wg0` / `wg-quick down wg0`.
- **`DELETE /api/network/vpn`**:
  - Shuts down the tunnel, deletes `/etc/wireguard/wg0.conf`, and deletes the persistent file `/media/usb-data/wg0.conf`.

### 3. Kiosk Boot Autostart Integration
- **`kiosk-vpn-setup.service`**:
  - Add a systemd one-shot service running at boot (After `kiosk-storage-setup.service` which mounts `/media/usb-data/`).
  - If `/media/usb-data/wg0.conf` is found, copy it to `/etc/wireguard/wg0.conf` and execute `wg-quick up wg0` to auto-establish the tunnel at boot.

### 4. Dependencies & Packaging
- Modify `docker/backend/Dockerfile` to download `wireguard-tools` and `openresolv` (or `resolvconf`) `.deb` packages to `/opt/offline-packages` so they are copied and installed inside the LiveCD during ISO repackaging.

---

## Verification Plan

### Automated Verification
- Unit tests in `backend/tests/test_network.py` verifying status parser logic from simulated `wg show wg0 dump` output.
- Unit tests verifying profile creation, deletion, and connection toggle endpoint routing.

### Manual Verification
- Deploy to Kiosk Client, select VPN tab, scan a mockup QR code, and verify that the tunnel connects and statistics are parsed.
