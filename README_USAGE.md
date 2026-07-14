# Edge B.R.O. — Usage Guide

🇬🇧 [English](README.md) · [Usage Guide](README_USAGE.md) · 🇷🇺 [Русский](README_ru.md) · [Инструкция](README_USAGE_ru.md)

Step-by-step instructions: from a bare server to managing a fleet of edge devices, creating backups, restoring disks, and generating Live-CD kiosks.

---

## 1. Server Setup

Any x86_64 Linux machine with Docker works. An Intel NUC, a mini-PC, or a full server — whatever you have.

1. Install a base OS (Ubuntu 22.04/24.04 or Debian 12).
2. Install Docker:
   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-v2
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER
   ```
   Re-login after adding yourself to the `docker` group.

---

## 2. Deploy the Orchestrator

### 2.1 Clone and configure

```bash
git clone https://github.com/masseselsev/Edge.bro.git /opt/stacks/Edge.bro
cd /opt/stacks/Edge.bro
cp .env.example .env
```

Edit `.env` — every line matters:

```env
# ── Database ──
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<strong_db_password>
POSTGRES_DB=borg_orchestrator
DATABASE_URL=postgresql://postgres:<strong_db_password>@db:5432/borg_orchestrator

# ── Redis ──
REDIS_URL=redis://redis:6379/0

# ── Borg ──
BORG_PASSPHRASE=<strong_borg_passphrase>

# ── Network ──
ORCHESTRATOR_IP=192.168.222.2     # IP of this server, reachable by edge nodes
                                   # Can be changed later in Settings → UI

# ── Superadmin credentials (first launch only) ──
SUPERADMIN_USERNAME=admin          # Login for the web dashboard
ADMIN_PASSWORD=<strong_password>   # Will NOT be overwritten if changed via UI later
JWT_SECRET_KEY=<random_secret>     # Used to sign session tokens

# ── SSH stability (optional) ──
# SSH_KEEPALIVE_INTERVAL=30        # Seconds between keepalive packets
# SSH_KEEPALIVE_COUNT=3            # Missed responses before disconnect

# ── Storage path ──
BORG_HOST_DATA_PATH=borg-data     # Docker volume by default
                                   # Set an absolute path for external storage:
                                   # BORG_HOST_DATA_PATH=/mnt/hdd/borg_data
```

> **About `SUPERADMIN_USERNAME` / `ADMIN_PASSWORD`**: these values seed the first superadmin account on initial startup. Once created, the account lives in the database. Changing `.env` later won't overwrite a password you've already changed via the web UI. To force-reset: clear the `users` table in PostgreSQL and restart.

### 2.2 Configure backup storage

By default, backups go into a Docker named volume (`borg-data`) under `/var/lib/docker/volumes/`. For any serious deployment, point it to a dedicated large drive:

```bash
# Create the directory on your large drive
mkdir -p /mnt/hdd/borg_data
chown -R 1000:1000 /mnt/hdd/borg_data
```

Then set in `.env`:
```env
BORG_HOST_DATA_PATH=/mnt/hdd/borg_data
```

> ⚠️ If the root partition fills up, backup tasks will fail with `Insufficient free space` and other host services may break. Always use a dedicated volume for production.

The configured path is shown in the web UI under **Settings**.

### 2.3 Start everything

```bash
docker compose up -d --build
```

Database migrations run automatically on startup (the backend container waits for PostgreSQL, applies Alembic migrations, then launches FastAPI).

**Done.** Open `http://<YOUR_SERVER_IP>:7777` in a browser. Log in with the credentials from `.env`.

---

## 3. Adding Nodes (Fleet Provisioning)

Go to the **Fleet** tab.

### 3.1 Add nodes

Click **Add Nodes**. Enter IP addresses in any format:
- Single: `192.168.1.10`
- List: `192.168.1.10, 192.168.1.11, 192.168.1.12`
- Range: `192.168.1.50-60`
- CIDR: `10.0.0.0/24`

Fill in SSH credentials (login, password, port). The form pre-fills defaults (`user` / `admin` / port `2222`) — adjust as needed.

### 3.2 What happens during Bootstrap

The orchestrator connects to each node in parallel (up to 24 at a time) and:

1. **Bypasses dead APT proxies** — temporarily renames unreachable proxy configs, restores them after.
2. **Installs packages** — `python3`, `borgbackup`, `parted`, `e2fsprogs`, `dosfstools`.
3. **Injects SSH key** — appends the orchestrator's Ed25519 public key to `/root/.ssh/authorized_keys`. Sets `PermitRootLogin prohibit-password`.
4. **Creates `borg` user** — system user with its own SSH keypair for pushing backup data.
5. **Gathers hardware info** — disk type, EFI UUID, hostname, OS version, partition layout, network interface.

From this point on, all communication is key-based — no passwords stored.

### 3.3 Auto-Prepare (disk labeling)

If a node shows status **NEEDS_FIX**, click **Auto-Prepare**. This:

1. Backs up `/etc/fstab` → `/etc/fstab.bak`.
2. Writes persistent labels to partitions: `edgeroot` (root), `edgeboot` (boot), `edgelog` (logs), `edgestor` (data), `EFI` (boot EFI).
3. Rewrites `/etc/fstab` using `LABEL=` entries instead of `/dev/sdX` paths. This makes the system immune to disk name drift (SATA ↔ NVMe swaps).
4. Runs `update-grub` + `update-initramfs -u` to embed the new config.

If anything fails, the original fstab is auto-restored from backup.

---

## 4. Creating Backups

### 4.1 Manual backup

1. In the **Fleet** tab, click the **Backup** button on any node row.
2. Click **View Logs** to watch real-time terminal output.
3. The orchestrator SSHes into the node, runs `borg create`, and data streams back to the central Borg repository.
4. Track results in the **Archive** tab (sizes, timestamps, deduplication stats).

### 4.2 Scheduled backups (Backup Groups)

Go to the **Schedule** tab → **Create Group**.

A backup group defines:
- **Which nodes** belong to it (drag from the list)
- **Interval** — 10min, 30min, weekly, monthly, quarterly, yearly
- **Execution window** — start time / end time
- **Concurrency limit** — how many nodes back up simultaneously

#### Resource limits (per group)

| Setting | What it does |
|---------|-------------|
| **Upload Rate Limit** (KiB/s) | Caps network throughput per backup stream |
| **CPU Quota** (%) | Limits CPU on the target node (0–400% of one core). Enforced via `systemd-run --scope -p CPUQuota=...` |
| **Compression** | Algorithm selection: `lz4` (fast), `zstd:1`–`zstd:9` (better ratio) |
| **Checkpoint Interval** | Auto-calculated from upload speed by default. Manual override available (seconds). |

#### Smart queue behavior

- **Dynamic concurrency**: if the window is running short, the scheduler automatically increases parallelism to finish on time.
- **Bandwidth capping**: with low upload limits, concurrency is auto-reduced (minimum 2 MiB/s per stream).
- **FIFO queue**: nodes are launched sequentially by stagger offset. When one finishes, the next starts immediately.
- **Running protection**: backups already in progress are allowed to finish past the window close.

#### Retention policies

Each group can inherit the global retention policy or override with its own:
- **Interval** — keep N daily + N weekly + N monthly backups
- **Count** — keep last N backups total
- **Timeframe** — keep everything within the last N days/weeks/months/years

Global defaults (retention, compression, CPU quota) are configured in **Settings**.

Pruning runs automatically at 03:00 daily via Celery Beat, followed by `borg compact`.

### 4.3 How deduplication saves space

All nodes share a single Borg repository. Identical files across different nodes are stored only once.

| Scenario | Space used |
|----------|-----------|
| 1st node (6 GB base OS) | ~2.2–2.7 GB (55–65% compression) |
| Each additional cloned node | +100–200 MB (97% dedup) |
| Nodes with minor differences | +20–30% of unique data |
| Incremental backup runs | ~100–200 MB per run |

### 4.4 Default backup exclusions

Configured in **Settings** → **Global Exclusions**. Each entry has a pattern and a comment. The shipped defaults:

| Pattern | Purpose |
|---------|---------|
| `/dev/*` | System devices |
| `/proc/*` | Virtual process filesystem |
| `/sys/*` | Sysfs system info |
| `/run/*` | Transient runtime files |
| `/mnt/*` | Mounted filesystems |
| `/media/*` | Removable media mounts |
| `/lost+found` | Recovered filesystem fragments |
| `/var/log/edge/*` | Edge app logs |
| `/var/opt/edge/blobstore/*` | Local media file storage |
| `/var/spool/edge/*` | Edge spool directory |
| `/var/log/journal/*` | Systemd journal logs |
| `/var/log/**/*.gz` | Compressed rotated logs |
| `/var/log/**/*.1` | Rotated log backups |
| `/var/hasplm/*` | Sentinel HASP licensing data |
| `/etc/hasplm/*` | Sentinel HASP licensing config |

You can add, remove, or comment any exclusion directly from the UI.

---

## 5. Bare-Metal Restore (Flasher)

Restore a backup directly onto a physical disk connected to the orchestrator via USB.

### 5.1 Connect the disk

Use a **USB-to-SATA** or **USB-to-NVMe** adapter.

| Drive type | Hot-plug rules |
|-----------|----------------|
| **SATA** | USB-SATA adapters generally support hot-plug. Plug the USB into the server while it's running. |
| **NVMe** | Insert the M.2 drive into the adapter **first**, then connect USB to the server. Never remove the M.2 board while the adapter is plugged in. Always safe-eject before unplugging. |

### 5.2 Flash the disk

1. Go to the **Flasher** tab.
2. **Right side** — select the connected USB disk. The system shows model, size, and bus type. The server's own system disk is filtered out and protected.
3. **Left side** — select the source node and the backup snapshot (archive) to restore.
4. Click **Start Flashing**. The log console shows every step:
   - Disk wiped (`wipefs`) and repartitioned as GPT
   - EFI partition formatted as `vfat` with the historically captured UUID
   - System partitions formatted as `ext4` (with `orphan_file` disabled for Debian 10 compat)
   - `borg extract` unpacks the archive onto the mounted disk
   - Chroot: `mount --bind` of `/dev`, `/proc`, `/sys` → GRUB reinstalled → `update-initramfs`
   - Network reset: persistent-net rules wiped, generic DHCP injected for `eth*`/`en*`
   - Fallback EFI loader written to `EFI/BOOT/BOOTX64.EFI`
5. Wait for `Restore completed successfully!` — the disk is safely unmounted.
6. Disconnect the adapter, install the disk into the target node, power on. It boots with all data from the backup timestamp.

> 💡 **NVMe ↔ SATA migration**: because fstab uses `LABEL=edgeroot` (not `/dev/sdX`), you can restore an NVMe backup onto a SATA disk or vice versa. The UI shows a "drive type mismatch" warning — this is by design. Confirm and proceed.

---

## 6. Live-CD Kiosk (Network Restore Without Disk Extraction)

Instead of physically removing the disk, you can boot a target node from a generated Live-CD USB and restore over the network.

### 6.1 Generate the ISO

1. Go to the **Live-CD & Kiosks** tab → **ISO Generator** section.
2. The orchestrator's IP and an auth token are baked into the ISO.
3. Click **Generate Live-USB**. The system downloads a base Debian image, injects your config, and compiles a custom bootable ISO.
4. Download the ISO when ready.

### 6.2 Write the ISO to USB

Use one of these tools (in **DD mode**, not ISO mode):

| Tool | Platform | Notes |
|------|----------|-------|
| [**Rufus**](https://rufus.ie/) | Windows | Select **DD Image** mode when prompted. ISO/GPT mode won't boot correctly. |
| [**balenaEtcher**](https://etcher.balena.io/) | Windows / macOS / Linux | Always writes in DD mode. Just select the ISO and the USB drive. |
| `dd` | Linux | `sudo dd if=technician_client_v1.iso of=/dev/sdX bs=4M status=progress && sync` |

> ⚠️ **Why DD mode?** The ISO contains a hybrid bootable image with custom partitioning. Writing it as a regular ISO (e.g., via Rufus in ISO mode) will break the boot layout. Rufus will ask you — always pick DD.

**USB drive requirements**: 8 GB minimum (16–32 GB recommended), USB 3.0+ for reasonable write speeds.

### 6.3 Single Snapshot Sync

Instead of downloading the entire node's backup history (potentially hundreds of GB), you can sync just one specific snapshot:

1. In the **Live-CD & Kiosks** tab, select the target node and the desired snapshot.
2. Click **Sync Snapshot**.
3. The backend compiles a temporary mini-repository on the fly using `borg export-tar` | `borg import-tar` and streams it to the USB.
4. The UI shows real-time download speed, progress bar, and ETA.

### 6.4 Boot and restore

1. Insert the USB into the broken edge node and boot from it.
2. If the node needs VPN to reach the orchestrator:
   - Open **Network Settings** in the kiosk UI.
   - Scan a WireGuard QR code with the webcam, or paste the config text manually.
   - The VPN profile persists on the USB drive (`/media/usb-data`) and auto-loads on next boot.
3. The kiosk connects to the orchestrator and shows the Flasher interface.
4. Select the node's internal disk and the desired snapshot → start the restore.

### 6.5 Kiosk Management

From the **Live-CD & Kiosks** tab → **Kiosk Control** section, you can:

- **Register** new kiosks with dynamic pairing keys
- **Approve / Block / Re-activate** kiosk access
- **Edit** kiosk metadata (name, contact, comments)
- **Issue personalized ISOs** — click **Issue Live Kiosk**, fill in recipient details, and the system compiles a custom ISO with a unique pairing token
- **Re-create / Download** previously generated ISOs from the history list

---

## 7. Sentinel LDK Licensing (HASP Keys)

For edge nodes running Sentinel HASP software licensing.

### 7.1 Status monitoring

During bootstrap, the orchestrator detects the Sentinel LDK runtime version. If present, a collapsible **Sentinel LDK License Info** card appears in the node details modal:
- **Active** (green) — license valid
- **Expired** (orange) — license expired
- **Clone Detected** (red) — hardware change detected
- **Disabled** (grey) — key or daemon offline

### 7.2 Download C2V fingerprint

1. Open node details in **Fleet** → expand the Sentinel card.
2. Click **Download C2V Fingerprint**.
3. The orchestrator runs `hasp_update lf` + `hasp_update i` on the node via SSH. Falls back to the local ACC API (`127.0.0.1:1947`) if CLI tools are missing.
4. The `.c2v` file downloads to your browser.

### 7.3 Apply V2C license update

1. Open node details → Sentinel card.
2. Drag-and-drop or select the `.V2C` file.
3. The orchestrator uploads it to the node and runs `hasp_update u <file>` via SSH.
4. The license status refreshes automatically.

---

## 8. User Management

Go to **Settings** → **Users**.

| Role | Permissions |
|------|------------|
| **Superadmin** | Full access. Can create/edit/delete other users. |
| **Admin** | Can operate the system (backup, restore, view logs) but cannot manage users. |

The first superadmin account is seeded from `.env` on first launch (see section 2.1).

To reset a lost password: update `ADMIN_PASSWORD` in `.env`, clear the `users` table in PostgreSQL, and restart the backend container.

---

## 9. Database Backup & Recovery

The orchestrator's PostgreSQL database holds all node configs, SSH keys, backup history, groups, and settings. Back it up.

### Automated daily dump (cron)

Create `/opt/backup_db.sh`:
```bash
#!/bin/bash
BACKUP_DIR="/var/backups/edge_bro_db"
mkdir -p "$BACKUP_DIR"
FILENAME="${BACKUP_DIR}/db_backup_$(date +%Y%m%d_%H%M%S).sql.gz"

docker compose -f /opt/stacks/Edge.bro/docker-compose.yml \
  exec -T db pg_dump -U postgres borg_orchestrator | gzip > "$FILENAME"

# Keep 30 days of history
find "$BACKUP_DIR" -type f -name "db_backup_*.sql.gz" -mtime +30 -delete
```

Add to crontab (`crontab -e`):
```cron
15 3 * * * /bin/bash /opt/backup_db.sh > /dev/null 2>&1
```

### Recovery

```bash
gunzip -c db_backup_YYYYMMDD_HHMMSS.sql.gz > recovery.sql

docker compose exec -T db psql -U postgres -d postgres \
  -c "DROP DATABASE borg_orchestrator;"
docker compose exec -T db psql -U postgres -d postgres \
  -c "CREATE DATABASE borg_orchestrator;"
docker compose exec -T db psql -U postgres -d borg_orchestrator < recovery.sql
```

Restart the backend container after recovery to pick up the restored data.
