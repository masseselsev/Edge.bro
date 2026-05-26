# Backup-edge-Restore Installation & Usage Guide

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
   git clone https://github.com/masseselsev/Backup-edge-Restore.git /opt/stacks/Backup-edge-Restore
   cd /opt/stacks/Backup-edge-Restore
   ```

2. Create a `.env` file in the project root. Be sure to use strong passwords. 
   **Important parameter `ORCHESTRATOR_IP`**: if your edge nodes are located in a different subnet or connect via VPN/ZeroTier, specify the IP address of the NUC itself, by which it is accessible to the client machines. Nodes will push data to the central Borg repository via this IP.
   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=your_secure_db_password
   POSTGRES_DB=borg_orchestrator
   REDIS_URL=redis://redis:6379/0
   BORG_PASSPHRASE=your_secure_borg_passphrase
   DATABASE_URL=postgresql://postgres:your_secure_db_password@db:5432/borg_orchestrator
   ORCHESTRATOR_IP=192.168.222.2  # Orchestrator IP accessible to edge nodes
   ```
   *(Note: You can override this address on the fly later via the **Settings** tab in the web interface, without needing to edit the `.env` file or restart containers).*

3. **Configuring Backup Storage Location (Important!)**
   By default, all repositories are stored in an internal Docker volume (`borg-data`), which is physically located in `/var/lib/docker/volumes/`. Because backups can consume a lot of space, it is highly recommended to mount them on a separate large drive.
   To mount an external folder (e.g., `/mnt/hdd/borg_data`), open the `docker-compose.yml` file, find the `volumes:` block at the very bottom of the file, and override `borg-data` as follows:
   ```yaml
   volumes:
     pg-data:
     ssh-keys:
     borg-data:
       driver: local
       driver_opts:
         type: none
         o: bind
         device: /mnt/hdd/borg_data
   ```
   *(Ensure the `/mnt/hdd/borg_data` folder exists on the host machine).*

4. Start the containers:
   ```bash
   docker compose up -d --build
   ```

5. Initialize the database (run migrations):
   ```bash
   docker compose exec backend alembic upgrade head
   ```

6. **Done!** The management interface is available in your browser at `http://<YOUR-NUC-IP>:7777`.

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
4. System directories (such as `/dev`, `/proc`, `/sys`) are automatically excluded.
5. You can track progress and final sizes in the **History** tab.

### 💡 How Global Deduplication Works (Space Savings)
Because all edge nodes are backed up into a **single, shared central repository** (`/data/borg/fleet`), Borg's built-in deduplication engine works across all devices simultaneously. 
This means identical system files (Linux kernel, Debian packages, libraries, application Docker images) present across dozens of different devices are physically stored on the server's disk **only once**.

**Example Savings (assuming a base OS footprint of ~6 GB):**
* **1 copy (1 node)**: takes ~6 GB
* **3 nodes**: ~6.2 GB *(only unique logs, configs, and keys are added)*
* **10 nodes**: ~6.5 GB *(instead of 60 GB using classic backups)*
* **100 nodes**: ~9 GB *(instead of 600+ GB!)*

As a result, an estate of identical Debian devices can achieve storage requirement reductions of **98% or more**.

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

## 6. Detailed List of Orchestrator Changes to Client Nodes

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
2. Automatically **excludes** virtual and temporary directories: `/dev/*`, `/proc/*`, `/sys/*`, `/run/*`, `/mnt/*`.
3. Hashes the data and securely transmits only new, deduplicated blocks over SSH to the orchestrator's central `borg-server`.
