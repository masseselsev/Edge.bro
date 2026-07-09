import os
import shutil
import subprocess
import json
from typing import Dict, Any, List, Callable, Optional
import pathlib

def get_host_root_disk() -> Optional[str]:
    """
    Parses the host kernel command line at /proc/cmdline to detect the host's actual root disk.
    This is extremely reliable inside Docker container environments where findmnt returns 'overlay'.
    """
    if os.path.exists("/proc/cmdline"):
        try:
            with open("/proc/cmdline", "r") as f:
                content = f.read()
            for arg in content.split():
                if arg.startswith("root="):
                    root_val = arg.split("=", 1)[1]
                    dev_path = None
                    if root_val.startswith("UUID="):
                        uuid = root_val.split("=", 1)[1].strip('"\'')
                        dev_path = f"/dev/disk/by-uuid/{uuid}"
                    elif root_val.startswith("PARTUUID="):
                        partuuid = root_val.split("=", 1)[1].strip('"\'')
                        dev_path = f"/dev/disk/by-partuuid/{partuuid}"
                    elif root_val.startswith("LABEL="):
                        label = root_val.split("=", 1)[1].strip('"\'')
                        dev_path = f"/dev/disk/by-label/{label}"
                    elif root_val.startswith("/dev/"):
                        dev_path = root_val.strip('"\'')

                    if dev_path and os.path.exists(dev_path):
                        real_path = os.path.realpath(dev_path)
                        # Remove partition suffix (e.g. /dev/sda1 -> /dev/sda, /dev/nvme0n1p2 -> /dev/nvme0n1)
                        import re
                        m = re.match(r"^(/dev/nvme\d+n\d+)p\d+$", real_path)
                        if m:
                            return m.group(1)
                        m_sd = re.match(r"^(/dev/sd[a-z]+)\d+$", real_path)
                        if m_sd:
                            return m_sd.group(1)
                        # Fallback: remove trailing digits
                        return real_path.rstrip("0123456789")
        except Exception:
            pass
    return None

def run_format_command_with_retry(
    cmd: List[str],
    part_dev: str,
    emit_log: Callable[[str, Optional[int], Optional[str]], None],
    max_retries: int = 5,
    delay: float = 1.0
) -> None:
    """
    Runs a partition formatting command, retrying if the device is busy due to
    udev/systemd probing or host automount locks.
    """
    import time
    for attempt in range(1, max_retries + 1):
        # Attempt to release any automount locks before formatting
        try:
            subprocess.run(["umount", part_dev], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            subprocess.run(["umount", "-l", part_dev], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        
        # Run format command and capture output to diagnose failures
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return
        
        stderr_msg = res.stderr.strip() if res.stderr else "Unknown error"
        emit_log(
            f"WARNING: Format attempt {attempt}/{max_retries} failed for {part_dev}: {stderr_msg}. Retrying in {delay}s...",
            None,
            None
        )
        if attempt < max_retries:
            time.sleep(delay)
            
    # Final attempt to run and raise exception with output if all retries failed
    emit_log(f"ERROR: All format attempts failed for {part_dev}. Executing final attempt...", None, None)
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        detailed_err = f"Command {cmd} failed with exit status {e.returncode}. stderr: {e.stderr.strip()}"
        emit_log(f"CRITICAL ERROR: {detailed_err}", None, None)
        raise RuntimeError(detailed_err) from e

def safe_unmount_target(target_mnt: str, log_callback: Optional[Callable[[str], None]] = None) -> None:
    """
    Safely unmounts all virtual filesystems (/dev/pts, /dev, /proc, /sys) and target partitions
    under target_mnt, then unmounts target_mnt itself.
    Avoids using 'umount -R' which propagates recursive unmounts back to the host in privileged containers.
    """
    def emit(msg: str):
        if log_callback:
            log_callback(msg)

    # 1. Unmount virtual filesystems in reverse order of mounting
    virtual_paths = [
        f"{target_mnt}/dev/pts",
        f"{target_mnt}/dev",
        f"{target_mnt}/proc",
        f"{target_mnt}/sys"
    ]
    for path in virtual_paths:
        if os.path.exists(path):
            try:
                # We use lazy unmount (-l) to safely detach the mount point from the namespace tree
                subprocess.run(["umount", "-l", path], stderr=subprocess.DEVNULL)
            except Exception:
                pass

    # 2. Parse /proc/mounts to find and unmount target partitions mounted under target_mnt
    try:
        if os.path.exists("/proc/mounts"):
            submounts = []
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        mnt_point = parts[1]
                        if mnt_point.startswith(target_mnt) and mnt_point != target_mnt:
                            submounts.append(mnt_point)
            
            # Sort from deepest path to shallowest path
            submounts.sort(key=lambda x: len(pathlib.PurePosixPath(x).parts), reverse=True)
            for mnt in submounts:
                subprocess.run(["umount", "-l", mnt], stderr=subprocess.DEVNULL)
    except Exception as e:
        emit(f"Warning during nested mounts cleanup: {str(e)}")

    # 3. Finally, unmount target_mnt itself
    try:
        subprocess.run(["umount", "-l", target_mnt], stderr=subprocess.DEVNULL)
    except Exception:
        pass

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
    log_callback: Callable[[str, Optional[int], Optional[str]], None],
    exclusions: Optional[List[Any]] = None,
    orchestrator_ip: Optional[str] = None,
    available_server_ips: Optional[str] = None
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
        host_root_disk = get_host_root_disk()
        if not host_root_disk:
            try:
                findmnt_out = subprocess.check_output("findmnt -n -o SOURCE /", shell=True, text=True).strip()
                if findmnt_out and findmnt_out != "overlay":
                    host_root_disk = findmnt_out
            except Exception:
                pass

        if host_root_disk:
            if "nvme" in host_root_disk:
                host_root_disk_base = host_root_disk.split("p")[0]
            else:
                host_root_disk_base = "".join([c for c in host_root_disk if not c.isdigit()])

            if host_root_disk_base in target_dev:
                raise PermissionError(f"PROTECTION SHIELD: Attempted to flash the host's root drive ({host_root_disk_base}). Blocked.")

        # 1.5. Release active mount and device-mapper (LVM/LUKS) locks on target device & its partitions
        try:
            import re
            import errno
            
            target_name = os.path.basename(target_dev)
            holders = set()
            sys_block_path = f"/sys/block/{target_name}"
            
            if os.path.exists(sys_block_path):
                # Initialize queue with root holders and partition holders directories
                queue = ["holders"]
                for item in os.listdir(sys_block_path):
                    if item.startswith(target_name):
                        queue.append(f"{item}/holders")
                
                # Recursively discover nested device-mapper holders (e.g. LVM on LUKS or vice versa)
                processed = set()
                while queue:
                    sub_path = queue.pop(0)
                    if sub_path in processed:
                        continue
                    processed.add(sub_path)
                    
                    full_h_path = os.path.join(sys_block_path, sub_path) if not sub_path.startswith("/") else sub_path
                    if os.path.exists(full_h_path):
                        for h in os.listdir(full_h_path):
                            if h.startswith("dm-"):
                                holders.add(h)
                                queue.append(f"/sys/block/{h}/holders")
                                
            # Resolve mapper names for each holder
            dm_names = set()
            for holder in holders:
                dm_names.add(holder)
                dm_sys_name_path = f"/sys/block/{holder}/dm/name"
                if os.path.exists(dm_sys_name_path):
                    try:
                        with open(dm_sys_name_path, "r") as fh:
                            dm_name = fh.read().strip()
                            if dm_name:
                                dm_names.add(dm_name)
                    except Exception:
                        pass

            # Unmount target partitions and resolved device-mapper holder devices
            if os.path.exists("/proc/mounts"):
                patterns = [re.escape(target_dev) + r"(p?\d+)?$"]
                for dm_name in dm_names:
                    patterns.append(r"/dev/mapper/" + re.escape(dm_name) + r"$")
                    patterns.append(r"/dev/" + re.escape(dm_name) + r"$")
                
                combined_pattern = re.compile(r"^(" + "|".join(patterns) + r")$")
                
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            dev_src = parts[0]
                            # Match if matches partition pattern or mapper name directly
                            if combined_pattern.match(dev_src) or any(dm in dev_src for dm in dm_names):
                                mount_point = parts[1]
                                emit_log(f"Releasing mount lock: unmounting {dev_src} from {mount_point}...", prog=8)
                                subprocess.call(["umount", "-l", mount_point])

            # Force-remove device-mapper mappings in a retry loop (to resolve hierarchy/dependencies)
            for attempt in range(3):
                still_have_dm = False
                for dm_name in list(dm_names):
                    if os.path.exists(f"/sys/block/{dm_name}") or os.path.exists(f"/dev/mapper/{dm_name}"):
                        still_have_dm = True
                        emit_log(f"Releasing device-mapper lock: removing {dm_name} (attempt {attempt+1})...", prog=9)
                        subprocess.call(["dmsetup", "remove", "-f", dm_name])
                if not still_have_dm:
                    break
                    
        except Exception as ue:
            emit_log(f"Warning: Failed to release mount/device-mapper locks: {str(ue)}")

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
            emit_log(f"Creating partition {i+1} with GPT label/name '{part_name}' ({parted_fs}, {start_offset} to {end_offset})...")
            subprocess.check_call(["parted", "-s", target_dev, "mkpart", part_name, parted_fs, start_offset, end_offset])

            if part.get("mount") == "/boot/efi":
                emit_log(f"Setting EFI System Partition (esp) flag on partition {i+1}...")
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
        try:
            subprocess.run(["partprobe", target_dev], stderr=subprocess.DEVNULL)
        except Exception:
            pass
        subprocess.check_call(["udevadm", "settle"])

        part_devices = {}
        for i, part in enumerate(partitions):
            part_suffix = f"p{i+1}" if "nvme" in target_dev else f"{i+1}"
            part_dev = f"{target_dev}{part_suffix}"
            part_devices[part["mount"]] = part_dev

            fstype = part.get("fstype", "ext4")
            label = part.get("label") or f"part{i+1}"
            uuid = part.get("uuid")

            # Release any active automount locks on this specific partition before formatting
            try:
                subprocess.run(["umount", "-l", part_dev], stderr=subprocess.DEVNULL)
            except Exception:
                pass

            progress_val = 20 + int((i / len(partitions)) * 20)
            emit_log(f"Formatting partition {part_dev} ({part.get('mount')}) as {fstype} with label: {label}...", prog=progress_val)

            if fstype == "vfat":
                clean_efi_uuid = (uuid or efi_uuid or "458C-37BB").replace("-", "")[:8]
                cmd = ["mkfs.vfat", "-F32", "-i", clean_efi_uuid, "-n", label, part_dev]
            elif fstype == "ext2":
                cmd = ["mkfs.ext2", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)
            elif fstype == "ext4":
                cmd = ["mkfs.ext4", "-E", "lazy_itable_init=1,lazy_journal_init=1", "-O", "^orphan_file", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)
            elif fstype == "xfs":
                cmd = ["mkfs.xfs", "-f", "-L", label]
                if uuid:
                    cmd += ["-m", f"uuid={uuid}"]
                cmd.append(part_dev)
            else:
                cmd = ["mkfs.ext4", "-E", "lazy_itable_init=1,lazy_journal_init=1", "-O", "^orphan_file", "-F", "-L", label]
                if uuid:
                    cmd += ["-U", uuid]
                cmd.append(part_dev)

            run_format_command_with_retry(cmd, part_dev, emit_log)

        # 5. Mounting partitions hierarchically
        target_mnt = "/mnt/target"
        if os.path.exists(target_mnt):
            safe_unmount_target(target_mnt)
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

        # Storage Wiping: guarantee Sentinel LDK directories are clean/absent on the target root filesystem
        parsed_exclusions = []
        if exclusions:
            for ex in exclusions:
                pattern = None
                if isinstance(ex, dict):
                    pattern = ex.get("pattern")
                elif isinstance(ex, str):
                    pattern = ex
                if pattern:
                    pat_stripped = pattern.strip().lstrip("/")
                    if pat_stripped:
                        parsed_exclusions.append(pat_stripped)

        # Ensure Sentinel dirs are also in the list if not present, and wipe them
        for hasp_dir in ["var/hasplm", "etc/hasplm"]:
            hasp_pattern = f"{hasp_dir}/*"
            if hasp_pattern not in parsed_exclusions:
                parsed_exclusions.append(hasp_pattern)
            
            target_path = os.path.join(target_mnt, hasp_dir)
            if os.path.exists(target_path):
                emit_log(f"Wiping legacy Sentinel LDK directory: {target_path}")
                shutil.rmtree(target_path, ignore_errors=True)

        # 6. Extract Borg Backup
        emit_log(f"Extracting archive {archive_name} into {target_mnt}...", prog=45)

        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "verysecureborgpassphrase")
        env["PYTHONUNBUFFERED"] = "1"
        env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"

        if repo_path.startswith("ssh://"):
            kiosk_key = "/opt/offline-client/backend/id_ed25519"
            host_key = "/root/.ssh/id_ed25519"
            key_path = kiosk_key if os.path.exists(kiosk_key) else host_key
            if os.path.exists(key_path):
                env["BORG_RSH"] = f"ssh -i {key_path} -o StrictHostKeyChecking=no"

        extract_cmd = [
            "stdbuf", "-e0",
            "borg", "extract", "--numeric-ids", "--sparse", "--progress"
        ]
        for pat in parsed_exclusions:
            extract_cmd.extend(["--exclude", pat])
        extract_cmd.append(f"{repo_path}::{archive_name}")
        
        try:
            import pty
            import errno
            master, slave = pty.openpty()
            stderr_fd = slave
            has_pty = True
        except (ImportError, OSError):
            stderr_fd = subprocess.PIPE
            has_pty = False

        proc = subprocess.Popen(
            extract_cmd, 
            cwd=target_mnt, 
            env=env, 
            stderr=stderr_fd, 
            text=True, 
            bufsize=1
        )

        if has_pty:
            os.close(slave)
            stderr_stream = os.fdopen(master, "r", encoding="utf-8", errors="ignore")
        else:
            stderr_stream = proc.stderr

        buffer = ""
        last_logged_files = -1000
        last_logged_prog = -1
        try:
            while True:
                try:
                    char = stderr_stream.read(1)
                except OSError as e:
                    if has_pty and e.errno == errno.EIO:
                        break
                    raise
                if not char:
                    break
                if char == '\r' or char == '\n':
                    line = buffer.strip()
                    buffer = ""
                    parts = line.split()
                    curr_files = None
                    for idx, part in enumerate(parts):
                        if part == "N" and idx > 0:
                            try:
                                curr_files = int(parts[idx - 1].replace(",", ""))
                                break
                            except ValueError:
                                continue
                    
                    if curr_files is not None:
                        if total_files > 0:
                            pct = int((curr_files / total_files) * 45)
                            progress_val = 45 + pct
                            if progress_val > last_logged_prog or curr_files - last_logged_files >= 1000:
                                emit_log(f"Extracting files ({curr_files}/{total_files})...", prog=progress_val)
                                last_logged_prog = progress_val
                                last_logged_files = curr_files
                        else:
                            if curr_files - last_logged_files >= 1000:
                                emit_log(f"Extracting files ({curr_files})...")
                                last_logged_files = curr_files
                else:
                    buffer += char
            
            if has_pty:
                try:
                    stderr_stream.close()
                except Exception:
                    pass
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
                    
                    # Pre-scan the file to find interfaces configured as static or manual
                    static_or_manual = set()
                    for line in lines:
                        stripped_line = line.strip()
                        if stripped_line.startswith("iface "):
                            parts = stripped_line.split()
                            if len(parts) >= 4:
                                iface_name = parts[1]
                                method = parts[3]
                                if method in ["static", "manual"]:
                                    static_or_manual.add(iface_name)
                    
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
                                
                                auto_ifaces = []
                                hotplug_ifaces = []
                                for iface in non_lo_ifaces:
                                    # Keep aliases (e.g. eno1:1) and static/manual interfaces as 'auto'
                                    if ":" in iface or iface in static_or_manual:
                                        auto_ifaces.append(iface)
                                    else:
                                        hotplug_ifaces.append(iface)
                                
                                if auto_ifaces:
                                    new_parts.append(f"auto {' '.join(auto_ifaces)}")
                                for iface in hotplug_ifaces:
                                    new_parts.append(f"allow-hotplug {iface}")
                                
                                new_lines.append("\n".join(new_parts))
                                modified = True
                            else:
                                new_lines.append(line.rstrip("\r\n"))
                        else:
                            new_lines.append(line.rstrip("\r\n"))
                    if modified:
                        with open(path, "w") as f:
                            f.write("\n".join(new_lines) + "\n")
                        emit_log(f"Successfully patched auto to allow-hotplug in {os.path.basename(path)}")
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
                    f.write("auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n")

                ifaces_to_configure = ["eth0", "enp1s0", "enp2s0", "enp3s0"]
                if network_iface and network_iface not in ifaces_to_configure:
                    ifaces_to_configure.append(network_iface)

                with open(f"{target_mnt}/etc/network/interfaces.d/orchestrator-dhcp", "w") as f:
                    for iface in ifaces_to_configure:
                        f.write(f"allow-hotplug {iface}\niface {iface} inet dhcp\n\n")
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
            f.write("\n".join(fstab_lines) + "\n")
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

        # Remove any leftover PostgreSQL postmaster.pid lock files to prevent startup failure
        try:
            var_path = os.path.join(target_mnt, "var")
            if os.path.exists(var_path):
                for root_dir, dirs, files in os.walk(var_path):
                    if "postmaster.pid" in files:
                        pid_file_path = os.path.join(root_dir, "postmaster.pid")
                        emit_log(f"Removing stale PostgreSQL lock file: {pid_file_path}")
                        try:
                            os.remove(pid_file_path)
                        except Exception as pe:
                            emit_log(f"WARNING: Failed to remove stale PID file {pid_file_path}: {str(pe)}")
        except Exception as pe_scan:
            emit_log(f"WARNING: Failed to scan for stale PostgreSQL lock files: {str(pe_scan)}")

        # Recreate custom PostgreSQL log directories if they point to custom locations (which might be excluded)
        try:
            pg_etc_dir = os.path.join(target_mnt, "etc/postgresql")
            if os.path.exists(pg_etc_dir):
                for version in os.listdir(pg_etc_dir):
                    version_path = os.path.join(pg_etc_dir, version)
                    if os.path.isdir(version_path):
                        for cluster in os.listdir(version_path):
                            cluster_path = os.path.join(version_path, cluster)
                            log_symlink = os.path.join(cluster_path, "log")
                            if os.path.islink(log_symlink):
                                target_log_path = os.readlink(log_symlink)
                                log_dir_in_chroot = os.path.dirname(target_log_path)
                                log_dir_host = os.path.join(target_mnt, log_dir_in_chroot.lstrip("/"))
                                if not os.path.exists(log_dir_host):
                                    emit_log(f"Recreating custom PostgreSQL log directory: {log_dir_in_chroot}")
                                    os.makedirs(log_dir_host, exist_ok=True)
                                    subprocess.run(["chroot", target_mnt, "chown", "postgres:postgres", log_dir_in_chroot], check=True)
                                    subprocess.run(["chroot", target_mnt, "chmod", "775", log_dir_in_chroot], check=True)
        except Exception as pe_log:
            emit_log(f"WARNING: Failed to recreate custom PostgreSQL log directories: {str(pe_log)}")

        # Reset Loop: Purge old config and re-initialize Sentinel LDK Runtime inside target OS
        try:
            dpkg_check = subprocess.run(["chroot", target_mnt, "dpkg-query", "-W", "edge-hasp-eoawt3"], capture_output=True, text=True)
            if dpkg_check.returncode == 0:
                emit_log("Re-initializing Sentinel LDK Runtime inside target chroot...")

                # 1. Check if we have offline packages cached in the repository
                local_pkg_src = os.path.join(repo_path, "packages")
                local_pkg_dst = os.path.join(target_mnt, "tmp", "offline_packages")
                has_local_pkgs = False
                deb_files = []

                if os.path.exists(local_pkg_src):
                    deb_files = [f for f in os.listdir(local_pkg_src) if f.endswith(".deb")]
                    if deb_files:
                        emit_log("Found offline Sentinel packages in repository cache. Preparing offline installation...")
                        try:
                            shutil.rmtree(local_pkg_dst, ignore_errors=True)
                            shutil.copytree(local_pkg_src, local_pkg_dst)
                            has_local_pkgs = True
                        except Exception as copy_err:
                            emit_log(f"WARNING: Failed to copy offline packages to target: {copy_err}")

                if has_local_pkgs:
                    # Offline local installation
                    deb_paths_in_chroot = [f"/tmp/offline_packages/{f}" for f in deb_files]
                    emit_log(f"Installing offline packages: {', '.join(deb_files)}...")
                    
                    # Run Install
                    p_install = subprocess.run(
                        ["chroot", target_mnt, "apt-get", "install", "-y"] + deb_paths_in_chroot,
                        capture_output=True,
                        text=True
                    )
                    if p_install.returncode != 0:
                        emit_log(f"WARNING: local apt-get install failed (exit code {p_install.returncode}), trying dpkg: {p_install.stderr.strip()}")
                        p_dpkg = subprocess.run(
                            ["chroot", target_mnt, "dpkg", "-i"] + deb_paths_in_chroot,
                            capture_output=True,
                            text=True
                        )
                        if p_dpkg.returncode != 0:
                            emit_log(f"WARNING: local dpkg install failed: {p_dpkg.stderr.strip()}")
                        else:
                            emit_log("Sentinel LDK Runtime successfully re-initialized (offline via dpkg).")
                    else:
                        emit_log("Sentinel LDK Runtime successfully re-initialized (offline via apt-get).")

                    # Clean up copied files inside target
                    shutil.rmtree(local_pkg_dst, ignore_errors=True)
                else:
                    # Online fallback: Setup temporary DNS inside chroot so apt-get update can resolve repo domains
                    resolv_conf_path = os.path.join(target_mnt, "etc", "resolv.conf")
                    backup_resolv_conf = None
                    if os.path.exists(resolv_conf_path) or os.path.islink(resolv_conf_path):
                        try:
                            if os.path.islink(resolv_conf_path):
                                backup_resolv_conf = ("symlink", os.readlink(resolv_conf_path))
                            else:
                                with open(resolv_conf_path, "rb") as f:
                                    backup_resolv_conf = ("file", f.read())
                            os.remove(resolv_conf_path)
                        except Exception as re_err:
                            emit_log(f"WARNING: Failed to backup target resolv.conf: {re_err}")

                    try:
                        if os.path.exists("/etc/resolv.conf"):
                            shutil.copy2("/etc/resolv.conf", resolv_conf_path)
                    except Exception as cp_err:
                        emit_log(f"WARNING: Failed to copy host resolv.conf to chroot: {cp_err}")

                    # Configure APT Proxy
                    apt_proxy_conf = os.path.join(target_mnt, "etc", "apt", "apt.conf.d", "proxy.conf")
                    if orchestrator_ip:
                        emit_log(f"Configuring target APT proxy to use orchestrator at {orchestrator_ip}:3142...")
                        try:
                            os.makedirs(os.path.dirname(apt_proxy_conf), exist_ok=True)
                            with open(apt_proxy_conf, "w") as f:
                                f.write(f'Acquire::http::Proxy "http://{orchestrator_ip}:3142/";\n')
                                f.write(f'Acquire::https::Proxy "http://{orchestrator_ip}:3142/";\n')
                        except Exception as proxy_err:
                            emit_log(f"WARNING: Failed to write proxy config: {proxy_err}")
                    else:
                        if os.path.exists(apt_proxy_conf):
                            try:
                                os.remove(apt_proxy_conf)
                                emit_log("Removed stale target APT proxy config.")
                            except Exception:
                                pass

                    # Run Purge
                    emit_log("Purging old configuration of edge-hasp-eoawt3...")
                    p_purge = subprocess.run(["chroot", target_mnt, "apt-get", "purge", "-y", "edge-hasp-eoawt3"], capture_output=True, text=True)
                    if p_purge.returncode != 0:
                        emit_log(f"WARNING: apt-get purge failed (exit code {p_purge.returncode}): {p_purge.stderr.strip()}")

                    # Run Update
                    emit_log("Updating package cache...")
                    p_update = subprocess.run(["chroot", target_mnt, "apt-get", "update"], capture_output=True, text=True)
                    if p_update.returncode != 0:
                        emit_log(f"WARNING: apt-get update failed (exit code {p_update.returncode}): {p_update.stderr.strip()}")

                    # Run Install
                    emit_log("Reinstalling edge-hasp-eoawt3...")
                    p_install = subprocess.run(["chroot", target_mnt, "apt-get", "install", "-y", "edge-hasp-eoawt3"], capture_output=True, text=True)
                    if p_install.returncode != 0:
                        emit_log(f"WARNING: apt-get install failed (exit code {p_install.returncode}): {p_install.stderr.strip()}")
                    else:
                        emit_log("Sentinel LDK Runtime successfully re-initialized.")

                    # Restore original resolv.conf
                    try:
                        if os.path.exists(resolv_conf_path) or os.path.islink(resolv_conf_path):
                            os.remove(resolv_conf_path)
                        if backup_resolv_conf:
                            if backup_resolv_conf[0] == "symlink":
                                os.symlink(backup_resolv_conf[1], resolv_conf_path)
                            else:
                                with open(resolv_conf_path, "wb") as f:
                                    f.write(backup_resolv_conf[1])
                    except Exception as rest_err:
                        emit_log(f"WARNING: Failed to restore target resolv.conf: {rest_err}")
        except Exception as ae:
            emit_log(f"WARNING: Failed to reinstall/re-initialize edge-hasp-eoawt3: {str(ae)}")

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

        # Setup one-time startup checkin script on restored node
        targets = []
        if available_server_ips:
            targets = [ip.strip() for ip in available_server_ips.split(",") if ip.strip()]
        if orchestrator_ip and orchestrator_ip not in targets:
            targets.append(orchestrator_ip)

        if targets:
            emit_log("Configuring one-time post-restore checkin script on target system...")
            try:
                targets_bash = " ".join(f'"{ip}"' for ip in targets)
                checkin_sh_path = os.path.join(target_mnt, "usr", "local", "bin", "edge-restore-checkin.sh")
                checkin_sh_content = (
                    "#!/bin/bash\n"
                    f"SERVER_IPS=({targets_bash})\n\n"
                    "for ip in \"${SERVER_IPS[@]}\"; do\n"
                    "    if ping -c 1 -W 1 \"$ip\" >/dev/null 2>&1; then\n"
                    "        HOSTNAME=$(hostname)\n"
                    "        IP_ADDR=$(ip route get \"$ip\" | awk '{{print $7; exit}}')\n\n"
                    "        res=$(curl -s -X POST -H \"Content-Type: application/json\" \\\n"
                    "                   -d \"{\\\"hostname\\\": \\\"$HOSTNAME\\\", \\\"ip_address\\\": \\\"$IP_ADDR\\\"}\" \\\n"
                    "                   \"http://$ip/api/nodes/checkin-restored\")\n\n"
                    "        if [[ \"$res\" == *\"success\"* ]]; then\n"
                    "            systemctl disable edge-restore-checkin.service\n"
                    "            rm -f /etc/systemd/system/edge-restore-checkin.service\n"
                    "            rm -f /usr/local/bin/edge-restore-checkin.sh\n"
                    "            exit 0\n"
                    "        fi\n"
                    "    fi\n"
                    "done\n"
                )
                os.makedirs(os.path.dirname(checkin_sh_path), exist_ok=True)
                with open(checkin_sh_path, "w") as f:
                    f.write(checkin_sh_content)
                os.chmod(checkin_sh_path, 0o755)

                checkin_service_path = os.path.join(target_mnt, "etc", "systemd", "system", "edge-restore-checkin.service")
                checkin_service_content = """[Unit]
Description=One-time Post-Restore Orchestrator Checkin
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/edge-restore-checkin.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
                with open(checkin_service_path, "w") as f:
                    f.write(checkin_service_content)

                # Enable the service inside the chroot
                subprocess.run(["chroot", target_mnt, "systemctl", "enable", "edge-restore-checkin.service"], capture_output=True)
                emit_log("One-time post-restore checkin service successfully installed.")
            except Exception as checkin_err:
                emit_log(f"WARNING: Failed to configure post-restore checkin script: {checkin_err}")

        # Unmount virtual filesystems
        emit_log("Unmounting virtual filesystems...")
        safe_unmount_target(target_mnt, log_callback=emit_log)

        emit_log("Restore completed successfully! Target device ready to boot.", prog=100, status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        emit_log(error_msg, status="FAILED")
        try:
            safe_unmount_target("/mnt/target")
        except Exception:
            pass
        return {"status": "FAILED", "error": str(e)}
