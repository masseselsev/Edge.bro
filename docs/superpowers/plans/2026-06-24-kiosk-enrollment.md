# Kiosk Enrollment and Persistent USB Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a generic Live-USB kiosk system that formats all unused space on first boot to create a persistent storage partition, dynamically registers itself with the server via an enrollment request (Name, Phone, Comment), uses a simplified 6-character case-insensitive pairing key, and lets administrators specify multiple server IPs for the kiosk connection.

**Architecture:** 
1. **Partitioning**: A pre-backend systemd service (`kiosk-storage-setup`) finds the boot USB device, creates an `ext4` partition (`kiosk-data`) in the remaining space, mounts it at `/media/usb-data`, and symlinks the kiosk `config.json` and SSH keys to preserve identities across boots.
2. **Simplified Pairing**: Update the backend to generate `\d{4}[A-Z]{2}` keys and compare them case-insensitively during handshake.
3. **Enrollment request**: Add database columns and APIs (`POST /api/kiosks/enroll`) for pending connection requests. Show a global header notification banner and modal on the server dashboard containing the pairing key.
4. **Multi-IP settings**: Add a JSON column `server_ips` to the global Settings, compile these into the client ISO config, and render a dropdown selector on the kiosk enrollment page.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, React, TypeScript, Tailwind CSS, systemd, parted, ext4.

## Global Constraints

- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic
- Database: PostgreSQL (with Alembic migrations)
- Frontend: React, TypeScript, Tailwind CSS, Lucide Icons
- UI Styling: Dropdown lists and modal windows must use CSS transition animations (`animate-modal-in`, `animate-fade-in`, etc.).
- Multi-Language Support (i18n): All new UI elements must support English, Russian, and Ukrainian translations in `frontend/src/i18n/translations.ts`.
- File Size Limit: Split components/routers if they exceed 500 lines.

---

### Task 1: Database Migration & Schema Changes

**Files:**
- Modify: [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py)
- Modify: [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Create: `backend/alembic/versions/<revision>_add_kiosk_enrollment_fields_and_settings_ips.py`
- Modify: [test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py)

**Interfaces:**
- Consumes: Existing SQLAlchemy database session.
- Produces: Updated `Settings` and `Kiosk` models in DB with `server_ips`, `phone`, and `comment` fields.

- [ ] **Step 1: Update Settings and Kiosk models**
  Modify [models.py](file:///home/masse/projects/Backup-edge-Restore/backend/models.py) to add:
  - `server_ips` to `Settings` model.
  - `phone` and `comment` to `Kiosk` model.

  ```python
  # In backend/models.py - class Settings:
  server_ips = Column(JSON, nullable=True, default=[])

  # In backend/models.py - class Kiosk:
  phone = Column(String, nullable=True)
  comment = Column(Text, nullable=True)
  ```

- [ ] **Step 2: Update Pydantic schemas**
  Modify [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py) to update schemas to include new fields:

  ```python
  # In backend/schemas.py - class SettingsBase:
  server_ips: Optional[List[str]] = Field(default=[])

  # In backend/schemas.py - class KioskBase:
  name: Optional[str] = None
  uuid: str
  phone: Optional[str] = None
  comment: Optional[str] = None

  # In backend/schemas.py - class KioskResponse:
  # Ensure it inherits KioskBase and has the new fields
  ```

- [ ] **Step 3: Generate Alembic migration file**
  Create the migration revision:
  Run: `docker compose exec backend alembic revision -m "add_kiosk_enrollment_fields_and_settings_ips"`
  Update the generated migration file's `upgrade` and `downgrade` methods:

  ```python
  def upgrade() -> None:
      op.add_column('settings', sa.Column('server_ips', sa.JSON(), nullable=True, server_default='[]'))
      op.add_column('kiosks', sa.Column('phone', sa.String(), nullable=True))
      op.add_column('kiosks', sa.Column('comment', sa.Text(), nullable=True))

  def downgrade() -> None:
      op.drop_column('kiosks', 'comment')
      op.drop_column('kiosks', 'phone')
      op.drop_column('settings', 'server_ips')
      pass
  ```

- [ ] **Step 4: Run the migration**
  Run: `docker compose exec backend alembic upgrade head`
  Expected: Successful exit and tables updated in PostgreSQL.

- [ ] **Step 5: Write unit tests**
  Add unit tests in [test_db.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_db.py) to assert new columns exist and can hold lists of IPs, phone numbers, and comments.
  Run tests: `docker compose exec backend pytest backend/tests/test_db.py -v`
  Expected: PASS.

- [ ] **Step 6: Commit**
  ```bash
  git add backend/models.py backend/schemas.py backend/alembic/versions/
  git commit -m "feat: add schema fields for kiosk enrollment and settings server ips"
  ```

---

### Task 2: Multi-IP Server Settings (Orchestrator backend & ISO generation)

**Files:**
- Modify: [settings.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/settings.py)
- Modify: [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)

**Interfaces:**
- Consumes: `Settings` database columns.
- Produces: REST API for settings updates returning `server_ips`, and client ISO configuration with `available_server_ips`.

- [ ] **Step 1: Update settings router to process `server_ips`**
  Modify [settings.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/settings.py):
  Ensure `update_settings` copies `payload.server_ips` to `settings.server_ips`.
  ```python
  # In backend/routers/settings.py - update_settings method:
  settings.server_ips = payload.server_ips
  ```

- [ ] **Step 2: Update ISO generation task to bake multi-IPs**
  Modify [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py):
  Update `generate_client_iso_task` to query setting `server_ips` and save it to kiosk client's `config.json` as `available_server_ips`.
  ```python
  # In backend/iso_tasks.py - generate_client_iso_task:
  settings = db.query(models.Settings).first()
  server_ips = settings.server_ips if (settings and settings.server_ips) else []
  # In config_data dict:
  config_data = {
      "orchestrator_ip": target_ip,
      "available_server_ips": server_ips,
      "auth_token": auth_token,
      "language": lang,
      "kiosk_uuid": kiosk_uuid
  }
  ```

- [ ] **Step 3: Test settings API**
  Use `pytest` or curl to verify setting changes are persisted.
  Run: `docker compose exec backend pytest backend/tests/test_kiosks.py -v` (and settings tests)
  Expected: PASS.

- [ ] **Step 4: Commit**
  ```bash
  git add backend/routers/settings.py backend/iso_tasks.py
  git commit -m "feat: handle multiple server IPs in orchestrator setting APIs and ISO builder"
  ```

---

### Task 3: Simplified Key Generation & Case-Insensitive Handshake

**Files:**
- Modify: [kiosks.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py)
- Modify: [test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py)

**Interfaces:**
- Consumes: User-provided key string during handshake.
- Produces: Case-insensitive match on database security key.

- [ ] **Step 1: Update generate_kiosk_key**
  Modify [kiosks.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py):
  Rewrite key generator to produce `1234AB` format (4 digits, 2 letters).
  ```python
  def generate_kiosk_key() -> str:
      import random
      # Exclude confusing digits (0, 1, 2) and letters (O, I, L, Z)
      digits = "".join(random.choice("3456789") for _ in range(4))
      letters = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXY") for _ in range(2))
      return f"{digits}{letters}"
  ```

- [ ] **Step 2: Make handshake compare keys case-insensitively**
  Modify the `handshake` route:
  Normalize both `req.key` and search in database. Since keys are saved in uppercase, convert `req.key` to uppercase.
  ```python
  # In backend/routers/kiosks.py - handshake:
  normalized_key = req.key.strip().upper()
  kiosk = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid, models.Kiosk.key == normalized_key).first()
  ```

- [ ] **Step 3: Update unit tests in test_kiosks.py**
  Modify [test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py):
  - Change the assertion `len(key) == 9` to `len(key) == 6`.
  - Assert that `-` is not present in the key.
  - Assert that lowercase keys are successfully matched.
  Run: `docker compose exec backend pytest backend/tests/test_kiosks.py -v`
  Expected: PASS.

- [ ] **Step 4: Commit**
  ```bash
  git add backend/routers/kiosks.py backend/tests/test_kiosks.py
  git commit -m "feat: simplify pairing key format to 6 chars and verify case-insensitively"
  ```

---

### Task 4: Dynamic Enrollment Request Endpoint

**Files:**
- Modify: [kiosks.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py)
- Modify: [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py)
- Modify: [test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py)

**Interfaces:**
- Consumes: Dynamic kiosk enrollment payload.
- Produces: Pending Kiosk entry in DB and generates key.

- [ ] **Step 1: Define Enrollment Schema**
  Modify [schemas.py](file:///home/masse/projects/Backup-edge-Restore/backend/schemas.py) to define `KioskEnrollRequest`:
  ```python
  class KioskEnrollRequest(BaseModel):
      uuid: str
      name: str
      phone: str
      comment: str
      ssh_pub_key: str
  ```

- [ ] **Step 2: Add `/api/kiosks/enroll` Endpoint**
  Modify [kiosks.py (router)](file:///home/masse/projects/Backup-edge-Restore/backend/routers/kiosks.py) to implement endpoint:
  ```python
  @router.post("/enroll")
  def enroll_kiosk(req: schemas.KioskEnrollRequest, db: Session = Depends(get_db)):
      # Check if already exists
      existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid).first()
      if existing:
          if existing.status == "APPROVED":
              raise HTTPException(status_code=400, detail="Kiosk is already approved")
          
          # Update request metadata
          existing.name = req.name
          existing.phone = req.phone
          existing.comment = req.comment
          existing.ssh_pub_key = req.ssh_pub_key
          existing.status = "PENDING"
          # Regenerate key
          existing.key = generate_kiosk_key()
          db.commit()
          db.refresh(existing)
          return {"status": "PENDING", "key": existing.key}

      key = generate_kiosk_key()
      while db.query(models.Kiosk).filter(models.Kiosk.key == key).first():
          key = generate_kiosk_key()

      kiosk = models.Kiosk(
          uuid=req.uuid,
          name=req.name,
          phone=req.phone,
          comment=req.comment,
          ssh_pub_key=req.ssh_pub_key,
          status="PENDING",
          key=key
      )
      db.add(kiosk)
      db.commit()
      return {"status": "PENDING", "key": key}
  ```

- [ ] **Step 3: Write tests for enrollment endpoint**
  Add unit tests in [test_kiosks.py](file:///home/masse/projects/Backup-edge-Restore/backend/tests/test_kiosks.py) for the `/enroll` endpoint.
  Run: `docker compose exec backend pytest backend/tests/test_kiosks.py -v`
  Expected: PASS.

- [ ] **Step 4: Commit**
  ```bash
  git add backend/routers/kiosks.py backend/schemas.py backend/tests/test_kiosks.py
  git commit -m "feat: implement dynamic kiosk enrollment API and tests"
  ```

---

### Task 5: Client Persistent storage partition setup & mounting service

**Files:**
- Create: `payload_client/kiosk-storage-setup.sh`
- Create: `payload_client/systemd/kiosk-storage-setup.service`
- Modify: [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py)
- Modify: [init-bottom-copy-payload.sh](file:///home/masse/projects/Backup-edge-Restore/payload_client/init-bottom-copy-payload.sh)

**Interfaces:**
- Consumes: Unallocated disk space on the USB boot media.
- Produces: Mounted persistent storage `/media/usb-data` housing configurations.

- [ ] **Step 1: Write kiosk-storage-setup.sh**
  Create `payload_client/kiosk-storage-setup.sh`:
  - Detect `/dev/disk/by-label/kiosk-data`.
  - Partition using `parted` if not found.
  - Format as `ext4` with label `kiosk-data`.
  - Mount, symlink `/opt/offline-client/backend/config.json` and SSH keys.

  ```bash
  #!/bin/bash
  set -e
  PERSISTENT_DEV="/dev/disk/by-label/kiosk-data"
  MOUNT_POINT="/media/usb-data"

  if [ -b "$PERSISTENT_DEV" ]; then
      echo "Persistent partition found. Mounting..."
      mkdir -p "$MOUNT_POINT"
      mount "$PERSISTENT_DEV" "$MOUNT_POINT"
  else
      echo "Persistent partition not found. Attempting partitioning..."
      # Find boot partition
      BOOT_PART=$(findmnt -n -o SOURCE /run/live/medium || true)
      if [ -z "$BOOT_PART" ] || [[ "$BOOT_PART" == *"/dev/sr"* ]]; then
          echo "WARNING: Booting from CD-ROM or VM. Falling back to non-persistent mode."
          exit 0
      fi
      
      # Extract parent disk
      PARENT_DISK=$(echo "$BOOT_PART" | sed -r 's/p?[0-9]+$//')
      if [ -z "$PARENT_DISK" ] || [ ! -b "$PARENT_DISK" ]; then
          echo "ERROR: Could not resolve parent disk for $BOOT_PART"
          exit 0
      fi
      
      echo "Parent USB disk is $PARENT_DISK. Creating partition..."
      # Fix/relocate GPT backup headers
      parted -s "$PARENT_DISK" print
      
      # Find end of last partition
      LAST_END=$(parted -s "$PARENT_DISK" print | awk '/^[0-9]/ {end=$3} END {print end}')
      if [ -z "$LAST_END" ]; then
          LAST_END="4000MB"
      fi

      parted -s "$PARENT_DISK" mkpart primary ext4 "$LAST_END" 100%
      partprobe "$PARENT_DISK" || true
      udevadm settle || true
      
      # Find new partition
      NEW_PART=$(lsblk -o NAME,TYPE -n -l "$PARENT_DISK" | grep part | tail -n 1 | awk '{print "/dev/"$1}')
      if [ -z "$NEW_PART" ] || [ ! -b "$NEW_PART" ]; then
          echo "ERROR: New partition not found"
          exit 0
      fi
      
      echo "Formatting $NEW_PART as ext4 with label kiosk-data..."
      mkfs.ext4 -F -L kiosk-data "$NEW_PART"
      mkdir -p "$MOUNT_POINT"
      mount "$NEW_PART" "$MOUNT_POINT"
  fi

  # Setup storage layout & symlinks
  mkdir -p "$MOUNT_POINT"/.ssh
  mkdir -p "$MOUNT_POINT"/borg/fleet

  CONFIG_FILE="/opt/offline-client/backend/config.json"
  PERSISTENT_CONFIG="$MOUNT_POINT/config.json"
  if [ ! -f "$PERSISTENT_CONFIG" ] && [ -f "$CONFIG_FILE" ]; then
      cp "$CONFIG_FILE" "$PERSISTENT_CONFIG"
  fi
  ln -sf "$PERSISTENT_CONFIG" "$CONFIG_FILE"

  # Link SSH key
  SSH_KEY="/opt/offline-client/backend/id_ed25519"
  PERSISTENT_SSH="$MOUNT_POINT/.ssh/id_ed25519"
  if [ -f "$SSH_KEY" ] && [ ! -f "$PERSISTENT_SSH" ]; then
      cp "$SSH_KEY" "$PERSISTENT_SSH"
      chmod 600 "$PERSISTENT_SSH"
  fi
  if [ -f "$SSH_KEY.pub" ] && [ ! -f "$PERSISTENT_SSH.pub" ]; then
      cp "$SSH_KEY.pub" "$PERSISTENT_SSH.pub"
  fi
  if [ -f "$PERSISTENT_SSH" ]; then
      ln -sf "$PERSISTENT_SSH" "$SSH_KEY"
      ln -sf "$PERSISTENT_SSH.pub" "$SSH_KEY.pub"
  fi
  ```

- [ ] **Step 2: Create Systemd service**
  Create `payload_client/systemd/kiosk-storage-setup.service`:
  ```ini
  [Unit]
  Description=Kiosk Persistent USB Storage Setup
  DefaultDependencies=no
  After=local-fs.target
  Before=offline-backend.service

  [Service]
  Type=oneshot
  ExecStart=/opt/offline-client/kiosk-storage-setup.sh
  RemainAfterExit=yes

  [Install]
  WantedBy=multi-user.target
  ```

- [ ] **Step 3: Modify init-bottom script**
  Modify [init-bottom-copy-payload.sh](file:///home/masse/projects/Backup-edge-Restore/payload_client/init-bottom-copy-payload.sh):
  Ensure the new setup service and script are copied.
  ```bash
  # In init-bottom-copy-payload.sh:
  if [ -f /etc/systemd/system/kiosk-storage-setup.service ]; then
      cp /etc/systemd/system/kiosk-storage-setup.service /root/etc/systemd/system/
      ln -sf /etc/systemd/system/kiosk-storage-setup.service /root/etc/systemd/system/multi-user.target.wants/kiosk-storage-setup.service
  fi
  ```

- [ ] **Step 4: Modify ISO repack tasks**
  Modify [iso_tasks.py](file:///home/masse/projects/Backup-edge-Restore/backend/iso_tasks.py):
  Ensure `kiosk-storage-setup.sh` and `kiosk-storage-setup.service` are packaged into the ISO payload.
  ```python
  # In iso_tasks.py - generate_client_iso_task:
  # Copy Script
  shutil.copy2("/payload_client/kiosk-storage-setup.sh", os.path.join(opt_offline, "kiosk-storage-setup.sh"))
  os.chmod(os.path.join(opt_offline, "kiosk-storage-setup.sh"), 0o755)

  # Copy Service
  shutil.copy2("/payload_client/systemd/kiosk-storage-setup.service", os.path.join(payload_dir, "etc", "systemd", "system", "kiosk-storage-setup.service"))
  os.symlink("/etc/systemd/system/kiosk-storage-setup.service", os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants", "kiosk-storage-setup.service"))
  ```

- [ ] **Step 5: Commit**
  ```bash
  git add payload_client/kiosk-storage-setup.sh payload_client/systemd/kiosk-storage-setup.service
  git add backend/iso_tasks.py payload_client/init-bottom-copy-payload.sh
  git commit -m "feat: add kiosk storage setup partition service script and systemd config"
  ```

---

### Task 6: Kiosk Client Frontend & Backend Pairing Updates

**Files:**
- Modify: [main.py (client)](file:///home/masse/projects/Backup-edge-Restore/payload_client/backend/main.py)
- Modify: `payload_client/backend/frontend_build/` (This contains static React build. During development, client uses React components to trigger connection. We will write the pairing layout updates in the React frontend, build it, and update the static build).

**Interfaces:**
- Consumes: Server enrollment endpoint.
- Produces: API for the client frontend to register settings and connection state.

- [ ] **Step 1: Expose `available_server_ips` in Client Backend**
  Modify [main.py (client)](file:///home/masse/projects/Backup-edge-Restore/payload_client/backend/main.py):
  Update `/api/version` endpoint to return the parsed `available_server_ips` from `config.json`.
  ```python
  # In payload_client/backend/main.py - get_version():
  available_ips = []
  if os.path.exists(CONFIG_PATH):
      try:
          with open(CONFIG_PATH, "r") as f:
              cfg = json.load(f)
              available_ips = cfg.get("available_server_ips", [])
      except:
          pass
  return {
      "version": VERSION,
      "is_kiosk": True,
      "orchestrator_ip": orchestrator_ip,
      "available_server_ips": available_ips,
      "auth_token": auth_token,
      "language": language,
      "kiosk_uuid": kiosk_uuid
  }
  ```

- [ ] **Step 2: Add Dynamic Enrollment route on client backend**
  Modify [main.py (client)](file:///home/masse/projects/Backup-edge-Restore/payload_client/backend/main.py):
  Add a client API endpoint `POST /api/kiosk/enroll` that forwards the request to the target orchestrator IP:
  ```python
  class ClientEnrollRequest(BaseModel):
      orchestrator_ip: str
      name: str
      phone: str
      comment: str

  @app.post("/api/kiosk/enroll")
  def enroll_client_kiosk(req: ClientEnrollRequest):
      ensure_ssh_keypair()
      pub_key_path = SSH_KEY_PATH + ".pub"
      with open(pub_key_path, "r") as f:
          pub_key_data = f.read().strip()
      
      url = f"http://{req.orchestrator_ip}:8000/api/kiosks/enroll"
      payload = {
          "uuid": kiosk_uuid,
          "name": req.name,
          "phone": req.phone,
          "comment": req.comment,
          "ssh_pub_key": pub_key_data
      }
      try:
          post_data = json.dumps(payload).encode("utf-8")
          req_obj = urllib.request.Request(
              url, 
              data=post_data,
              headers={"Content-Type": "application/json"}
          )
          with urllib.request.urlopen(req_obj, timeout=10) as response:
              res = json.loads(response.read().decode())
          return res
      except Exception as e:
          raise HTTPException(status_code=400, detail=str(e))
  ```

- [ ] **Step 3: Update Kiosk Pairing UI**
  In the React App (shared dashboard/kiosk pages), modify the connection modal/pairing view.
  If the kiosk is not approved (`auth_token` is empty):
  - Step A: Display enrollment form fields (dropdown/input for Server IP, Name input, Phone input, Comment input).
  - Step B: On submit, hit `/api/kiosk/enroll`.
  - Step C: Once received, show pairing key prompt: "Please enter the pairing key shown on the server dashboard".
  - Step D: On submit, hit `/api/kiosk/connect` with the target IP and the entered key.

- [ ] **Step 4: Commit**
  ```bash
  git add payload_client/backend/main.py
  git commit -m "feat: implement client backend enrollment routes and update static resources"
  ```

---

### Task 7: Orchestrator Settings and Kiosk Dashboard UI

**Files:**
- Modify: [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx)
- Modify: [translations.ts](file:///home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts)
- Modify: [KioskManagementSection.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/components/KioskManagementSection.tsx)

**Interfaces:**
- Consumes: `/api/kiosks` list endpoint.
- Produces: Dynamic notification header banner and modal window in React dashboard.

- [ ] **Step 1: Add i18n translation keys**
  Modify [translations.ts](file:///home/masse/projects/Backup-edge-Restore/frontend/src/i18n/translations.ts):
  Add keys for multi-IP config, notifications, Name/Phone/Comment validation alerts in EN, RU, and UA.
  ```typescript
  // Example keys to append in translations.ts:
  pendingConnectionBanner: {
      en: "⚠️ Pending Kiosk Connection Request from {name} ({phone})",
      ru: "⚠️ Ожидается запрос подключения киоска от {name} ({phone})",
      uk: "⚠️ Очікується запит підключення кіоску від {name} ({phone})"
  }
  ```

- [ ] **Step 2: Add global header notification banner**
  Modify [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx):
  Implement state to hold pending kiosks and poll `/api/kiosks` in a `useEffect` loop.
  Render the banner if a request is `PENDING` and has metadata.
  Clicking the button opens the connection review modal.

- [ ] **Step 3: Build approval modal**
  Inside [App.tsx](file:///home/masse/projects/Backup-edge-Restore/frontend/src/App.tsx), implement the modal. Render the pairing key prominently (font size `text-2xl`, monospaced) with a Copy button.
  Ensure animations: `animate-modal-in` and `animate-fade-in`.

- [ ] **Step 4: Commit and rebuild**
  Run: `npm run build` inside `frontend/` to generate the production assets.
  Copy built assets to `/payload_client/backend/frontend_build/` to bake it into the next client image.
  ```bash
  git add frontend/src/App.tsx frontend/src/i18n/translations.ts
  git commit -m "feat: add global connection request notifications and review modals on server dashboard"
  ```
