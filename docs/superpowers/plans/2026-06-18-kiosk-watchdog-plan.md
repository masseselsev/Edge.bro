# Kiosk Watchdog & Client Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate hardware watchdog control in the kiosk, disable screen blanking, pin the footer to the bottom, and resolve static IP configuration reset bugs.

**Architecture:** A lightweight FastAPI router handles serial Modbus RTU requests to `/dev/ttyUSB0`–`/dev/ttyUSB2` at 19200 baud. The React frontend polls this status, shows a detection modal, and provides a toggle in the pinned footer.

**Tech Stack:** Python (FastAPI, pyserial), React (TypeScript, Tailwind CSS, Lucide icons).

---

### Task 1: Package Dependencies & ISO Configs

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/iso_tasks.py`

- [ ] **Step 1: Add pyserial to orchestrator requirements**
  Add `pyserial>=3.5` to `/home/masse/projects/Backup-edge-Restore/backend/requirements.txt`.
  ```diff
  +pyserial>=3.5
  ```

- [ ] **Step 2: Copy serial package in ISO tasks**
  In `/home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py`, append `"serial"` to `packages_to_copy`:
  ```python
          packages_to_copy = [
              "fastapi", "pydantic", "pydantic_core", "uvicorn", "starlette",
              "anyio", "h11", "click", "annotated_types", "idna",
              "annotated_doc", "typing_inspection", "watchfiles", "python_multipart", "multipart",
              "serial"
          ]
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add backend/requirements.txt backend/iso_tasks.py
  git commit -m "build: add pyserial dependency and package it in ISO build"
  ```

---

### Task 2: Live-CD NetworkManager & Screensaver Disabling

**Files:**
- Modify: `payload_client/init-bottom-copy-payload.sh`
- Modify: `payload_client/kiosk-launcher.sh`

- [ ] **Step 1: Set managed=true in NetworkManager.conf**
  In `/home/masse/projects/Backup-edge-Restore/payload_client/init-bottom-copy-payload.sh`, set `managed=true` on the target overlay filesystem before switch_root:
  ```bash
  # Copy systemd units
  ...
  # Set NetworkManager to manage all interfaces
  if [ -f /root/etc/NetworkManager/NetworkManager.conf ]; then
      sed -i 's/managed=false/managed=true/g' /root/etc/NetworkManager/NetworkManager.conf
  fi
  ```

- [ ] **Step 2: Add xfconf-query commands to kiosk-launcher**
  In `/home/masse/projects/Backup-edge-Restore/payload_client/kiosk-launcher.sh`, add calls to disable screensaver/locking/blanking:
  ```bash
  # Disable screensaver and DPMS (screen turning off / going to sleep)
  if command -v xset &>/dev/null; then
    xset s off      # disable screensaver
    xset s noblank  # don't blank the video device
    xset -dpms      # disable DPMS (Energy Star) features
  fi

  if command -v xfconf-query &>/dev/null; then
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac -s 0 --create -t int || true
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-sleep -s 0 --create -t int || true
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-off -s 0 --create -t int || true
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s false --create -t bool || true
    xfconf-query -c xfce4-screensaver -p /saver/enabled -s false --create -t bool || true
    xfconf-query -c xfce4-screensaver -p /lock/enabled -s false --create -t bool || true
  fi
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add payload_client/init-bottom-copy-payload.sh payload_client/kiosk-launcher.sh
  git commit -m "kiosk: disable screen blanking and configure NetworkManager system-wide"
  ```

---

### Task 3: Watchdog Backend Module

**Files:**
- Create: `payload_client/backend/routers/watchdog.py`
- Modify: `payload_client/backend/main.py`

- [ ] **Step 1: Create watchdog.py router**
  Write `/home/masse/projects/Backup-edge-Restore/payload_client/backend/routers/watchdog.py`:
  ```python
  import serial
  from fastapi import APIRouter, HTTPException
  from pydantic import BaseModel
  from typing import Dict, Any

  router = APIRouter(prefix="/kiosk/watchdog", tags=["Watchdog"])

  class WatchdogStatus(BaseModel):
      detected: bool
      port: str | None = None
      seconds_left: int | None = None
      frozen: bool = False

  def calculate_crc(data: bytes) -> bytes:
      crc = 0xFFFF
      for byte in data:
          crc ^= byte
          for _ in range(8):
              if crc & 1:
                  crc = (crc >> 1) ^ 0xA001
              else:
                  crc >>= 1
      return bytes([crc & 0xFF, (crc >> 8) & 0xFF])

  def scan_watchdog() -> Dict[str, Any]:
      ports_to_scan = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
      # pc_wdt read command: 30 03 00 00 00 01 + CRC (80 2B)
      read_cmd = bytes.fromhex("300300000001802B")
      
      # read coils starting at 0x0000 to get REG_VSM_FROZEN_REQ state
      read_coils_cmd = bytes.fromhex("3001000000083E0C")

      for port in ports_to_scan:
          try:
              ser = serial.Serial(port, 19200, timeout=0.15)
              ser.write(read_cmd)
              res = ser.read(7)
              if len(res) == 7 and res[0] == 0x30 and res[1] == 0x03 and res[2] == 0x02:
                  # Validate CRC
                  expected_crc = calculate_crc(res[:-2])
                  if res[-2:] == expected_crc:
                      seconds = (res[3] << 8) | res[4]
                      
                      # Now check if frozen
                      frozen = False
                      ser.write(read_coils_cmd)
                      c_res = ser.read(6)
                      if len(c_res) == 6 and c_res[0] == 0x30 and c_res[1] == 0x01:
                          expected_c_crc = calculate_crc(c_res[:-2])
                          if c_res[-2:] == expected_c_crc:
                              # Bit 7 is Frozen Request
                              frozen = bool((c_res[3] >> 7) & 1)

                      ser.close()
                      return {
                          "detected": True,
                          "port": port,
                          "seconds_left": seconds,
                          "frozen": frozen
                      }
              ser.close()
          except Exception:
              pass
      return {"detected": False, "port": None, "seconds_left": None, "frozen": False}

  @router.get("/status", response_model=WatchdogStatus)
  def get_watchdog_status():
      try:
          status = scan_watchdog()
          return WatchdogStatus(**status)
      except Exception as e:
          raise HTTPException(status_code=500, detail=str(e))

  @router.post("/freeze")
  def freeze_watchdog():
      status = scan_watchdog()
      if not status["detected"]:
          raise HTTPException(status_code=404, detail="Watchdog controller not found")
      
      port = status["port"]
      # Commands
      freeze_cmd = bytes.fromhex("30050007FF0039DA")
      reset_cmd = bytes.fromhex("3006000000008DEB")
      
      try:
          ser = serial.Serial(port, 19200, timeout=0.5)
          
          # Send freeze command
          ser.write(freeze_cmd)
          f_res = ser.read(8)
          if f_res != freeze_cmd:
              ser.close()
              raise Exception("Watchdog failed to confirm freeze request")
              
          # Send reset command
          ser.write(reset_cmd)
          r_res = ser.read(8)
          ser.close()
          return {"status": "SUCCESS", "message": "Watchdog frozen successfully"}
      except Exception as e:
          raise HTTPException(status_code=500, detail=str(e))

  @router.post("/unfreeze")
  def unfreeze_watchdog():
      status = scan_watchdog()
      if not status["detected"]:
          raise HTTPException(status_code=404, detail="Watchdog controller not found")
      
      port = status["port"]
      unfreeze_cmd = bytes.fromhex("300500070000782A")
      
      try:
          ser = serial.Serial(port, 19200, timeout=0.5)
          ser.write(unfreeze_cmd)
          res = ser.read(8)
          ser.close()
          if res != unfreeze_cmd:
              raise Exception("Watchdog failed to confirm unfreeze request")
          return {"status": "SUCCESS", "message": "Watchdog unfrozen successfully"}
      except Exception as e:
          raise HTTPException(status_code=500, detail=str(e))
  ```

- [ ] **Step 2: Inject router into backend main.py**
  In `/home/masse/projects/Backup-edge-Restore/payload_client/backend/main.py`, dynamically load the watchdog router to ensure complete isolation:
  ```python
  # Try to register the shared network configurations router if available
  try:
      from routers.network import router as network_router
      app.include_router(network_router, prefix="/api")
  except ImportError:
      pass

  # Register Kiosk Watchdog router
  try:
      from routers.watchdog import router as watchdog_router
      app.include_router(watchdog_router, prefix="/api")
  except ImportError:
      pass
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add payload_client/backend/routers/watchdog.py payload_client/backend/main.py
  git commit -m "kiosk: add watchdog backend API endpoints for status, freeze, and unfreeze"
  ```

---

### Task 4: Fix Network Settings Modal

**Files:**
- Modify: `frontend/src/components/NetworkSettingsModal.tsx`

- [ ] **Step 1: Add hasInitialized to separate form load from updates**
  In `/home/masse/projects/Backup-edge-Restore/frontend/src/components/NetworkSettingsModal.tsx`, prevent background status polls from resetting the input field states:
  ```typescript
    const [status, setStatus] = useState<NetworkStatus | null>(null);
    const [hasInitialized, setHasInitialized] = useState(false);
    ...
    const fetchStatus = async () => {
      try {
        const res = await fetch('/api/network/status');
        if (!res.ok) return;
        const data: NetworkStatus = await res.json();
        setStatus(data);
        if (data.wired && !hasInitialized) {
          setWiredMode(data.wired.mode || 'auto');
          setIpAddress(data.wired.ip || '');
          setNetmask(data.wired.netmask || '255.255.255.0');
          setGateway(data.wired.gateway || '');
          setDnsMode(data.wired.dns_mode || 'auto');
          if (data.wired.dns_servers) {
            setDns1(data.wired.dns_servers[0] || '');
            setDns2(data.wired.dns_servers[1] || '');
          }
          setHasInitialized(true);
        }
      } catch (err) {
        console.error('Failed to fetch network status:', err);
      }
    };
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add frontend/src/components/NetworkSettingsModal.tsx
  git commit -m "fix(kiosk): fix network configurations form resetting during background polls"
  ```

---

### Task 5: Frontend Footer & Watchdog UI Controls

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/i18n/translations.ts`

- [ ] **Step 1: Update translation dictionaries**
  In `/home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts`, add the required keys in `en`, `ru`, and `uk`:
  - **English** (under `en` dictionary):
    ```typescript
        watchdogTitle: 'Watchdog Controller Detected',
        watchdogAlertText: 'A hardware watchdog timer has been detected. We recommend freezing the watchdog to prevent unexpected system reboots during backups or restores.',
        watchdogFreezeButton: 'Freeze Watchdog',
        watchdogUnfreezeButton: 'Unfreeze Watchdog',
        watchdogActiveBadge: 'Watchdog Active',
        watchdogFrozenBadge: 'Watchdog Frozen',
    ```
  - **Russian** (under `ru` dictionary):
    ```typescript
        watchdogTitle: 'Обнаружен контроллер Watchdog',
        watchdogAlertText: 'Обнаружен аппаратный сторожевой таймер. Рекомендуется заморозить вочдог, чтобы предотвратить случайную перезагрузку материнской платы во время резервного копирования или восстановления.',
        watchdogFreezeButton: 'Заморозить вочдог',
        watchdogUnfreezeButton: 'Разморозить вочдог',
        watchdogActiveBadge: 'Вочдог активен',
        watchdogFrozenBadge: 'Вочдог заморожен',
    ```
  - **Ukrainian** (under `uk` dictionary):
    ```typescript
        watchdogTitle: 'Виявлено контролер Watchdog',
        watchdogAlertText: 'Виявлено апаратний сторожовий таймер. Рекомендується заморозити вочдог, щоб запобігти раптовому перезавантаженню плати під час резервного копіювання або відновлення.',
        watchdogFreezeButton: 'Заморозити вочдог',
        watchdogUnfreezeButton: 'Розморозити вочдог',
        watchdogActiveBadge: 'Вочдог активний',
        watchdogFrozenBadge: 'Вочдог заморожений',
    ```

- [ ] **Step 2: Add watchdog polling state, modals, and footer control in App.tsx**
  In `/home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx`:
  - Position the footer fixed: `<footer className="fixed bottom-0 left-0 right-0 z-40 ...">`
  - In `<main>`, change container to add padding bottom: `<main className="flex-1 max-w-7xl w-full mx-auto px-6 py-8 pb-20">`
  - Fetch watchdog status on interval when `isKiosk` is true.
  - Render the watchdog alert modal and the button in the footer.
  - Complete code changes details:
    ```typescript
      const [watchdogStatus, setWatchdogStatus] = useState<{
        detected: boolean;
        port: string | null;
        seconds_left: number | null;
        frozen: boolean;
      } | null>(null);
      const [showWatchdogModal, setShowWatchdogModal] = useState(false);
      const [hasShownWatchdogModal, setHasShownWatchdogModal] = useState(false);
      const [watchdogActionLoading, setWatchdogActionLoading] = useState(false);

      const fetchWatchdogStatus = async () => {
        try {
          const res = await fetch('/api/kiosk/watchdog/status');
          if (res.ok) {
            const data = await res.json();
            setWatchdogStatus(data);
            if (data.detected && !data.frozen && !hasShownWatchdogModal) {
              setShowWatchdogModal(true);
              setHasShownWatchdogModal(true);
            }
          }
        } catch (e) {
          console.error(e);
        }
      };

      useEffect(() => {
        if (!isKiosk) return;
        fetchWatchdogStatus();
        const interval = setInterval(fetchWatchdogStatus, 4000);
        return () => clearInterval(interval);
      }, [isKiosk, hasShownWatchdogModal]);

      const handleFreezeWatchdog = async () => {
        setWatchdogActionLoading(true);
        try {
          const res = await fetch('/api/kiosk/watchdog/freeze', { method: 'POST' });
          if (!res.ok) throw new Error("Failed to freeze watchdog");
          await fetchWatchdogStatus();
          setShowWatchdogModal(false);
        } catch (err: any) {
          alert(err.message);
        } finally {
          setWatchdogActionLoading(false);
        }
      };

      const handleUnfreezeWatchdog = async () => {
        setWatchdogActionLoading(true);
        try {
          const res = await fetch('/api/kiosk/watchdog/unfreeze', { method: 'POST' });
          if (!res.ok) throw new Error("Failed to unfreeze watchdog");
          await fetchWatchdogStatus();
        } catch (err: any) {
          alert(err.message);
        } finally {
          setWatchdogActionLoading(false);
        }
      };
    ```
  - Modal markup (to render before `NetworkSettingsModal`):
    ```tsx
          {/* Watchdog Alert Modal */}
          {showWatchdogModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
              <div className="w-full max-w-md p-6 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl space-y-4 animate-modal-in">
                <div className="flex items-start gap-3 border-b border-zinc-800 pb-3">
                  <div className="p-2 bg-rose-500/10 text-rose-400 border border-rose-500/20 rounded-lg shrink-0">
                    <ShieldAlert size={20} className="animate-pulse" />
                  </div>
                  <div>
                    <h3 className="text-base font-bold text-zinc-50 leading-tight">{t('watchdogTitle')}</h3>
                    <p className="text-[10px] text-zinc-500 font-semibold uppercase tracking-wider mt-0.5">{watchdogStatus?.port}</p>
                  </div>
                </div>
                <p className="text-xs text-zinc-300 leading-relaxed">
                  {t('watchdogAlertText')}
                </p>
                <div className="flex justify-end gap-2 pt-2 border-t border-zinc-800">
                  <button
                    onClick={() => setShowWatchdogModal(false)}
                    className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors"
                  >
                    {t('closeButton') || 'Close'}
                  </button>
                  <button
                    onClick={handleFreezeWatchdog}
                    disabled={watchdogActionLoading}
                    className="px-4 py-2 text-xs font-bold text-white bg-rose-600 hover:bg-rose-500 rounded-lg disabled:opacity-50 transition-colors flex items-center gap-1.5"
                  >
                    {watchdogActionLoading ? <RefreshCw size={12} className="animate-spin" /> : null}
                    {t('watchdogFreezeButton')}
                  </button>
                </div>
              </div>
            </div>
          )}
    ```
  - Footer UI controls addition:
    ```tsx
              {watchdogStatus?.detected && (
                <>
                  <span className="h-4 w-px bg-zinc-800" />
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                      watchdogStatus.frozen 
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/20 animate-pulse'
                    }`}>
                      {watchdogStatus.frozen ? t('watchdogFrozenBadge') : t('watchdogActiveBadge')}
                    </span>
                    <button
                      disabled={watchdogActionLoading}
                      onClick={watchdogStatus.frozen ? handleUnfreezeWatchdog : handleFreezeWatchdog}
                      className="px-2.5 py-1 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 text-zinc-200 hover:text-white rounded text-[10px] font-bold transition-all flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                    >
                      {watchdogActionLoading && <RefreshCw size={9} className="animate-spin" />}
                      {watchdogStatus.frozen ? t('watchdogUnfreezeButton') : t('watchdogFreezeButton')}
                    </button>
                  </div>
                </>
              )}
    ```

- [ ] **Step 3: Commit**
  ```bash
  git add frontend/src/App.tsx frontend/src/i18n/translations.ts
  git commit -m "kiosk: add UI watchdog indicators, modal confirmation, and persistent footer toggle button"
  ```
