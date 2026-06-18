# Kiosk Watchdog Integration & Client Fixes Design Spec

This document details the design and architecture for adding hardware watchdog control to the technician kiosk client, resolving network configuration state reset bugs, disabling screen blanking by default, and fixing the layout scroll issues in the footer.

## 1. Goals and Scope

*   **Watchdog Control**: Auto-detect the hardware watchdog controller on COM ports (`/dev/ttyUSB0` to `/dev/ttyUSB2`) on the client complex. Display a modal dialog on discovery offering to freeze the watchdog. Add a persistent watchdog control button (Freeze/Unfreeze) with visual status indication in the client footer.
*   **Footer Scroll Fix**: Fix the kiosk layout so the footer is always pinned to the bottom of the viewport (`fixed bottom-0`) and does not scroll out of view.
*   **Network Settings Form Reset Bug**: Fix the modal form so background status polls do not overwrite the user's input/selection when configuring a static IP.
*   **Live-CD DHCP Failures**: Enable NetworkManager management for pre-configured live-cd interfaces by setting `managed=true` in `NetworkManager.conf`. Auto-create default ethernet connections in NetworkManager if missing.
*   **Screen Sleep Disabling**: Turn off screensaver, locking, and display power management (DPMS) in the user session using XFCE configuration tools (`xfconf-query`) to prevent screen blanking when idle.

---

## 2. Technical Architecture

### Backend: Watchdog Router (`routers/watchdog.py`)
A new dedicated FastAPI router under `payload_client/backend/routers/watchdog.py` will expose three endpoints:
1.  `GET /api/kiosk/watchdog/status`: Scans `/dev/ttyUSB0` to `/dev/ttyUSB2` at 19200 baud.
    *   To scan, it sends the Modbus RTU read command `30 03 00 00 00 01 80 2B` (read `pc_wdt` register).
    *   If a 7-byte reply with correct CRC is received, the controller is present.
    *   It also reads the `REG_VSM_FROZEN_REQ` status (from reading coils at `0x0000` or querying the coil value directly).
    *   Returns: `{ "detected": true, "port": "/dev/ttyUSBX", "seconds_left": int, "frozen": bool }`.
2.  `POST /api/kiosk/watchdog/freeze`: Writes `0xFF00` to Coil `0x0007` (freeze request) and writes `0x0000` to Register `0x0000` (reset WDT to 120s).
3.  `POST /api/kiosk/watchdog/unfreeze`: Writes `0x0000` to Coil `0x0007` (unfreeze request).

We will implement standard Modbus CRC16 in Python to verify received check-sums and format outgoing command envelopes.

### Backend: ISO Generation & Dependencies
*   Add `pyserial` to `backend/requirements.txt` to install it in the build container.
*   Add `"serial"` to `packages_to_copy` in `backend/iso_tasks.py` to bundle the package in `/opt/offline-client/backend/site-packages`.

### Frontend Layout & Footer Pinned Control
*   The kiosk footer is updated with tailwind classes: `fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/90 backdrop-blur-md`.
*   The main body `<main>` is given `pb-20` to prevent content overlap with the pinned footer.
*   If `watchdog.detected` is true, a watchdog button is displayed in the footer next to the UUID, showing frozen/running state with appropriate color badges.
*   An active session-level state is maintained in `App.tsx` to display the "Watchdog Detected" popup modal once per session.

---

## 3. Detailed Component Specification

### Watchdog Controller Modbus RTU Command Mapping
*   **Read Watchdog Remaining Time**: `30 03 00 00 00 01 80 2B`
*   **Write Coil VSM_FROZEN_REQ (Freeze ON)**: `30 05 00 07 FF 00 39 DA`
*   **Write Coil VSM_FROZEN_REQ (Freeze OFF)**: `30 05 00 07 00 00 78 2A`
*   **Write Register pc_wdt_reset**: `30 06 00 00 00 00 8D EB`
*   **Read Coils (0x0000 to 0x0007)**: `30 01 00 00 00 08 3E 0C`
    *   Returns 1 byte data where Bit 7 is the `REG_VSM_FROZEN_REQ` state.

### XFCE Power Manager & Screensaver Tweak (`kiosk-launcher.sh`)
Add Xfconf commands to kiosk startup script:
```bash
if command -v xfconf-query &>/dev/null; then
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-sleep -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-off -s 0 --create -t int || true
  xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s false --create -t bool || true
  xfconf-query -c xfce4-screensaver -p /saver/enabled -s false --create -t bool || true
  xfconf-query -c xfce4-screensaver -p /lock/enabled -s false --create -t bool || true
fi
```

### Network Manager Configuration Enablement (`init-bottom-copy-payload.sh`)
Update copy hook:
```bash
if [ -f /root/etc/NetworkManager/NetworkManager.conf ]; then
  sed -i 's/managed=false/managed=true/g' /root/etc/NetworkManager/NetworkManager.conf
fi
```

### Network Settings Modal State Fix
Introduce `hasInitialized` boolean state. Only call state set setters in `fetchStatus` if `!hasInitialized`:
```typescript
const [hasInitialized, setHasInitialized] = useState(false);

const fetchStatus = async () => {
  const res = await fetch('/api/network/status');
  if (res.ok) {
    const data = await res.json();
    setStatus(data);
    if (data.wired && !hasInitialized) {
      setWiredMode(data.wired.mode || 'auto');
      setIpAddress(data.wired.ip || '');
      setNetmask(data.wired.netmask || '255.255.255.0');
      setGateway(data.wired.gateway || '');
      setDnsMode(data.wired.dns_mode || 'auto');
      ...
      setHasInitialized(true);
    }
  }
};
```

---

## 4. Verification Plan

1.  **Unit / Integration Testing**:
    *   Verify that `calculate_crc` returns correct Modbus checksum bytes.
    *   Ensure `/api/kiosk/watchdog/status` handles missing COM ports gracefully by returning `detected: false`.
2.  **UI Verification**:
    *   Ensure the footer stays locked at the bottom of the browser viewport when scrolling page contents.
    *   Verify that selecting "Static IP" does not revert back to "Dynamic DHCP" on status polling.
3.  **End-to-End LiveCD Verification**:
    *   Build ISO and check that `NetworkManager.conf` has `managed=true`.
    *   Confirm screen blanking/screensaver does not turn on.
