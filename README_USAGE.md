# Edge B.R.O. Installation & Usage Guide

🇬🇧 **[English README](README.md)** | 🇬🇧 **[English Usage Guide](README_USAGE.md)** | 🇷🇺 **[Русский README](README_ru.md)** | 🇷🇺 **[Русская инструкция (Usage Guide)](README_USAGE_ru.md)**

This document describes the complete deployment lifecycle of the backup and restore orchestration system, starting from a "barebone" PC (e.g., Intel NUC) to the full Bare-Metal Restore process on physical disks.

---

## 1. Server Preparation (Intel NUC)

A compact PC like an Intel NUC is perfectly suited to serve as the central management server (orchestrator).

1. Install a base Linux OS on it (e.g., Ubuntu 22.04/24.04 or Debian 12).
2. Install **Docker** and **Docker Compose**:
   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-v2
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER
   ```
   *(After adding your user to the docker group, re-login to apply changes).*

---

## 2. Orchestrator Deployment

1. Clone the project repository:
   ```bash
   git clone https://github.com/masseselsev/Edge.bro.git /opt/stacks/Edge.bro
   cd /opt/stacks/Edge.bro
   ```

2. Create a `.env` file in the project root by copying the template file and editing it. Be sure to use strong passwords:
   ```bash
   cp .env.example .env
   ```
   **Important parameter `ORCHESTRATOR_IP`**: if your edge nodes are located in a different subnet or connect via VPN/ZeroTier, specify the IP address of the NUC itself, by which it is accessible to the client machines. Nodes will push data to the central Borg repository via this IP.
   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your_secure_db_password
   POSTGRES_DB=borg_orchestrator
   REDIS_URL=redis://redis:6379/0
   BORG_PASSPHRASE=your_secure_borg_passphrase
   DATABASE_URL=postgresql://postgres:your_secure_db_password@db:5432/borg_orchestrator
   ORCHESTRATOR_IP=192.168.222.2  # Orchestrator IP accessible to edge nodes

   # SSH Keepalive connection stability settings (optional, defaults shown)
   # SSH_KEEPALIVE_INTERVAL=30    # Send alive packets every X seconds
   # SSH_KEEPALIVE_COUNT=3       # Drop connection after Y missed responses
   ```
   *(Note: You can override the orchestrator IP address on the fly later via the **Settings** tab in the web interface, without needing to edit the `.env` file or restart containers).*

3. **Configuring Backup Storage Location (Important!)**
   By default, all repositories are stored in an internal Docker volume (`borg-data`), which is physically located in `/var/lib/docker/volumes/`. Because backups can consume a lot of space, it is highly recommended to mount them on a separate large drive to avoid saturating the system root partition.

   > [!WARNING]
   > Running out of space on the root partition will cause backup tasks to fail with `Insufficient free space to complete transaction` errors and can disrupt other host services.
   > 
   > *Note: The orchestrator automatically executes `borg compact` after pruning or purging archives to reclaim disk space immediately, but utilizing a dedicated storage partition is still critical.*

   To mount an external folder (e.g., `/mnt/hdd/borg_data`), open the `.env` file and set the `BORG_HOST_DATA_PATH` variable:
   ```env
   BORG_HOST_DATA_PATH=/mnt/hdd/borg_data
   ```

   > [!IMPORTANT]
   > If you set a custom host directory, make sure the folder exists on the host machine and has the correct ownership permissions (`chown -R 1000:1000`) to prevent write permission failures inside the containers:
   > ```bash
   > mkdir -p /mnt/hdd/borg_data
   > chown -R 1000:1000 /mnt/hdd/borg_data
   > ```
   > If `BORG_HOST_DATA_PATH` is left as `borg-data` (default), Docker will use a standard named volume.

   The path configured here will also be dynamically displayed under the **Settings** tab in the management interface.

4. Start the containers:
   ```bash
   docker compose up -d --build
   ```
   *(Note: Database migrations run automatically on startup inside the backend container. It will wait for PostgreSQL to become online, apply Alembic migrations, and launch the server).*

5. **Done!** The management interface is available in your browser at `http://<YOUR-NUC-IP>:7777`.

---

## 3. Node Provisioning

Navigate to the **Fleet** tab in the web interface to add edge nodes.

1. **Add Node**:
   - You can enter IP addresses individually, as a comma-separated list, by range (e.g., `192.168.1.50-60`), or using a CIDR subnet (`10.0.0.0/24`).
   - Specify the login (usually `root` or `admin`), password, and SSH port (e.g., `22` or `2222`).
2. **Bootstrap**:
   - Upon adding, the orchestrator will automatically connect to the node, install necessary packages (even temporarily bypassing "dead" APT proxies if configured), and inject its public SSH key (`/root/.ssh/authorized_keys`).
   - Future interactions with the node will occur **passwordlessly** using the orchestrator's secure SSH key.
3. **Disk Preparation**:
   - During the prepare phase, the system reads the complex's current disk structure, saves the unique `EFI UUID`, and applies persistent filesystem labels (`edgeroot`, `edgeboot`). This protects the system against Linux disk naming drift.

---

## 4. Creating a Backup

1. In the **Fleet** tab, click the **Backup** button for the desired node.
2. In the resulting modal, you can click "View Logs" to monitor the real-time execution stream.
3. The orchestrator connects to the node via SSH and triggers the backup command from the client side (push model), streaming data to the central `borg-server` container.
4. System directories and configured paths are excluded dynamically based on the global exclusions setting (configured under the **Settings** tab in the web UI).
   - **Default Exclusions**: `/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*`
5. You can track progress and final sizes in the **History** tab.

### 4.1 Backup Resource Limits & Optimization

To prevent backup tasks from saturating target node CPU or available site bandwidth, you can configure granular limits when creating or editing a **Backup Group**:
* **Upload Rate Limit (Bandwidth)**: Constrain the maximum network throughput (in KiB/s) during backup data streams.
* **CPU Quota**: Restricts the maximum CPU time (0-400% of a single core) allocated to the backup processes. This is enforced directly on the client node via `systemd-run --scope -p CPUQuota=...`.
* **Compression**: Customizes the Borg compression algorithm (e.g., `lz4`, `zstd:1` up to `zstd:9`) per group.
* **Checkpoint Interval**:
  - By default, the system **auto-calculates** a dynamic checkpoint interval based on the configured bandwidth upload rate limit (e.g. checkpointing every ~50 MB of data at <= 500 KiB/s, or every ~200 MB at <= 5000 KiB/s) to ensure progress is saved frequently on slow links.
  - You can also explicitly override this to a manual interval (in seconds).
* **Retention Policy Override**: Toggle retention overrides to apply group-specific rules rather than inheriting the global policy. The system supports three pruning types:
  - **Interval**: Keep a specified number of daily, weekly, and monthly backups.
  - **Count**: Keep a fixed number of the last backups (e.g. keep last 5).
  - **Timeframe**: Keep all backups created within a specific timeframe (e.g. within the past 3 months).

Global default values for CPU Quotas, Compression levels, and the global Retention Policy can be adjusted inside the **Settings** tab.

### 💡 How Global Deduplication & Compression Work (Space Savings)
Because all edge nodes are backed up into a **single, shared central repository** (`/data/borg/fleet`), Borg's built-in deduplication engine and compression work across all devices simultaneously. 
This means identical system files (Linux kernel, Debian packages, libraries, application Docker images) present across dozens of different devices are physically stored on the server's disk **only once**, and all stored chunks are compressed.

**Example Savings (assuming a base OS footprint of ~6 GB per node):**
* **1st node (Compression & Internal Deduplication)**: saves **55% - 65%** right away due to Borg's built-in compression (e.g., `lz4`), taking only ~2.2 - 2.7 GB on the central disk.
* **Each additional similar node (Cross-node Deduplication)**: saves up to **97%** for identical/cloned nodes (adding only **~100 - 200 MB** for the same device under a different name) and saves about **20% - 30%** of space for nodes with minor system configuration and package differences.
* **Incremental backups**: of running systems tend towards only **~100 - 200 MB** of unique incremental data per run (storing only unique logs, cache differences, and database states).

As a result, an estate of similar Debian devices achieves massive storage requirement reductions, keeping both initial and incremental runs extremely lightweight.

---

## 5. Bare-Metal Restore Procedure

This feature allows you to flash a backup directly onto a physical disk connected to the orchestrator (NUC) via USB.

### 5.1 Connecting the Disk to the Orchestrator
For convenience, use an external **USB to SATA** or **USB to NVMe** dongle/adapter.

⚠️ **Drive Connection Rules**:
* **SATA Drives**: USB-SATA adapters generally support hot-plugging. You can connect an SSD to the adapter, and then plug the USB directly into the NUC while powered on.
* **NVMe Drives**: Most M.2 NVMe to USB adapters **do not support** hot-swapping the drive itself from the socket while under power. Always insert the NVMe drive into the adapter first, and only then connect the USB cable to the NUC. When ejecting, ensure writes have completed, perform a safe eject, and then unplug the USB cable. It is strictly forbidden to remove the M.2 board from the adapter while it is plugged into the USB port.

### 5.2 Flashing Process
1. Navigate to the **Flasher** tab in the interface.
2. **Target Device**: On the right side of the screen, select your connected USB disk. The system displays physical disk labels (model, size) and bus type (SATA/NVMe).
   *(The NUC's own system disk is filtered out and protected from accidental overwriting, but still be careful).* 
3. **Select Node & Archive**: On the left side, select the node you want to restore and the specific snapshot (archive) from the list.
   *(💡 **Cross-Drive Migration (NVMe ↔ SATA)**: Because the system strictly binds disks using filesystem labels (`LABEL=edgeroot`) rather than hardware paths, you can freely restore a backup taken from an NVMe drive onto a standard SATA disk, or vice versa. If there is a drive type mismatch, the interface will show a "Drive type mismatch" warning (by-design) to prevent accidental operator error. You can safely ignore it and confirm the operation).*
4. Click **Start Flashing**. 
   A log console will open showing the entire process:
   - The disk is completely wiped (`wipefs`) and re-partitioned using a GPT table.
   - Partitions are created: EFI (`vfat`) and system partitions (`ext4`). For `ext4`, the incompatible `orphan_file` feature is disabled to guarantee boot compatibility even on older Debian 10 systems.
   - The historical EFI UUID of the boot partition is restored (so the node's motherboard recognizes the bootloader).
   - Files from the Borg archive are extracted onto the mounted disk.
   - System directories are bind-mounted (`chroot`) and the GRUB bootloader is updated.
   - Network settings are reset (persistent-net-rules removed) and a universal DHCP configuration is injected (for `eth*` and `en*`). This ensures that when the disk is placed into new hardware (where MAC addresses and network card names change), the node will automatically receive an IP address.
5. After the message `Restore completed successfully!` appears, the disk is safely unmounted.
6. Disconnect the USB adapter from the NUC, remove the disk, and install it into the target edge node. Power on the node — it will boot fully functional with all data from the time the backup was taken.

---

## 6. Live-USB Offline Restoration (Kiosk Mode)

While Section 5 describes restoring a disk by physically connecting it to the NUC, you can also restore edge nodes **without extracting their disks** by using the generated Live-USB.

### 6.1 Generating the Live-USB
1. Navigate to the **Live-USB Client** tab in the web interface.
2. Enter the Orchestrator's IP address (the NUC) and an API Authentication Token. These credentials will be securely "baked" into the ISO so the client can automatically authenticate with the server.
3. Click **GENERATE LIVE-USB**. The orchestrator will dynamically fetch the exact file size, download a base Debian testing ISO, inject the configuration, and compile a custom `technician_client_v1.iso`.
4. Once generation is complete, click **DOWNLOAD ISO IMAGE** and flash it to a USB drive using [Rufus](https://rufus.ie/en/) or [balenaEtcher](https://etcher.balena.io/).

### 6.2 Using the Live-USB
1. Insert the generated USB drive into the broken edge node and boot from it.
2. Ensure the node is connected to the same local network as the orchestrator.
3. The system will automatically boot into a lightweight Debian XFCE environment and launch a secure kiosk browser.
4. The kiosk will connect to the central orchestrator and present the Flasher interface.
5. Select the local disk of the node and the desired backup snapshot. The restoration process will securely pull the backup data over the network and flash it directly to the node's internal disk.

---

## 7. Detailed List of Orchestrator Changes to Client Nodes

Throughout its lifecycle, the orchestrator performs a strictly defined set of non-destructive operations on remote nodes, installing only the dependencies necessary for its operation:

### During the Bootstrap Phase
1. **Bypassing Broken Proxies**: The system tests the reachability of proxy servers specified in `/etc/apt/apt.conf` and `/etc/apt/apt.conf.d/`. If a proxy is accessible, it is used normally. Only if a proxy is "dead" (the server is offline or unreachable) will its configuration be temporarily disabled and then restored once package installation completes.
2. **System Package Installation**: Installs `python3` and `aptitude` (required for core Ansible functionality).
3. **Passwordless SSH Configuration**:
   - Creates the `/root/.ssh` directory (permissions `0700`).
   - Adds the orchestrator's Ed25519 public key to `/root/.ssh/authorized_keys` (permissions `0600`).
   - Changes the `PermitRootLogin` parameter in `/etc/ssh/sshd_config` to `prohibit-password` (allowing root login via keys only and disabling password login).
   - Restarts the `ssh` / `sshd` service.

### During the Prepare Phase
1. **Disk and Backup Utility Installation**: Installs `e2fsprogs`, `parted`, `dosfstools`, `borgbackup`.
2. **Filesystem Labeling** (using the `e2label` utility):
   - The root partition (`/`) is assigned the persistent label `edgeroot`.
   - The boot partition (`/boot`) is assigned the label `edgeboot`.
   - The log partition (`/var/log/edge`) is assigned the label `edgelog`.
   - The data partition (`/var/opt/edge`) is assigned the label `edgestor`.
3. **Updating /etc/fstab**: Old disk bindings are replaced with a strict mounting template based on the created labels (`LABEL=edgeroot`, `LABEL=edgeboot`, etc.). The only exception is the EFI partition (`/boot/efi`), which is mounted using its native hardware `UUID`.
4. **Configuration Extraction**: The unique EFI UUID of the node is read and transmitted to the orchestrator's database.

### During the Backup Phase
**No new packages are installed** on the node. The orchestrator merely executes the already installed `borg create` client, which:
1. Scans the node's filesystem.
2. Automatically **excludes** virtual, temporary, and configured edge directories based on the orchestrator's global settings (default exclusions: `/dev/*`, `/proc/*`, `/sys/*`, `/run/*`, `/mnt/*`, `/media/*`, `/lost+found`, `/var/log/edge/*`, `/var/opt/edge/*`).
3. Hashes the data and securely transmits only new, deduplicated blocks over SSH to the orchestrator's central `borg-server`.
