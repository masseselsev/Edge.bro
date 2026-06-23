# Edge B.R.O. — Edge Backup & Restore Orchestrator

🇬🇧 **[English README](README.md)** | 🇬🇧 **[English Usage Guide](README_USAGE.md)** | 🇷🇺 **[Русский README](README_ru.md)** | 🇷🇺 **[Русская инструкция (Usage Guide)](README_USAGE_ru.md)**

A production-grade, centralized, dockerized orchestration panel designed for managing, backup scheduling, and bare-metal flashing restoration of multiple Debian-based edge nodes.

---

## 🏗️ Architecture Overview

The system is fully containerized and uses a decoupled architecture to manage concurrency, state synchronization, and privileged hardware execution:

```
                  ┌──────────────────────────────────────────────┐
                  │              React SPA Frontend              │
                  │             (Port 7777 - Nginx)              │
                  └──────────────────────┬───────────────────────┘
                                         │ REST API
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │               FastAPI Backend                │
                  │             (Port 8000 - Uvicorn)            │
                  └──────────────┬───────────────┬───────────────┘
                                 │               │
                     Writes Logs │               │ Dispatches Tasks
                     & Metadata  │               │
                                 ▼               ▼
   ┌──────────────┐       ┌──────────┐      ┌──────────┐       ┌─────────────┐
   │  PostgreSQL  │ ◄──── │ Database │      │  Redis   │ ◄───► │   Celery    │
   │ (Port 5432)  │       │  Session │      │  Broker  │       │ Task Worker │
   └──────────────┘       └──────────┘      └──────────┘       └──────┬──────┘
                                                                      │ Runs
                                                                      │ Privileged Actions
                                                                      ▼
                                                               ┌─────────────┐
                                                               │  Edge Fleet │
                                                               │   Targets   │
                                                               └─────────────┘
```

### Components:
1. **React SPA Frontend (Port 7777)**: Responsive, dark-themed dashboard mapped to tabs (Fleet, Flasher, History, Live-USB Client, Settings). Supports multi-language translation (English, Russian, Ukrainian) with a premium animated language selector dropdown. Displays stats (de-duplication ratios, total space) and features a terminal console overlay to stream execution logs in real-time.
2. **FastAPI Backend (Port 8000)**: Serves RESTful APIs, implements the IP parser (supporting CIDR, lists, and ranges), validates drive type configurations, and tracks active jobs.
3. **Celery Worker (Privileged Host-Device Mode)**: Subscribed to task queues to execute playbooks and perform flashing partition commands (requires access to `/dev` of the local orchestrator node during flashing).
4. **Borg SSH Server (Port 12345)**: Isolated central repository environment where edge node public keys are automatically appended to `/home/borg/.ssh/authorized_keys` under forced command restrictions (`command="borg serve --restrict-to-path ..."`).
5. **Redis**: In-memory task queue broker and result backend.
6. **PostgreSQL**: Stores Orchestrator states, global settings, node inventory metadata, backup histories, and task execution logs.

---

## 🛠️ Key Orchestration Modules

### 1. Fleet Provisioning & Bulk IP Parsing
- Supports registering hosts via **lists** (comma-separated), **ranges** (e.g. `192.168.1.50-60` or `10.0.0.1-10.0.0.3`), and **CIDR blocks** (e.g. `192.168.1.0/30`).
- Spawns parallel, concurrent Celery tasks utilizing a pre-configured Celery concurrency of 24 to bootstrap multiple edge nodes simultaneously.
- Form inputs pre-populate with default credentials (`user`, `admin`, `SSH port 2222`) to speed up administrative workflows.

### 2. Auto-Prepare Playbook (Label & EFI Extraction)
- Runs an idempotent Ansible playbook to verify node readiness.
- Sets persistent filesystem labels (`edgeroot` on the root partition and `edgeboot` on the ESP boot partition).
- Captures and saves the unique EFI FAT32 filesystem UUID.
- Rewrites the target's `/etc/fstab` to reference partition labels, shielding the operating system against hardware device drift (e.g., SATA `/dev/sda` transitioning to NVMe `/dev/nvme0n1` on new hardware).

### 3. Backup Scheduling, Global Deduplication & Resource Limits
- Backups are initiated remotely via `ssh` command and stream data to the central Borg SSH Server.
- **SSH Connection Keepalive**: Outgoing backup and initialization commands are configured with client-side SSH keepalives (`ServerAliveInterval` and `ServerAliveCountMax` values configured via environment variables) to maintain tunnel stability over unreliable networks and prevent worker tasks from hanging indefinitely.
- **Granular Resource limits**: To safeguard host performance and network bandwidth on edge sites, backups can be configured with:
  - **Upload Rate Limit**: Limits maximum bandwidth throughput (in KiB/s) per backup group.
  - **CPU Quota**: Restricts backup process CPU usage (0-400% of a single core) globally or per backup group, enforced on client nodes using `systemd-run --scope -p CPUQuota=...`.
  - **Custom Compression**: Enables selecting specific compression algorithm/level (e.g., `lz4`, `zstd:3` as default, `zstd:5`, etc.) globally or overridden per backup group.
  - **Optimized Checkpoint Intervals**: Borg's checkpoint interval is dynamically auto-calculated from the upload speed limit to maximize recovery points on slow connections (e.g. checkpointing every ~50 MB at <= 500 KiB/s, ~200 MB at <= 5000 KiB/s, or defaulting to 1800s), with manual group overrides available.
- **Cross-Device Deduplication & Compression**: Because all edge nodes back up into a single, centralized Borg repository (`/data/borg/fleet`), Borg's chunk-level deduplication and compression span across all devices globally. Identical files, OS binaries, and application assets present on multiple Debian nodes are only stored **once** on the server.
  - *Example Savings (Assuming a 6 GB base OS footprint per node)*:
    - **1st Node (Standalone compression & deduplication)**: saves **55% - 65%** of its size right away due to Borg's built-in compression (e.g. `lz4`), reducing a 6 GB system to ~2.2 - 2.7 GB on disk.
    - **Each additional similar node (Cross-node deduplication)**: saves up to **97%** for identical/cloned nodes (adding only **~100 - 200 MB** for the same device under a different name) and saves about **20% - 30%** of space for nodes with minor system configuration and package differences.
    - **Incremental backups**: of running systems tend towards only **~100 - 200 MB** of unique incremental data per backup run (storing only unique logs, cache differences, and database states).
    - This yields a massive overall storage footprint reduction for fleets running similar base images, with incremental runs remaining extremely lightweight.
- **Configurable Global Exclusions**: In the **Orchestrator Settings** tab in the web UI, you can configure a comma-separated list of directories to exclude from backups (e.g. temporary/virtual mounts or heavy log/data folders).
  - **Default Exclusions**: `/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*`
- To prevent database lock-ups on the shared Borg repositories, pruning is decoupled from individual backups. A global Celery Beat schedule triggers a local repository `borg prune` daily at 3:00 AM using the global retention policy (configurable in Settings, or overridden per backup group). The system supports interval-based (daily/weekly/monthly), count-based (keep last N), or timeframe-based (keep within past days/weeks/months/years) retention.

### 4. Bare-Metal Flashing Restore
- **Device Protection Safeguard**: Scans target block devices on the orchestrator host while shielding the host's own root system drive against accidental overwrite.
- **Drive Type Mismatch Warning & Cross-Drive Migration**: Because the system strictly uses filesystem labels (`LABEL=edgeroot`) in `/etc/fstab` instead of hardcoded `/dev/sdX` or UUID paths, **you can seamlessly migrate a backup taken from an NVMe drive onto a SATA drive** (or vice versa). The web UI will show a by-design warning when it detects this hardware change to ensure you are aware, but allows you to override and proceed with the cross-drive restoration.
- **EFI UUID Preservation**: Partitions the target device as GPT, formats the ESP boot partition and explicitly overrides its UUID using the historical captured value (`mkfs.vfat -i <EFI_UUID_HEX>`).
- **PCIe Network Drift Mitigation**: Wipes old persistent network device bindings and injects generic wildcard interface configurations (`eth*` and `en*`) to guarantee network reachability upon post-flashing boots.
- **Chroot Bootloader Config**: Mounts the system, binds virtualization paths (`/dev`, `/proc`, `/sys`), reinstalls GRUB on the target device, updates initramfs, and writes a fallback EFI loader path (`EFI/BOOT/BOOTX64.EFI`).
- **Auditing**: Performs a post-restore verification audit confirming label configurations inside `/etc/fstab` before safely unmounting.

### 5. Live-USB Offline Client Generation
- Compiles a bootable Debian Live environment on the fly.
- Embeds the orchestrator's IP address and authentication tokens directly into the generated ISO.
- When booted on an edge node, launches a secure kiosk UI that connects to the central orchestrator, enabling offline network restoration directly to the node's internal disk without requiring hardware extraction.

---

## 🚀 Installation & Usage

For full deployment instructions, including server preparation, environment configuration, database migrations, and a comprehensive usage guide, please refer to the:
- 🇬🇧 **[English Installation & Usage Guide](README_USAGE.md)**
- 🇷🇺 **[Русская инструкция по установке и использованию](README_USAGE_ru.md)**

---

## 📂 Repository Layout

```
.
├── backend
│   ├── alembic/                # DB Migrations
│   ├── playbooks/              # Ansible bootstrap/prepare playbooks
│   ├── ansible_utils.py        # Python subprocess wrapper for Ansible
│   ├── main.py                 # FastAPI endpoints
│   ├── models.py               # SQLAlchemy models
│   ├── schemas.py              # Pydantic validation schemas
│   ├── tasks.py                # Celery tasks (backup, bootstrap, prune)
│   ├── restore_logic.py        # Bare-metal flashing restore routine
│   └── tests/                  # Pytest unit tests
├── docker
│   ├── backend/                # FastAPI & Worker Dockerfile
│   ├── borg/                   # SSH Borg Server Dockerfile
│   └── frontend/               # React & Nginx Dockerfile
├── frontend
│   ├── src/
│   │   ├── components/         # Fleet, Flasher, History UI tabs
│   │   ├── App.tsx             # Navigation controller
│   │   └── index.css           # Tailwind configuration styles
│   ├── tailwind.config.js
│   └── nginx.conf              # Production asset routing server configuration
└── docker-compose.yml          # Container stack orchestration definition
```

---

## 🔌 Hardware & Host Storage Stability

When performing bare-metal restore operations on target USB flash drives or external drives through USB-to-SATA/NVMe bridges (e.g., JMicron controllers like `152d:0581`), the default Linux **UAS (USB Attached SCSI)** driver might crash or reset under heavy concurrent queue write loads (like `borg extract`). This causes the disk to hang and locks the flashing process in an uninterruptible `D` (I/O wait) state.

To guarantee host platform stability during bulk bare-metal flashing:

1. **Configure USB Storage Quirks** on the host machine to bypass the buggy `uas` driver and force the ultra-stable legacy `usb-storage` path:
   ```bash
   echo -e "options usb-storage quirks=152d:0581:u\noptions uas quirks=152d:0581:u" | sudo tee /etc/modprobe.d/usb-quirks.conf
   ```
   *(Replace `152d:0581` with the respective Vendor:Product ID of your USB-to-SATA bridge found in `lsusb` if different).*

2. **Re-plug the USB Drive** to apply the rule, or reset the USB bus programmatically using the host's `usbreset` utility:
   ```bash
   sudo usbreset 152d:0581
   ```

---

## 🔍 Changes Made on Target Edge Nodes

This section provides a **complete, exhaustive list** of every modification the orchestrator performs on managed target edge nodes. No changes beyond those listed here are made. All actions are executed via SSH from the orchestrator; no persistent agents or daemons are installed on target nodes.

### Phase 1: Bootstrap (Initial Provisioning)

> Executed by: `playbooks/bootstrap.yml` via Ansible over SSH.
> Triggered by: Adding a new node through the web UI (Fleet → Add Nodes).
> Authentication: Password-based SSH (one-time, with the user-supplied credentials).

#### 1.1. OS Compatibility Check *(read-only)*

| Action | Details |
|--------|---------|
| File read | `/etc/os-release` or `/etc/debian_version` |
| Effect | **None** — read-only validation. Rejects OS versions below Debian 10 or Ubuntu 18. |

#### 1.2. Package Installation

| Action | Details |
|--------|---------|
| APT update | `apt-get update` is executed to refresh the package index |
| Packages installed | `python3`, `python3-pip`, `borgbackup`, `parted`, `udev`, `dosfstools`, `e2fsprogs`, `util-linux` |
| Proxy handling | If an unreachable APT proxy is detected, its config files under `/etc/apt/apt.conf` and `/etc/apt/apt.conf.d/` are **temporarily** renamed to `*.disabled`, and **restored** at the end of the playbook |

#### 1.3. System User Creation

| Action | Details |
|--------|---------|
| User created | `borg` — a system user with `/bin/bash` shell and a home directory at `/home/borg` |
| SSH keypair generated | Ed25519 keypair at `/home/borg/.ssh/id_ed25519` and `/home/borg/.ssh/id_ed25519.pub` |
| Purpose | The `borg` user's private key is used by the node to authenticate outgoing backup connections to the orchestrator's Borg SSH server |

#### 1.4. SSH Configuration

| File Modified | Change |
|---------------|--------|
| `/root/.ssh/authorized_keys` | The orchestrator's public SSH key is appended (created if absent, mode `0600`) |
| `/root/.ssh/` directory | Created if absent, mode `0700` |
| `/etc/ssh/sshd_config` | Line `PermitRootLogin` is set to `prohibit-password` (allows key-only root login) |
| SSH service | Restarted **only if** `sshd_config` was actually changed |

#### 1.5. System Information Gathering *(read-only)*

The following information is **read** from the node and stored in the orchestrator's database. No files are modified during this step:

| Data collected | Source |
|----------------|--------|
| Disk type (SATA/NVMe) | `/sys/block/*/queue/rotational`, `lsblk` |
| EFI partition UUID | `blkid -s UUID` on the EFI partition |
| Active network interface | `ip route get 8.8.8.8` |
| Hostname | `hostname` command |
| OS version | `/etc/os-release` or `/etc/debian_version` |
| Partition layout (JSON) | `lsblk -J -b -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT,LABEL,UUID,PARTUUID` |
| Filesystem labels | `e2label` on root, boot, log, and storage partitions |
| fstab label compliance | Content check of `/etc/fstab` for `LABEL=edge*` entries |

---

### Phase 2: Auto-Prepare (Disk Label Standardization)

> Executed by: `playbooks/prepare.yml` via Ansible over SSH.
> Triggered by: Clicking "Auto-Prepare" on a node with status `NEEDS_FIX`.
> Authentication: Key-based SSH (using the orchestrator's key authorized during bootstrap).
> Rollback: If any step fails, the original `/etc/fstab` is automatically restored from backup.

#### 2.1. Fstab Backup

| Action | Details |
|--------|---------|
| File created | `/etc/fstab.bak` — a copy of the current `/etc/fstab` (used for automatic rollback on failure) |

#### 2.2. Filesystem Label Assignment

The following partition labels are **written directly** to the on-disk filesystem metadata using `e2label` / `fatlabel`:

| Partition Mount | Label Set | Command |
|-----------------|-----------|---------|
| `/` (root) | `edgeroot` | `e2label <device> edgeroot` |
| `/boot` | `edgeboot` | `e2label <device> edgeboot` |
| `/var/log/edge` | `edgelog` | `e2label <device> edgelog` |
| `/var/opt/edge` | `edgestor` | `e2label <device> edgestor` |
| `/boot/efi` | `EFI` | `fatlabel <device> EFI` (strips any incorrect label) |

#### 2.3. Fstab Rewrite

| File Modified | Change |
|---------------|--------|
| `/etc/fstab` | **Completely replaced** with a 5-line standardized template using `LABEL=` entries instead of `/dev/` paths or UUIDs. The EFI partition uses `UUID=<captured_uuid>` |

New `/etc/fstab` content:
```
# Standardized fstab via Borg Orchestrator Auto-Prepare
LABEL=edgeroot   /               ext4    defaults,noatime                  0       1
UUID=<EFI_UUID>  /boot/efi       vfat    umask=0077,defaults,noatime       0       1
LABEL=edgeboot   /boot           ext2    defaults,noatime                  0       2
LABEL=edgelog    /var/log/edge   ext4    defaults,noatime                  0       2
LABEL=edgestor   /var/opt/edge   ext4    defaults,noatime                  0       2
```

#### 2.4. Bootloader & Initramfs Update

| Action | Command | Purpose |
|--------|---------|---------|
| Reload systemd | `systemctl daemon-reload` | Picks up the new fstab |
| Verify mounts | `mount -a` | Validates all entries in the new fstab resolve correctly |
| Update GRUB | `update-grub` | Regenerates GRUB config to reflect new root-by-label |
| Update initramfs | `update-initramfs -u` | Embeds updated fstab references into the boot initrd |

#### 2.5. Additional System Info Gathered *(read-only)*

Same as Phase 1 (section 1.5), plus:

| Data collected | Source |
|----------------|--------|
| CPU model | `lscpu` or `/proc/cpuinfo` |
| Total RAM | `free -h` |
| Edge software version | `/etc/motd` (parsed from EDGE banner) |

---

### Phase 3: Backup Execution

> Executed by: `backup_tasks.py` — remote SSH command to the node.
> Triggered by: Manual backup button in the web UI, or by the automatic scheduler.
> Authentication: Key-based SSH from orchestrator root (`/root/.ssh/id_ed25519`) to node root.
> Direction: The orchestrator **SSHes into the node** and runs `borg create` on the node. Borg then pushes data **from the node to the orchestrator** over a reverse SSH tunnel using the node's `borg` user key.

#### 3.1. Borg Repository Initialization *(on orchestrator, not on node)*

| Action | Details |
|--------|---------|
| Command | `borg init --encryption=repokey ssh://borg@<orchestrator_ip>:12345/data/borg/fleet` |
| Runs on | **The target node** (via SSH from orchestrator), but the repository is created on the **orchestrator** filesystem |
| Idempotent | Returns code 2 if already initialized — safely ignored |
| Effect on node | **None** — only the outgoing SSH connection is made |

#### 3.2. Borg Create *(runs on the target node)*

The orchestrator SSHes to the node as `root` and executes:

```bash
borg create --json --stats \
  --compression <algorithm> \
  --checkpoint-interval <seconds> \
  --remote-ratelimit <kib/s> \
  ssh://borg@<orchestrator_ip>:12345/data/borg/fleet::<archive_name> \
  / <exclusions>
```

| Aspect | Details |
|--------|---------|
| Process on node | A `borg create` process runs temporarily, reading the filesystem and streaming data to the orchestrator |
| Optional CPU limiting | If a CPU quota is configured, the command is wrapped in `systemd-run --scope -p CPUQuota=<N>% -p IOSchedulingClass=idle` |
| Files modified on node | **None** — `borg create` is a read-only operation on the source filesystem |
| Files created on node | **None** — no lock files, cache, or state is written on the node itself |
| Network | Outgoing SSH connection from node to orchestrator on port 12345, using `/home/borg/.ssh/id_ed25519` |
| Default exclusions | `/dev/*`, `/proc/*`, `/sys/*`, `/run/*`, `/mnt/*`, `/media/*`, `/lost+found`, `/var/log/edge/*`, `/var/opt/edge/*` |

#### 3.3. Summary of Node Footprint During Backup

| Resource | Impact |
|----------|--------|
| Disk writes | **Zero** — backup is a read-only scan |
| CPU | Controlled by optional `CPUQuota` (via `systemd-run --scope`) |
| Network | Controlled by `--remote-ratelimit` (KiB/s) |
| I/O priority | Optional `IOSchedulingClass=idle` when CPU quota is active |
| Temporary processes | One `borg create` process + one `ssh` tunnel — both terminate when backup completes |

---

### Complete File Change Summary

The table below lists **every file and system object** modified on the target node across all phases:

| File / Object | Phase | Action | Reversible? |
|----------------|-------|--------|-------------|
| APT package index | Bootstrap | `apt-get update` | Auto-refreshed |
| `python3`, `python3-pip` | Bootstrap | Installed via APT | `apt-get remove` |
| `borgbackup` | Bootstrap | Installed via APT | `apt-get remove` |
| `parted`, `udev`, `dosfstools`, `e2fsprogs`, `util-linux` | Bootstrap | Installed via APT | `apt-get remove` |
| `/home/borg/` (user + home) | Bootstrap | Created system user `borg` | `userdel -r borg` |
| `/home/borg/.ssh/id_ed25519{,.pub}` | Bootstrap | Ed25519 keypair generated | Delete files |
| `/root/.ssh/authorized_keys` | Bootstrap | Orchestrator public key appended | Remove the line |
| `/etc/ssh/sshd_config` | Bootstrap | `PermitRootLogin prohibit-password` | Edit line back |
| SSH service | Bootstrap | Restarted (if config changed) | N/A |
| `/etc/apt/apt.conf{,.d/*}` | Bootstrap | Temporarily renamed `*.disabled` → restored | Auto-restored |
| Partition labels (on-disk metadata) | Prepare | `edgeroot`, `edgeboot`, `edgelog`, `edgestor`, `EFI` | `e2label <dev> ""` |
| `/etc/fstab` | Prepare | Replaced with label-based template | Restore `/etc/fstab.bak` |
| `/etc/fstab.bak` | Prepare | Created (backup copy) | Delete file |
| GRUB config | Prepare | Regenerated via `update-grub` | `update-grub` |
| Initramfs | Prepare | Rebuilt via `update-initramfs -u` | `update-initramfs -u` |
| systemd state | Prepare | `daemon-reload` | N/A |
| *Backup execution* | Backup | **No files modified on node** | N/A |

---

## 🔐 Security & User Authentication

To protect the Orchestrator dashboard and API, Edge B.R.O. implements a role-based access control (RBAC) system:

1. **Dashboard Authentication**:
   - Access to the administrative dashboard requires logging in with credentials.
   - User sessions are managed using secure, HTTP-only `admin_session` JWT cookies, protecting users from Cross-Site Scripting (XSS) attacks.

2. **User Roles**:
   - **Superadmin**: The master administrative account. Can manage other administrator accounts under the **Settings → Administrators** sub-tab (create, edit details, set/reset passwords, delete, and add comments).
   - **Administrator**: Standard admin account. Can view nodes, tasks, settings, triggers backups/restores, but cannot manage other users.

3. **Technician Kiosks**:
   - Paired technician kiosks booted from the Live-USB bypass the username/password login by sending pre-paired tokens in the `Authorization: Bearer <auth_token>` header.
   - For offline restoration ISOs where network setup is untethered, query parameters (`?token=<auth_token>`) are supported to ensure authorization persists.

4. **Default Seeding & Recovery**:
   - On the initial startup, the Orchestrator seeds a default Superadmin account using values from the environment variables (`.env`):
     - Username: `SUPERADMIN_USERNAME` (defaults to `admin` if not set)
     - Password: `ADMIN_PASSWORD` (defaults to `admin_pass` if not set)
   - These credentials are created in the database only once. Subsequent changes in the `.env` file will not overwrite changes made via the Web UI.
   - If you need to reset the master password, change the values in `.env` and restart the container, or clear the `users` table to trigger a re-seed.

