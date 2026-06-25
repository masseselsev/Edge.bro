import os
import shutil
import subprocess
import json
from typing import Dict, Any, List, Callable, Optional
import pathlib

def format_and_restore(
    target_dev: str,
    partitions: List[Dict[str, Any]],
    efi_uuid: str,
    archive_name: str,
    repo_path: str,
    keep_network_configs: bool,
    wipe_mac_bindings: bool,
    network_iface: str,
    total_files: int,
    log_callback: Callable[[str, Optional[int], Optional[str]], None]
) -> Dict[str, Any]:
    """
    Core logic for bare-metal restore partition flashing, filesystem formatting,
    Borg backup extraction, and network wildcard injection.
    
    This is a shared module used by both the main orchestrator backend and the offline payload client.
    `log_callback` signature: func(message: str, progress: Optional[int] = None, status: Optional[str] = None)
    """

    def emit_log(msg: str, prog: Optional[int] = None, status: Optional[str] = None):
        log_callback(msg, prog, status)

    emit_log(f"Initializing flashing process on target device: {target_dev}", prog=5)

    try:
        # 1. Device scan / validation
        if not os.path.exists(target_dev):
            raise FileNotFoundError(f"Target device {target_dev} does not exist.")

        # Safety: avoid flashing host root drive
        findmnt_out = subprocess.check_output("findmnt -n -o SOURCE /", shell=True, text=True).strip()
        host_root_disk = findmnt_out
        if "nvme" in host_root_disk:
            host_root_disk = host_root_disk.split("p")[0]
        else:
            host_root_disk = "".join([c for c in host_root_disk if not c.isdigit()])

        if host_root_disk in target_dev:
            raise PermissionError("PROTECTION SHIELD: Attempted to flash the orchestrator host's root drive. Blocked.")

        # 1.5. Release active mount locks on target device & its partitions
        try:
            import re
            if os.path.exists("/proc/mounts"):
                part_pattern = re.compile(r"^" + re.escape(target_dev) + r"(p?\d+)?$")
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            dev_src = parts[0]
                            if part_pattern.match(dev_src):
                                mount_point = parts[1]
                                emit_log(f"Releasing mount lock: unmounting {dev_src} from {mount_point}...", prog=8)
                                subprocess.call(["umount", "-l", mount_point])
        except Exception as ue:
            emit_log(f"Warning: Failed to release mount locks: {str(ue)}")

        # 2. Wipe target signature
        emit_log(f"Wiping signatures on {target_dev}...", prog=10)
        subprocess.check_call(["wipefs", "-a", target_dev])

        # 3. Partitioning via parted (GPT)
        emit_log("Creating GPT partitions...", prog=15)
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

        # Set pmbr_boot flag to 'on' for older BIOS compatibility
        try:
            emit_log(f"Setting pmbr_boot flag to 'on' on device {target_dev}...")
            subprocess.check_call(["parted", "-s", target_dev, "disk_set", "pmbr_boot", "on"])
        except Exception as e:
            emit_log(f"WARNING: Failed to set pmbr_boot flag: {str(e)}")

        # Restore original PARTUUIDs if present
        for i, part in enumerate(partitions):
            partuuid = part.get("partuuid")
            if partuuid:
                try:
                    emit_log(f"Restoring PARTUUID {partuuid} for partition {i+1}...")
                    subprocess.check_call(["sfdisk", "--part-uuid", target_dev, str(i+1), partuuid])
                except Exception as e:
                    emit_log(f"WARNING: Failed to restore PARTUUID for partition {i+1}: {str(e)}")

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

            progress_val = 20 + int((i / len(partitions)) * 20)
            emit_log(f"Formatting partition {part_dev} ({part.get('mount')}) as {fstype} with label: {label}...", prog=progress_val)

            if fstype == "vfat":
                clean_efi_uuid = (uuid or efi_uuid or "458C-37BB").replace("-", "")[:8]
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

        mount_ordered_partitions = sorted(partitions, key=lambda x: len(pathlib.PurePosixPath(x["mount"]).parts))

        for part in mount_ordered_partitions:
            mount_path = part["mount"]
            part_dev = part_devices[mount_path]

            target_path = target_mnt if mount_path == "/" else f"{target_mnt}{mount_path}"
            os.makedirs(target_path, exist_ok=True)

            emit_log(f"Mounting partition {part_dev} to {target_path}...", prog=42)
            subprocess.check_call(["mount", part_dev, target_path])

        # 6. Extract Borg Backup
        emit_log(f"Extracting archive {archive_name} into {target_mnt}...", prog=45)

        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

        if repo_path.startswith("ssh://"):
            kiosk_key = "/opt/offline-client/backend/id_ed25519"
            host_key = "/root/.ssh/id_ed25519"
            key_path = kiosk_key if os.path.exists(kiosk_key) else host_key
            if os.path.exists(key_path):
                env["BORG_RSH"] = f"ssh -i {key_path} -o StrictHostKeyChecking=no"

        extract_cmd = [
            "borg", "extract", "--numeric-ids", "--sparse", "--progress",
            f"{repo_path}::{archive_name}"
        ]
        
        proc = subprocess.Popen(
            extract_cmd, 
            cwd=target_mnt, 
            env=env, 
            stderr=subprocess.PIPE, 
            text=True, 
            bufsize=1
        )

        buffer = ""
        try:
            while True:
                char = proc.stderr.read(1)
                if not char:
                    break
                if char == '\\r' or char == '\\n':
                    line = buffer.strip()
                    buffer = ""
                    if "files" in line and total_files > 0:
                        parts = line.split()
                        try:
                            idx = parts.index("files")
                            curr_files = int(parts[idx - 1].replace(",", ""))
                            pct = int((curr_files / total_files) * 45)
                            progress_val = 45 + pct
                            emit_log(f"Extracting files ({curr_files}/{total_files})...", prog=progress_val)
                        except ValueError:
                            pass
                else:
                    buffer += char
            
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, extract_cmd)
            emit_log("Extraction completed successfully.", prog=90)
        except Exception as e:
            proc.kill()
            raise e

        # 7. Network configuration injection
        if keep_network_configs:
            emit_log("Skipping network config injection: preserving 1-to-1 original backup settings.")
            if wipe_mac_bindings:
                udev_rules = f"{target_mnt}/etc/udev/rules.d/70-persistent-net.rules"
                if os.path.exists(udev_rules):
                    os.remove(udev_rules)
                    emit_log("Removed old persistent network udev rules to reset MAC bindings.")

            emit_log("Patching restored /etc/network/interfaces and interfaces.d to use allow-hotplug for physical interfaces...")
            interfaces_file = f"{target_mnt}/etc/network/interfaces"
            interfaces_d = f"{target_mnt}/etc/network/interfaces.d"
            
            paths_to_patch = []
            if os.path.exists(interfaces_file):
                paths_to_patch.append(interfaces_file)
            if os.path.exists(interfaces_d):
                try:
                    for f_name in os.listdir(interfaces_d):
                        full_path = os.path.join(interfaces_d, f_name)
                        if os.path.isfile(full_path):
                            paths_to_patch.append(full_path)
                except Exception as e:
                    emit_log(f"WARNING: Failed to list interfaces.d directory: {str(e)}")
            
            for path in paths_to_patch:
                try:
                    with open(path, "r") as f:
                        lines = f.readlines()
                    modified = False
                    new_lines = []
                    for line in lines:
                        stripped = line.strip()
                        if stripped.startswith("auto ") or stripped.startswith("auto\t"):
                            parts = stripped.split()
                            ifaces = parts[1:]
                            non_lo_ifaces = [i for i in ifaces if i != "lo"]
                            lo_ifaces = [i for i in ifaces if i == "lo"]
                            
                            if non_lo_ifaces:
                                new_parts = []
                                if lo_ifaces:
                                    new_parts.append(f"auto {' '.join(lo_ifaces)}")
                                for iface in non_lo_ifaces:
                                    new_parts.append(f"allow-hotplug {iface}")
                                new_lines.append("\\n".join(new_parts))
                                modified = True
                            else:
                                new_lines.append(line.rstrip("\\r\\n"))
                        else:
                            new_lines.append(line.rstrip("\\r\\n"))
                    if modified:
                        with open(path, "w") as f:
                            f.write("\\n".join(new_lines) + "\\n")
                        emit_log(f"Successfully converted auto to allow-hotplug in {os.path.basename(path)}")
                except Exception as e:
                    emit_log(f"WARNING: Failed to patch network file {path}: {str(e)}")

        else:
            emit_log("Executing network configuration injection (DHCP override fallback)...")
            udev_rules = f"{target_mnt}/etc/udev/rules.d/70-persistent-net.rules"
            if os.path.exists(udev_rules):
                os.remove(udev_rules)
                emit_log("Removed old persistent network udev rules.")

            netplan_dir = f"{target_mnt}/etc/netplan"
            if os.path.exists(netplan_dir):
                for file in os.listdir(netplan_dir):
                    os.remove(os.path.join(netplan_dir, file))
                np_config = {
                    "network": {
                        "version": 2,
                        "ethernets": {
                            "all-en": {"match": {"name": "en*"}, "dhcp4": True},
                            "all-eth": {"match": {"name": "eth*"}, "dhcp4": True}
                        }
                    }
                }
                with open(os.path.join(netplan_dir, "01-orchestrator-dhcp.yaml"), "w") as f:
                    yaml_str = json.dumps(np_config)
                    f.write(yaml_str)
                emit_log("Injected wildcard Netplan config.")

            interfaces_file = f"{target_mnt}/etc/network/interfaces"
            if os.path.exists(interfaces_file) or os.path.exists(f"{target_mnt}/etc/network"):
                os.makedirs(f"{target_mnt}/etc/network/interfaces.d", exist_ok=True)
                with open(interfaces_file, "w") as f:
                    f.write("auto lo\\niface lo inet loopback\\nsource /etc/network/interfaces.d/*\\n")

                ifaces_to_configure = ["eth0", "enp1s0", "enp2s0", "enp3s0"]
                if network_iface and network_iface not in ifaces_to_configure:
                    ifaces_to_configure.append(network_iface)

                with open(f"{target_mnt}/etc/network/interfaces.d/orchestrator-dhcp", "w") as f:
                    for iface in ifaces_to_configure:
                        f.write(f"allow-hotplug {iface}\\niface {iface} inet dhcp\\n\\n")
                emit_log(f"Injected /etc/network/interfaces.d config mapping: {', '.join(ifaces_to_configure)}")

        # 8. Rewrite target /etc/fstab dynamically
        emit_log("Writing dynamic /etc/fstab to target...")
        fstab_path = f"{target_mnt}/etc/fstab"
        os.makedirs(os.path.dirname(fstab_path), exist_ok=True)

        fstab_lines = ["# Dynamic fstab generated via Borg Orchestrator Bare-Metal Restore"]
        for part in partitions:
            mount = part["mount"]
            fstype = part["fstype"]
            label = part["label"]
            uuid = part["uuid"]

            if mount == "/boot/efi":
                fstab_lines.append(f"UUID={efi_uuid or uuid}  {mount}       {fstype}    umask=0077,defaults,noatime       0       1")
            else:
                options = "defaults,noatime"
                pass_num = 1 if mount == "/" else 2
                if label:
                    fstab_lines.append(f"LABEL={label}   {mount}           {fstype}    {options}                  0       {pass_num}")
                else:
                    fstab_lines.append(f"UUID={uuid}   {mount}           {fstype}    {options}                  0       {pass_num}")

        with open(fstab_path, "w") as f:
            f.write("\\n".join(fstab_lines) + "\\n")
        emit_log("Dynamic /etc/fstab successfully written.")

        # 9. Chroot, Grub setup
        emit_log("Mounting virtual filesystems...", prog=94)
        subprocess.check_call(["mount", "--bind", "/dev", f"{target_mnt}/dev"])
        subprocess.check_call(["mount", "--bind", "/dev/pts", f"{target_mnt}/dev/pts"])
        subprocess.check_call(["mount", "--bind", "/proc", f"{target_mnt}/dev/../proc"])
        subprocess.check_call(["mount", "--bind", "/sys", f"{target_mnt}/sys"])

        emit_log(f"Reinstalling GRUB bootloader on {target_dev}...", prog=96)
        target_grub_dir = os.path.join(target_mnt, "usr/lib/grub")
        is_efi = os.path.exists(os.path.join(target_grub_dir, "x86_64-efi"))
        is_bios = os.path.exists(os.path.join(target_grub_dir, "i386-pc"))

        if is_efi:
            emit_log("Target system has UEFI bootloader modules. Running EFI grub-install...")
            grub_cmd = ["chroot", target_mnt, "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--no-nvram", "--removable"]
        elif is_bios:
            emit_log(f"Target system has legacy BIOS bootloader modules. Running BIOS grub-install on {target_dev}...")
            grub_cmd = ["chroot", target_mnt, "grub-install", "--target=i386-pc", target_dev]
        else:
            emit_log("WARNING: Could not auto-detect GRUB target platform directory. Defaulting to standard grub-install...")
            grub_cmd = ["chroot", target_mnt, "grub-install", target_dev]

        emit_log("Running update-grub...", prog=98)
        subprocess.check_call(grub_cmd)
        subprocess.check_call(["chroot", target_mnt, "update-grub"])

        # Inject EFI Fallback path
        efi_base = f"{target_mnt}/boot/efi/EFI"
        fallback_dir = f"{efi_base}/BOOT"
        os.makedirs(fallback_dir, exist_ok=True)

        grub_efi_src = None
        for root_dir, dirs, files in os.walk(efi_base):
            for file in files:
                if file.endswith(".efi") and "BOOT" not in root_dir:
                    grub_efi_src = os.path.join(root_dir, file)
                    break
            if grub_efi_src:
                break

        if grub_efi_src:
            emit_log(f"Copying EFI fallback loader: {grub_efi_src} -> {fallback_dir}/BOOTX64.EFI")
            shutil.copy2(grub_efi_src, f"{fallback_dir}/BOOTX64.EFI")

        emit_log("Masking live-config systemd generators...")
        generators_dir = f"{target_mnt}/etc/systemd/system-generators"
        os.makedirs(generators_dir, exist_ok=True)
        try:
            target_link = os.path.join(generators_dir, "live-config-getty-generator")
            if os.path.lexists(target_link):
                os.remove(target_link)
            os.symlink("/dev/null", target_link)
        except Exception as e:
            emit_log(f"WARNING: Failed to mask live-config generator: {str(e)}")

        # 10. Post-Restore verification audit
        emit_log("Starting post-restore audit...")
        with open(f"{target_mnt}/etc/fstab", "r") as f:
            fstab_content = f.read()

        for part in partitions:
            mount = part["mount"]
            label = part["label"]
            uuid = part["uuid"]

            if mount == "/boot/efi":
                efi_check_uuid = efi_uuid or uuid
                if f"UUID={efi_check_uuid}" not in fstab_content:
                    raise ValueError(f"Post-restore verification audit failed: /etc/fstab is missing EFI 'UUID={efi_check_uuid}' mapping.")
            else:
                expected_target = f"LABEL={label}" if label else f"UUID={uuid}"
                if expected_target not in fstab_content:
                    raise ValueError(f"Post-restore verification audit failed: /etc/fstab is missing '{expected_target}' mapping.")

        emit_log("Post-restore verification audit passed.")

        # Unmount virtual filesystems
        emit_log("Unmounting virtual filesystems...")
        subprocess.check_call(["umount", "-R", target_mnt])

        emit_log("Restore completed successfully! Target device ready to boot.", prog=100, status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        emit_log(error_msg, status="FAILED")
        try:
            subprocess.run(["umount", "-R", "/mnt/target"], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        return {"status": "FAILED", "error": str(e)}
