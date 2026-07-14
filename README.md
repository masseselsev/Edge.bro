# Edge B.R.O. — Backup & Restore Orchestrator

🇬🇧 [English](README.md) · [Usage Guide](README_USAGE.md) · 🇷🇺 [Русский](README_ru.md) · [Инструкция](README_USAGE_ru.md)

Centralized backup management and bare-metal restore system for fleets of Debian-based edge devices. Fully containerized. Ships as a single `docker compose up`.

---

## Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │              React SPA (Nginx :7777)            │
                    │   Fleet · Flasher · Archive · Schedule · Logs   │
                    └────────────────────────┬────────────────────────┘
                                             │ REST / JSON
                                             ▼
                    ┌─────────────────────────────────────────────────┐
                    │             FastAPI Backend (:8000)             │
                    │   IP Parser · Job Tracker · Host IP Discovery   │
                    └────────┬──────────────────────────┬─────────────┘
                             │                          │
                  Reads/Writes DB                Dispatches tasks
                             │                          │
                             ▼                          ▼
              ┌──────────────────┐          ┌───────────────────────┐
              │ PostgreSQL :5432 │          │     Redis :6379       │
              │ Inventory, logs, │          │  Celery broker +      │
              │ settings, users  │          │  result backend       │
              └──────────────────┘          └───────┬───────────────┘
                                                    │
                                      ┌─────────────┼──────────────┐
                                      ▼             ▼              ▼
                               ┌───────────┐ ┌───────────┐ ┌────────────┐
                               │  Worker   │ │   Beat    │ │ Borg SSH   │
                               │ (Celery)  │ │ (Celery)  │ │ Server     │
                               │ Ansible,  │ │ Scheduled │ │ :12345     │
                               │ restore,  │ │ prune,    │ │ Encrypted  │
                               │ backup    │ │ retention │ │ repo store │
                               └─────┬─────┘ └───────────┘ └────────────┘
                                     │
                          SSH + Ansible Playbooks
                                     │
                                     ▼
                            ┌──────────────────┐
                            │   Edge Nodes     │
                            │   (Fleet)        │
                            └──────────────────┘
```

Seven containers in `docker-compose.yml`:

| Service | Role |
|---------|------|
| **frontend** | React SPA behind Nginx. Dashboard with customizable dark/light theme supporting Fleet, Flasher, Archive, Schedule, Logs, and Settings tabs. Multi-language (EN/RU/UK). Real-time terminal console overlay. |
| **backend** | FastAPI on Uvicorn. REST API, IP parser (CIDR / ranges / lists), job tracking. Resolves host physical and VPN IPs by reading `/host/proc/1/net/` (skips docker bridges). |
| **worker** | Celery worker in privileged host-device mode. Runs Ansible playbooks and disk partitioning commands. Needs `/dev` access for bare-metal flashing. |
| **beat** | Celery Beat scheduler. Fires daily `borg prune` at 03:00, enforces retention policies. |
| **borg-server** | Isolated SSH server on port 12345. Central Borg repository. Node keys land in `authorized_keys` under `borg serve --restrict-to-path` forced commands. |
| **db** | PostgreSQL 15. Stores inventory, backup history, groups, settings, user accounts. |
| **redis** | Redis 7. Task broker + result backend for Celery. |

---

## Core Capabilities

### Fleet Provisioning
- Register nodes by IP lists, ranges (`192.168.1.50-60`), or CIDR blocks (`10.0.0.0/24`).
- Parallel Celery bootstrap — up to 24 concurrent node setups. Multiple credentials can be managed and pre-saved via Settings (identified by comment or username/password pair).
- Installs packages, injects SSH keys, gathers detailed hardware/software info (disk type, EFI UUID, hostname, OS version, partition layout, network interfaces, RAM size, CPU model, Edge version, Sentinel LDK version) — all via Ansible.

### Disk Preparation (Auto-Prepare)
- Assigns persistent filesystem labels: `edgeroot`, `edgeboot`, `edgelog`, `edgestor`, `EFI`.
- Captures unique EFI FAT32 UUID for later restore.
- Rewrites `/etc/fstab` to use `LABEL=` mounts — immune to `/dev/sda` ↔ `/dev/nvme0n1` drift.

### Backup Scheduling & Deduplication
- Push-model: orchestrator SSHes into the node, runs `borg create`, data streams back to the central repo.
- **SSH keepalive** tuning via `.env` (`SSH_KEEPALIVE_INTERVAL`, `SSH_KEEPALIVE_COUNT`).
- **Resource limits** per backup group:
  - Upload rate cap (KiB/s)
  - CPU quota (0–400% per core, via `systemd-run --scope`)
  - Compression algorithm (`lz4`, `zstd:1`–`zstd:9`)
  - Checkpoint interval — auto-calculated from upload speed, or manual override
- **Cross-device deduplication**: all nodes share one Borg repo. Identical OS files stored once.
  - 1st node: 55–65% compression savings (~6 GB → ~2.5 GB)
  - Each cloned node adds only ~100–200 MB
  - Incremental runs: ~100–200 MB of unique data
- **Smart queue scheduler**:
  - Dynamic concurrency scaling when window time runs short
  - Bandwidth-aware concurrency caps (can scale below 2 MiB/s per stream if needed)
  - FIFO queue with stagger offsets; slots release instantly on completion
  - Running backups protected past window close
- **Retention**: interval-based, count-based, or timeframe-based. Global or per-group override. Pruning runs daily at 03:00 via Celery Beat, followed by automatic `borg compact`.
- **Exclusions**: configurable in Settings UI. Defaults:

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

### Bare-Metal Restore (Flasher)
- Connect target drive via USB-SATA/NVMe adapter → select node + snapshot → flash.
- **Local Flashing Warning**: Since the orchestrator supports flashing drives directly from the server, all drives other than the server's own system (OS) partition will be visible in the Flasher dropdown. Operators must choose the target disk **EXTREMELY CAREFULLY**. Drives connected via USB will have a special badge/label in the UI.
- **Sentinel LDK (HASP) Reinstallation**: By design, Sentinel licensing does not survive raw cloning of machine hardware/fingerprints. Therefore, the Sentinel HASP runtime is completely reinstalled/reactivated during the restore process.
- GPT partitioning, EFI UUID preservation, `borg extract`, chroot GRUB reinstall, initramfs rebuild.
- Network reset: wipes persistent-net rules, injects generic DHCP for `eth*`/`en*`.
- Post-restore check-in service pings orchestrator and updates status to `RESTORED`.

### Live-CD Kiosk Client
- **Automated Compilation**: Seamless pipeline for base cached Debian template preparation and client ISO compilation/generation — no manual ISO packing commands required. Issue customized client ISOs directly from the dashboard.
- **Main Operating Pattern**: Booted on a technician's PC/laptop (e.g., in the office). The client can flash target drives over the network (pulling backup snapshots from the cloud/server), or run in fully offline mode by pre-synchronizing backup snapshots directly to the free space on the bootable USB flash drive itself beforehand.
- **Secondary Pattern**: Booting the Live-USB directly on the target edge node itself is also supported as an alternative.
- **Single snapshot sync**: generates a temporary mini-repo via `borg export-tar` | `borg import-tar` pipeline — no need to download full history.
- Real-time download speed, progress bar, and ETA display.
- **Kiosk management**: register, approve, block, re-pair kiosks from the dashboard. Dynamic pairing keys. Configurable cache limit (`max_kiosk_isos`) for the maximum number of custom kiosks to keep in memory (oldest ISOs are automatically pruned from disk, but their records remain in the database and can be re-created with one click).

### WireGuard VPN Integration
- Browser webcam QR scanner (`jsQR`) for WireGuard configs, with manual paste fallback.
- VPN profiles persist to `/media/usb-data` with `0600 root:root` permissions.
- Backend endpoints: tunnel stats (`wg show`), NM reload, up/down toggle.

### Sentinel LDK Licensing
- Auto-detects `hasp_runtime_version` during bootstrap.
- Real-time license status badges: Active / Expired / Clone Detected / Disabled.
- C2V fingerprint download via SSH (`hasp_update lf` + `hasp_update i`), with ACC API fallback.
- V2C license upload: drag-and-drop in the dashboard, applied remotely via SSH.

---

## System Requirements

### Orchestrator Server
| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores (x86_64) | 4 cores |
| RAM | 4 GB | 8 GB |
| Network | 100 Mbps | 1 Gbps |
| System disk | 20 GB free | — |
| Backup volume | Sized per fleet — see below | Dedicated drive |

**Backup volume sizing** (quarterly backups, keep last 5 = ~1.25 years). For fleets with highly uniform/identical hardware, Borg's cross-device deduplication is significantly more efficient:

| Fleet size | Estimate (Mixed/Custom Fleet) | Estimate (Highly Uniform Fleet) |
|------------|-------------------------------|---------------------------------|
| 50 devices | ~60 GB | ~40 GB |
| 300 devices | ~300 GB | ~150–200 GB |
| 1000 devices | ~1 TB | ~500–600 GB |

### Edge Node (Kiosk PC)
- 64-bit x86 CPU, 2 GB RAM (4 GB recommended), Ethernet or Wi-Fi.

### Flasher USB Drive
- Minimum 32 GB (preferably larger), write speed ≥ 15-20 MB/s. Recommended: Netac US5 level or higher.

---

## Repository Layout

```
.
├── backend/
│   ├── alembic/                 # DB migrations
│   ├── core/                    # Scheduler, HASP helper, disk ops
│   ├── playbooks/               # Ansible: bootstrap.yml, prepare.yml
│   ├── routers/                 # FastAPI route modules
│   │   ├── nodes_crud.py        #   Fleet CRUD
│   │   ├── nodes_actions.py     #   Bootstrap, prepare, backup triggers
│   │   ├── restore.py           #   Flasher logic
│   │   ├── groups.py            #   Backup group management
│   │   ├── kiosks.py            #   Kiosk pairing & control
│   │   ├── iso.py               #   Live-CD ISO generation
│   │   ├── settings.py          #   Global settings API
│   │   └── users.py             #   Auth & user management
│   ├── tasks/                   # Celery task modules
│   ├── tests/                   # Pytest unit tests
│   ├── backup_tasks.py          # Backup execution logic
│   ├── restore_logic.py         # Bare-metal flash routine
│   ├── iso_tasks.py             # ISO compilation tasks
│   ├── models.py                # SQLAlchemy models
│   ├── schemas.py               # Pydantic schemas
│   └── main.py                  # FastAPI app entry point
├── docker/
│   ├── backend/                 # Dockerfile: FastAPI + Worker
│   ├── borg/                    # Dockerfile: Borg SSH server
│   ├── frontend/                # Dockerfile: React + Nginx
│   └── apt-proxy/               # APT caching proxy
├── frontend/
│   ├── src/
│   │   ├── components/          # 30 React components (tabs, modals, etc.)
│   │   ├── context/             # Translation context provider
│   │   ├── i18n/                # EN/RU/UK translation dictionaries
│   │   ├── App.tsx              # Main app shell & navigation
│   │   └── index.css            # Tailwind + custom animations
│   └── nginx.conf               # Production static server config
├── docker-compose.yml           # Full stack definition (7 services)
└── .env.example                 # Environment variable template
```

---

## USB Hardware Stability

USB-to-SATA/NVMe adapters with UAS drivers (e.g., JMicron `152d:0581`) can hang during heavy `borg extract` writes. Force the stable legacy `usb-storage` driver:

```bash
echo -e "options usb-storage quirks=152d:0581:u\noptions uas quirks=152d:0581:u" \
  | sudo tee /etc/modprobe.d/usb-quirks.conf
sudo usbreset 152d:0581   # or re-plug the USB cable
```

Replace `152d:0581` with your adapter's Vendor:Product ID from `lsusb`.

---

## Security & Authentication

- **Dashboard login**: username + password, sessions via HTTP-only JWT cookies (`admin_session`).
- **Roles**:
  - **Superadmin** — manages other user accounts (Settings → Users tab).
  - **Admin** — operates the system but cannot manage users.
- **Kiosk auth**: paired Live-CD kiosks use pre-baked `Authorization: Bearer <token>` headers. Offline ISOs fall back to `?token=` query params.
- **First-run seed**: on initial startup, a superadmin account is created from `.env` values (`SUPERADMIN_USERNAME`, `ADMIN_PASSWORD`). Created once — subsequent `.env` changes won't overwrite UI-modified credentials. To reset: clear the `users` table or update `.env` + restart.

---

## What the Orchestrator Changes on Target Nodes

No persistent agents are installed. All actions are SSH-based, one-time.

### Bootstrap (initial provisioning)
| What | Details |
|------|---------|
| APT packages | `python3`, `python3-pip`, `borgbackup`, `parted`, `e2fsprogs`, `dosfstools`, `util-linux` |
| SSH key | Orchestrator's Ed25519 pubkey → `/root/.ssh/authorized_keys` |
| sshd_config | `PermitRootLogin prohibit-password` |
| Borg user | System user `borg` created with SSH keypair at `/home/borg/.ssh/` |
| Dead proxy bypass | APT proxy configs temporarily renamed `*.disabled`, restored after install |

### Auto-Prepare (disk labels)
| What | Details |
|------|---------|
| Partition labels | `edgeroot`, `edgeboot`, `edgelog`, `edgestor`, `EFI` via `e2label`/`fatlabel` |
| `/etc/fstab` | Replaced with label-based template. Backup saved as `/etc/fstab.bak` |
| Bootloader | `update-grub` + `update-initramfs -u` |

### Backup execution
| What | Details |
|------|---------|
| Files modified on node | **None** — `borg create` is read-only |
| Processes | Temporary `borg create` + `ssh` tunnel, both terminate on completion |
| CPU/IO control | Optional `systemd-run --scope -p CPUQuota=... -p IOSchedulingClass=idle` |

---

## Installation & Usage

→ **[English Usage Guide](README_USAGE.md)** — step-by-step deployment, configuration, and operations manual.

→ **[Русская инструкция](README_USAGE_ru.md)** — подробное руководство на русском.
