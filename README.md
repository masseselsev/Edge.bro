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
1. **React SPA Frontend (Port 7777)**: Responsive, dark-themed dashboard mapped to tabs (Fleet, Flasher, History, Live-USB Client, Settings). Displays stats (de-duplication ratios, total space) and features a terminal console overlay to stream execution logs in real-time.
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

### 3. Backup Scheduling & Global Deduplication
- Backups are initiated remotely via `ssh` command and stream data to the central Borg SSH Server.
- **Cross-Device Deduplication & Compression**: Because all edge nodes back up into a single, centralized Borg repository (`/data/borg/fleet`), Borg's chunk-level deduplication and compression span across all devices globally. Identical files, OS binaries, and application assets present on multiple Debian nodes are only stored **once** on the server.
  - *Example Savings (Assuming a 6 GB base OS footprint per node)*:
    - **1st Node (Standalone compression & deduplication)**: saves **55% - 65%** of its size right away due to Borg's built-in compression (e.g. `lz4`), reducing a 6 GB system to ~2.2 - 2.7 GB on disk.
    - **Each additional similar node (Cross-node deduplication)**: saves up to **97%** for identical/cloned nodes (adding only **~100 - 200 MB** for the same device under a different name) and saves about **20% - 30%** of space for nodes with minor system configuration and package differences.
    - **Incremental backups**: of running systems tend towards only **~100 - 200 MB** of unique incremental data per backup run (storing only unique logs, cache differences, and database states).
    - This yields a massive overall storage footprint reduction for fleets running similar base images, with incremental runs remaining extremely lightweight.
- **Configurable Global Exclusions**: In the **Orchestrator Settings** tab in the web UI, you can configure a comma-separated list of directories to exclude from backups (e.g. temporary/virtual mounts or heavy log/data folders).
  - **Default Exclusions**: `/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*`
- To prevent database lock-ups on the shared Borg repositories, pruning is decoupled from individual backups. A global Celery Beat schedule triggers a local repository `borg prune` daily at 3:00 AM using the global prune rules (daily, weekly, monthly limits).

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
