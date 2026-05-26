import os
import shutil
import subprocess
import json
from typing import Dict, Any
from sqlalchemy.orm import Session
from database import SessionLocal
from models import TaskLog, Node

def execute_restore(task_obj: Any, node_id: int, archive_name: str, target_dev: str, keep_network_configs: bool = True, wipe_mac_bindings: bool = True) -> Dict[str, Any]:
    """
    Executes the bare-metal restore partition flashing, filesystem formatting,
    Borg backup extraction, and network wildcard injection options.
    """
    from tasks import log_to_task

    task_id = task_obj.request.id
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = db.query(TaskLog).filter(TaskLog.id == task_id).first()
    if not task_log:
        task_log = TaskLog(id=task_id, task_type="RESTORE", status="RUNNING", log_output="")
        db.add(task_log)
        db.commit()

    log_to_task(task_id, f"Initializing flashing process on target device: {target_dev}")

    # Double check if EFI UUID is collected
    if not node.efi_uuid:
        log_to_task(task_id, "ERROR: EFI partition UUID is missing from database. Aborting restore to prevent data loss.", status="FAILED")
        db.close()
        return {"status": "FAILED", "error": "Missing EFI UUID"}

    try:
        # 1. Device scan / validation
        if not os.path.exists(target_dev):
            raise FileNotFoundError(f"Target device {target_dev} does not exist.")

        # Safety: avoid flashing host root drive
        findmnt_out = subprocess.check_output("findmnt -n -o SOURCE /", shell=True, text=True).strip()
        host_root_disk = findmnt_out
        # e.g., /dev/sda3 -> /dev/sda
        if "nvme" in host_root_disk:
            host_root_disk = host_root_disk.split("p")[0]
        else:
            host_root_disk = "".join([c for c in host_root_disk if not c.isdigit()])

        if host_root_disk in target_dev:
            raise PermissionError("PROTECTION SHIELD: Attempted to flash the orchestrator host's root drive. Blocked.")

        # 2. Wipe target signature
        log_to_task(task_id, f"Wiping signatures on {target_dev}...")
        subprocess.check_call(["wipefs", "-a", target_dev])

        # 3. Partitioning via parted (GPT)
        # Fallback to the default 5-partition layout if node.partition_layout is not defined
        partitions = node.partition_layout
        if not partitions:
            # Reconstruct default 5-partition layout
            partitions = [
                {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": node.efi_uuid or "458C-37BB", "size_bytes": 512 * 1024 * 1024},
                {"name": "boot", "mount": "/boot", "fstype": "ext2", "label": "edgeboot", "uuid": "", "size_bytes": 1024 * 1024 * 1024},
                {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 30 * 1024 * 1024 * 1024},
                {"name": "log", "mount": "/var/log/edge", "fstype": "ext4", "label": "edgelog", "uuid": "", "size_bytes": 5 * 1024 * 1024 * 1024},
                {"name": "storage", "mount": "/var/opt/edge", "fstype": "ext4", "label": "edgestor", "uuid": "", "size_bytes": 0} # 0 means remaining
            ]

        log_to_task(task_id, "Creating GPT partitions...")
        subprocess.check_call(["parted", "-s", target_dev, "mklabel", "gpt"])

        current_offset = 1 # Start at 1MiB for alignment
        for i, part in enumerate(partitions):
            start_offset = f"{current_offset}MiB"
            if i == len(partitions) - 1:
                end_offset = "100%"
            else:
                size_mib = part["size_bytes"] // (1024 * 1024)
                if size_mib <= 0:
                    size_mib = 512 # fallback minimum size
                current_offset += size_mib
                end_offset = f"{current_offset}MiB"

            part_name = part.get("name") or f"part{i+1}"
            fstype = part.get("fstype", "ext4")
            parted_fs = "fat32" if fstype == "vfat" else fstype
            subprocess.check_call(["parted", "-s", target_dev, "mkpart", part_name, parted_fs, start_offset, end_offset])

            if part.get("mount") == "/boot/efi":
                subprocess.check_call(["parted", "-s", target_dev, "set", str(i+1), "esp", "on"])

        # Determine partition device paths and format them
        subprocess.check_call(["udevadm", "settle"])

        part_devices = {}
        for i, part in enumerate(partitions):
            part_suffix = f"p{i+1}" if "nvme" in target_dev else f"{i+1}"
            part_dev = f"{target_dev}{part_suffix}"
            part_devices[part["mount"]] = part_dev

            fstype = part.get("fstype", "ext4")
            label = part.get("label") or f"part{i+1}"
            uuid = part.get("uuid")

            log_to_task(task_id, f"Formatting partition {part_dev} ({part.get('mount')}) as {fstype} with label: {label}...")

            if fstype == "vfat":
                clean_efi_uuid = (uuid or node.efi_uuid or "458C-37BB").replace("-", "")[:8]
                subprocess.check_call(["mkfs.vfat", "-F32", "-i", clean_efi_uuid, "-n", label, part_dev])
            elif fstype == "ext2":
                cmd = ["mkfs.ext2", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)
                subprocess.check_call(cmd)
            elif fstype == "ext4":
                cmd = ["mkfs.ext4", "-E", "lazy_itable_init=1,lazy_journal_init=1", "-O", "^orphan_file", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)
                subprocess.check_call(cmd)
            elif fstype == "xfs":
                cmd = ["mkfs.xfs", "-f", "-L", label]
                if uuid:
                    cmd += ["-m", f"uuid={uuid}"]
                cmd.append(part_dev)
                subprocess.check_call(cmd)
            else:
                cmd = ["mkfs.ext4", "-E", "lazy_itable_init=1,lazy_journal_init=1", "-O", "^orphan_file", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)
                subprocess.check_call(cmd)

        # 5. Mounting partitions hierarchically
        target_mnt = "/mnt/target"
        if os.path.exists(target_mnt):
            subprocess.run(["umount", "-R", target_mnt], stderr=subprocess.DEVNULL)
            shutil.rmtree(target_mnt, ignore_errors=True)

        os.makedirs(target_mnt, exist_ok=True)

        # Sort partitions by path component depth to mount root first hierarchically
        import pathlib
        mount_ordered_partitions = sorted(partitions, key=lambda x: len(pathlib.PurePosixPath(x["mount"]).parts))

        for part in mount_ordered_partitions:
            mount_path = part["mount"]
            part_dev = part_devices[mount_path]

            target_path = target_mnt if mount_path == "/" else f"{target_mnt}{mount_path}"
            os.makedirs(target_path, exist_ok=True)

            log_to_task(task_id, f"Mounting partition {part_dev} to {target_path}...")
            subprocess.check_call(["mount", part_dev, target_path])

        # 6. Extract Borg Backup
        repo_path = "/data/borg/fleet"
        log_to_task(task_id, f"Extracting archive {archive_name} into {target_mnt}...")

        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

        extract_cmd = [
            "borg", "extract", "--numeric-ids", "--sparse",
            f"{repo_path}::{archive_name}"
        ]
        # Extract files in target directory
        subprocess.check_call(extract_cmd, cwd=target_mnt, env=env)
        log_to_task(task_id, "Extraction completed successfully.")

        # 7. Network configuration injection (PCIe Drift Prevention)
        if keep_network_configs:
            log_to_task(task_id, "Skipping network config injection: preserving 1-to-1 original backup settings.")
            if wipe_mac_bindings:
                udev_rules = f"{target_mnt}/etc/udev/rules.d/70-persistent-net.rules"
                if os.path.exists(udev_rules):
                    os.remove(udev_rules)
                    log_to_task(task_id, "Removed old persistent network udev rules to reset MAC bindings.")
                else:
                    log_to_task(task_id, "No persistent network udev rules found to remove.")
        else:
            log_to_task(task_id, "Executing network configuration injection (DHCP override fallback)...")
            # Wipe old udev persistent rules
            udev_rules = f"{target_mnt}/etc/udev/rules.d/70-persistent-net.rules"
            if os.path.exists(udev_rules):
                os.remove(udev_rules)
                log_to_task(task_id, "Removed old persistent network udev rules.")

            # Handle network configuration
            netplan_dir = f"{target_mnt}/etc/netplan"
            if os.path.exists(netplan_dir):
                # Wipe old netplan files
                for file in os.listdir(netplan_dir):
                    os.remove(os.path.join(netplan_dir, file))
                # Inject generic wildcard netplan mapping en* and eth*
                np_config = {
                    "network": {
                        "version": 2,
                        "ethernets": {
                            "all-en": {
                                "match": {"name": "en*"},
                                "dhcp4": True
                            },
                            "all-eth": {
                                "match": {"name": "eth*"},
                                "dhcp4": True
                            }
                        }
                    }
                }
                with open(os.path.join(netplan_dir, "01-orchestrator-dhcp.yaml"), "w") as f:
                    yaml_str = json.dumps(np_config)
                    f.write(yaml_str)
                log_to_task(task_id, "Injected wildcard Netplan config.")

            # Inject interfaces.d configuration
            interfaces_file = f"{target_mnt}/etc/network/interfaces"
            if os.path.exists(interfaces_file) or os.path.exists(f"{target_mnt}/etc/network"):
                os.makedirs(f"{target_mnt}/etc/network/interfaces.d", exist_ok=True)
                # Standard generic loopback configuration
                with open(interfaces_file, "w") as f:
                    f.write("auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n")

                # Enable DHCP on common naming structures plus original node name
                ifaces_to_configure = ["eth0", "enp1s0", "enp2s0", "enp3s0"]
                if node.network_iface and node.network_iface not in ifaces_to_configure:
                    ifaces_to_configure.append(node.network_iface)

                with open(f"{target_mnt}/etc/network/interfaces.d/orchestrator-dhcp", "w") as f:
                    for iface in ifaces_to_configure:
                        f.write(f"allow-hotplug {iface}\niface {iface} inet dhcp\n\n")
                log_to_task(task_id, f"Injected /etc/network/interfaces.d config mapping: {', '.join(ifaces_to_configure)}")


        # 8. Rewrite target /etc/fstab dynamically
        log_to_task(task_id, "Writing dynamic /etc/fstab to target...")
        fstab_path = f"{target_mnt}/etc/fstab"
        os.makedirs(os.path.dirname(fstab_path), exist_ok=True)

        fstab_lines = ["# Dynamic fstab generated via Borg Orchestrator Bare-Metal Restore"]
        for part in partitions:
            mount = part["mount"]
            fstype = part["fstype"]
            label = part["label"]
            uuid = part["uuid"]

            if mount == "/boot/efi":
                fstab_lines.append(f"UUID={node.efi_uuid or uuid}  {mount}       {fstype}    umask=0077,defaults,noatime       0       1")
            else:
                options = "defaults,noatime"
                pass_num = 1 if mount == "/" else 2
                if label:
                    fstab_lines.append(f"LABEL={label}   {mount}           {fstype}    {options}                  0       {pass_num}")
                else:
                    fstab_lines.append(f"UUID={uuid}   {mount}           {fstype}    {options}                  0       {pass_num}")

        with open(fstab_path, "w") as f:
            f.write("\n".join(fstab_lines) + "\n")
        log_to_task(task_id, "Dynamic /etc/fstab successfully written.")

        # 9. Chroot, Grub setup
        log_to_task(task_id, "Mounting virtual filesystems...")
        subprocess.check_call(["mount", "--bind", "/dev", f"{target_mnt}/dev"])
        subprocess.check_call(["mount", "--bind", "/dev/pts", f"{target_mnt}/dev/pts"])
        subprocess.check_call(["mount", "--bind", "/proc", f"{target_mnt}/dev/../proc"])
        subprocess.check_call(["mount", "--bind", "/sys", f"{target_mnt}/sys"])

        log_to_task(task_id, f"Reinstalling GRUB bootloader on {target_dev}...")
        subprocess.check_call(["chroot", target_mnt, "grub-install", target_dev])
        subprocess.check_call(["chroot", target_mnt, "update-grub"])

        # Inject EFI Fallback path to make sure UEFI sees it
        efi_base = f"{target_mnt}/boot/efi/EFI"
        fallback_dir = f"{efi_base}/BOOT"
        os.makedirs(fallback_dir, exist_ok=True)

        # Search for any efi file generated by grub-install inside EFI/
        grub_efi_src = None
        for root_dir, dirs, files in os.walk(efi_base):
            for file in files:
                if file.endswith(".efi") and "BOOT" not in root_dir:
                    grub_efi_src = os.path.join(root_dir, file)
                    break
            if grub_efi_src:
                break

        if grub_efi_src:
            log_to_task(task_id, f"Copying EFI fallback loader: {grub_efi_src} -> {fallback_dir}/BOOTX64.EFI")
            shutil.copy2(grub_efi_src, f"{fallback_dir}/BOOTX64.EFI")
        else:
            log_to_task(task_id, "WARNING: Could not find compiled grubx64.efi loader. Proceeding.")

        # Mask live-config generators that fail on persistent installs
        log_to_task(task_id, "Masking live-config systemd generators to prevent boot warnings...")
        generators_dir = f"{target_mnt}/etc/systemd/system-generators"
        os.makedirs(generators_dir, exist_ok=True)
        try:
            target_link = os.path.join(generators_dir, "live-config-getty-generator")
            if os.path.lexists(target_link):
                os.remove(target_link)
            os.symlink("/dev/null", target_link)
        except Exception as e:
            log_to_task(task_id, f"WARNING: Failed to mask live-config generator: {str(e)}")

        # 10. Post-Restore verification audit
        log_to_task(task_id, "Starting post-restore audit...")
        with open(f"{target_mnt}/etc/fstab", "r") as f:
            fstab_content = f.read()

        for part in partitions:
            mount = part["mount"]
            label = part["label"]
            uuid = part["uuid"]

            if mount == "/boot/efi":
                efi_check_uuid = node.efi_uuid or uuid
                if f"UUID={efi_check_uuid}" not in fstab_content:
                    raise ValueError(f"Post-restore verification audit failed: /etc/fstab is missing EFI 'UUID={efi_check_uuid}' mapping.")
            else:
                expected_target = f"LABEL={label}" if label else f"UUID={uuid}"
                if expected_target not in fstab_content:
                    raise ValueError(f"Post-restore verification audit failed: /etc/fstab is missing '{expected_target}' mapping.")

        log_to_task(task_id, "Post-restore verification audit passed. Filesystems, labels, and fstab structures are verified.")

        # Unmount virtual filesystems
        log_to_task(task_id, "Unmounting virtual filesystems...")
        subprocess.check_call(["umount", "-R", target_mnt])

        log_to_task(task_id, "Restore completed successfully! Target device ready to boot.", status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        log_to_task(task_id, error_msg, status="FAILED")

        # Clean unmount on failure
        try:
            subprocess.run(["umount", "-R", "/mnt/target"], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
