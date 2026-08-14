"""Block-device work for a bare-metal restore: partition, format, mount, extract.

This is the half of a restore that can destroy data. Everything here writes to
raw devices — wipefs, parted, mkfs — on a machine that is also running the
orchestrator, or on a technician's kiosk plugged into a customer's server. Get
the target device wrong and there is nothing to recover from.

Configuring the restored operating system afterwards — network, fstab,
bootloader, licensing — lives in `guest_config.py`. `format_and_restore` below
is the orchestrator of both halves and the only entry point callers use.

**This file ships inside the offline kiosk ISO** (`iso_tasks.py` copies it into
the payload), so it must import nothing from the backend: no database, no
models, no config. Standard library and its sibling `guest_config` only. Adding
another `core.` import means adding that file to the ISO injection list too, or
the kiosk's restore silently stops working — the payload client swallows the
ImportError. `tests/test_iso_payload_injection.py` fails if you forget.
"""
import errno
import os
import re
import shutil
import subprocess
import time
from typing import Dict, Any, List, Callable, Optional
import pathlib

from core import guest_config

#: The device naming schemes this fleet actually boots from, each mapping a
#: partition path to the whole disk it lives on. Written as explicit patterns
#: rather than "strip the trailing digits", because that heuristic turns
#: mmcblk0p1 into mmcblkp — a name no device has, which is how a safety check
#: that compares disk names comes to permit the disk it should have refused.
_PARTITION_PATTERNS = (
    re.compile(r"^(/dev/nvme\d+n\d+)(?:p\d+)?$"),
    re.compile(r"^(/dev/mmcblk\d+)(?:p\d+)?$"),
    re.compile(r"^(/dev/[a-z]+)\d*$"),          # sda1, vdb2, hdc3
)


def base_disk(device: str) -> str:
    """The whole disk a device path refers to: nvme0n1p2 becomes nvme0n1.

    Already a whole disk? Returned unchanged. Unrecognised? Returned unchanged
    too, so a caller comparing two of these gets a mismatch rather than a
    confident wrong answer.
    """
    if not device:
        return device
    for pattern in _PARTITION_PATTERNS:
        match = pattern.match(device)
        if match:
            return match.group(1)
    return device


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
                        return base_disk(os.path.realpath(dev_path))
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


def mount_virtual_filesystems(target_mnt: str) -> None:
    """Bind the host's /dev, /dev/pts, /proc and /sys into the target.

    grub-install and apt run in a chroot and need all four. Paired with
    `safe_unmount_target`, which takes them down again in the order that does
    not leak them back onto the host.
    """
    subprocess.check_call(["mount", "--bind", "/dev", f"{target_mnt}/dev"])
    subprocess.check_call(["mount", "--bind", "/dev/pts", f"{target_mnt}/dev/pts"])
    subprocess.check_call(["mount", "--bind", "/proc", f"{target_mnt}/proc"])
    subprocess.check_call(["mount", "--bind", "/sys", f"{target_mnt}/sys"])


def safe_unmount_target(target_mnt: str, log_callback: Optional[Callable[[str], None]] = None) -> None:
    """
    Safely unmounts all virtual filesystems (/dev/pts, /dev, /proc, /sys) and target partitions
    under target_mnt, then unmounts target_mnt itself.
    Avoids using 'umount -R' which propagates recursive unmounts back to the host in privileged containers.

    The three stages run in this order because each one can only succeed once
    the previous has finished, and getting it wrong does not fail loudly — it
    leaves the host holding mounts.

    1. **Virtual filesystems first, deepest first.** These were bind-mounted
       from the host so grub-install could run in a chroot. /dev/pts is nested
       inside /dev, so unmounting /dev first leaves /dev/pts orphaned and
       still attached to the host's devtmpfs.
    2. **Then the target's own partitions, deepest first.** /boot/efi lives
       under /boot lives under the root; a parent cannot be unmounted while a
       child is mounted on it.
    3. **Then the mountpoint itself**, which by now has nothing under it.

    Every unmount is lazy (-l) and every failure is swallowed. Both are
    deliberate: this runs in the teardown path of a restore that may already
    have failed, and a busy mountpoint here must not mask the error the
    caller is trying to report. `umount -R` would do all three stages in one
    call, but inside a privileged container it propagates back through the
    shared mount namespace and unmounts the host's own /dev.
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


def _guard_host_root_disk(target_dev: str, emit_log: Callable[..., None]) -> None:
    """Refuse to flash the disk this process is running from.

    Two ways to identify it, because neither works everywhere: /proc/cmdline is
    authoritative inside a container, where findmnt reports the overlay; findmnt
    covers the cases where the kernel command line names something we cannot
    resolve to a device.
    """
    host_root_disk = get_host_root_disk()
    if not host_root_disk:
        try:
            findmnt_out = subprocess.check_output("findmnt -n -o SOURCE /", shell=True, text=True).strip()
            if findmnt_out and findmnt_out != "overlay":
                host_root_disk = findmnt_out
        except Exception:
            pass

    # Compared as whole disks, not as substrings. The previous check
    # stripped the digits out of the name and asked whether the result
    # appeared anywhere in the target, which is wrong in both directions:
    # "sda" occurs inside "sdaa" (blocks a restore that was fine), and an
    # eMMC root mangled into "mmcblkp" occurs inside nothing at all
    # (permits the one that wipes the orchestrator).
    if host_root_disk:
        host_root_disk_base = base_disk(host_root_disk)
        if base_disk(target_dev) == host_root_disk_base:
            raise PermissionError(f"PROTECTION SHIELD: Attempted to flash the host's root drive ({host_root_disk_base}). Blocked.")
    else:
        # Not fatal, and deliberately so: on the technician kiosk the "host
        # root" is the USB stick it booted from, /proc/cmdline names a loop
        # device, and flashing the machine's internal disk is the entire
        # purpose. Refusing here would break the product to protect a
        # machine that was never at risk. Say so loudly instead.
        emit_log(
            "WARNING: could not determine the host's root disk, so the "
            "protection shield is not active for this restore."
        )


def _release_device_locks(target_dev: str, emit_log: Callable[..., None]) -> None:
    """Detach anything holding the target open, so wipefs and parted can work.

    The target may be mounted, or carry an LVM volume group or a LUKS container
    that the host's udev helpfully activated the moment the disk appeared. Those
    device-mapper devices keep the partitions busy, and the kernel will not
    reread the partition table underneath them.

    Holders are discovered by walking /sys, not by parsing lsblk, and the walk
    is a queue rather than one pass because the stack nests: LVM on LUKS on a
    partition is three levels, and removing the bottom one first fails.
    """
    try:
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
            # A holder is known to /sys as dm-3 but to dmsetup and /proc/mounts
            # as its friendly name (vg0-root). Both spellings are needed below.
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
            # Anchored on both ends: an unanchored match on /dev/sdb would also
            # catch /dev/sdbb, a different disk that is very likely in use.
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

        # Retried because removal is bottom-up in the dependency graph and this
        # loop is not: a mapping whose child is still present refuses to go, and
        # succeeds on the pass after the child does.
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


def _partition_disk(target_dev: str, partitions: List[Dict[str, Any]], emit_log: Callable[..., None]) -> None:
    """Lay down a fresh GPT matching the layout captured from the source node."""
    emit_log("Creating GPT partitions...", prog=15)
    subprocess.check_call(["parted", "-s", target_dev, "mklabel", "gpt"])

    current_offset = 1 # Start at 1MiB for alignment
    for i, part in enumerate(partitions):
        start_offset = f"{current_offset}MiB"
        if i == len(partitions) - 1:
            # The last partition takes whatever is left, so a restore onto a
            # larger disk uses it rather than stranding the extra space.
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

    # Lets firmware that only understands MBR boot a GPT disk. Harmless on UEFI,
    # and the alternative is a machine that shows no boot device at all.
    try:
        emit_log(f"Setting pmbr_boot flag to 'on' on device {target_dev}...")
        subprocess.check_call(["parted", "-s", target_dev, "disk_set", "pmbr_boot", "on"])
    except Exception as e:
        emit_log(f"WARNING: Failed to set pmbr_boot flag: {str(e)}")

    # Restore original PARTUUIDs where the source recorded them: the archived
    # system may reference them in its own fstab or bootloader config, which we
    # rewrite for the partitions we know about but not for ones we do not.
    for i, part in enumerate(partitions):
        partuuid = part.get("partuuid")
        if partuuid:
            try:
                emit_log(f"Restoring PARTUUID {partuuid} for partition {i+1}...")
                subprocess.check_call(["sfdisk", "--part-uuid", target_dev, str(i+1), partuuid])
            except Exception as e:
                emit_log(f"WARNING: Failed to restore PARTUUID for partition {i+1}: {str(e)}")


def _format_partitions(
    target_dev: str,
    partitions: List[Dict[str, Any]],
    efi_uuid: str,
    emit_log: Callable[..., None],
) -> Dict[str, str]:
    """Create a filesystem on each partition. Returns {mountpoint: device}.

    Labels and UUIDs are carried over from the source wherever the layout
    recorded them, so that anything on the restored system still referring to
    them by name — an fstab line we did not generate, a mount unit — keeps
    working.
    """
    # The partition table only just changed; without both of these the device
    # nodes below may not exist yet, and the mkfs fails on a path rather than a
    # disk.
    try:
        subprocess.run(["partprobe", target_dev], stderr=subprocess.DEVNULL)
    except Exception:
        pass
    subprocess.check_call(["udevadm", "settle"])

    part_devices = {}
    for i, part in enumerate(partitions):
        # nvme0n1 + p1; sdb + 1. Two spellings, one rule.
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
            # vfat has no UUID, only a 32-bit volume serial written without
            # separators. The fallback is an arbitrary constant so the ESP is
            # still addressable when the source recorded nothing.
            clean_efi_uuid = (uuid or efi_uuid or "458C-37BB").replace("-", "")[:8]
            cmd = ["mkfs.vfat", "-F32", "-i", clean_efi_uuid, "-n", label, part_dev]
        elif fstype == "ext2":
            cmd = ["mkfs.ext2", "-F", "-L", label]
            if uuid:
                cmd += ["-U", uuid]
            cmd.append(part_dev)
        elif fstype == "ext4":
            # lazy init defers zeroing the inode tables to first mount, which
            # takes minutes off a restore on spinning disks. ^orphan_file drops
            # a feature that older kernels — including ones on the nodes being
            # restored — refuse to mount.
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

    return part_devices


def _mount_partitions(
    target_mnt: str,
    partitions: List[Dict[str, Any]],
    part_devices: Dict[str, str],
    emit_log: Callable[..., None],
) -> None:
    """Mount the new filesystems under target_mnt, parents before children."""
    if os.path.exists(target_mnt):
        safe_unmount_target(target_mnt)
        shutil.rmtree(target_mnt, ignore_errors=True)

    os.makedirs(target_mnt, exist_ok=True)

    # Sorted by path depth: /boot/efi cannot be mounted before /boot, which
    # cannot be mounted before /. The layout does not arrive in that order.
    mount_ordered_partitions = sorted(partitions, key=lambda x: len(pathlib.PurePosixPath(x["mount"]).parts))

    for part in mount_ordered_partitions:
        mount_path = part["mount"]
        part_dev = part_devices[mount_path]

        target_path = target_mnt if mount_path == "/" else f"{target_mnt}{mount_path}"
        os.makedirs(target_path, exist_ok=True)

        emit_log(f"Mounting partition {part_dev} to {target_path}...", prog=42)
        subprocess.check_call(["mount", part_dev, target_path])


def _prepare_exclusions(
    target_mnt: str,
    exclusions: Optional[List[Any]],
    emit_log: Callable[..., None],
) -> List[str]:
    """Normalise the exclusion patterns, and force the Sentinel dirs into them.

    /var/hasplm and /etc/hasplm hold the licence runtime's machine-specific
    state. Restoring the source machine's copy leaves a fingerprint that does
    not match the new hardware, and the node comes up unlicensed with nothing
    obviously wrong — so they are excluded from the extract whatever the
    caller asked for, and any leftovers on the target are removed as well.
    """
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

    for hasp_dir in ["var/hasplm", "etc/hasplm"]:
        hasp_pattern = f"{hasp_dir}/*"
        if hasp_pattern not in parsed_exclusions:
            parsed_exclusions.append(hasp_pattern)

        target_path = os.path.join(target_mnt, hasp_dir)
        if os.path.exists(target_path):
            emit_log(f"Wiping legacy Sentinel LDK directory: {target_path}")
            shutil.rmtree(target_path, ignore_errors=True)

    return parsed_exclusions


def _extract_archive(
    target_mnt: str,
    repo_path: str,
    archive_name: str,
    parsed_exclusions: List[str],
    total_files: int,
    emit_log: Callable[..., None],
) -> None:
    """Extract the borg archive into the mounted target, reporting progress.

    The progress reporting is why this is not three lines. borg writes its
    progress to stderr as carriage-return-terminated lines and only does so at
    all when it believes it is talking to a terminal — hence the pty. Without
    it the restore shows nothing for however long the extract takes, which on a
    full system image is long enough that operators assume it has hung.

    Falls back to a plain pipe where openpty is unavailable, and then simply
    reports less.
    """
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    env["PYTHONUNBUFFERED"] = "1"
    # The repo is reached by a different path here than when it was written
    # (kiosk vs orchestrator), which borg treats as suspicious by default.
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"

    if repo_path.startswith("ssh://"):
        kiosk_key = "/opt/offline-client/backend/id_ed25519"
        host_key = "/root/.ssh/id_ed25519"
        key_path = kiosk_key if os.path.exists(kiosk_key) else host_key
        if os.path.exists(key_path):
            env["BORG_RSH"] = f"ssh -i {key_path} -o StrictHostKeyChecking=no"

    extract_cmd = [
        # stdbuf -e0 so stderr is unbuffered even in the no-pty case.
        "stdbuf", "-e0",
        # --numeric-ids because the target's /etc/passwd is inside the archive
        # we are extracting; resolving names against the kiosk's would assign
        # every file to the wrong owner.
        "borg", "extract", "--numeric-ids", "--sparse", "--progress"
    ]
    for pat in parsed_exclusions:
        extract_cmd.extend(["--exclude", pat])
    extract_cmd.append(f"{repo_path}::{archive_name}")

    try:
        import pty
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
        # Read a character at a time: borg overwrites its progress line with \r
        # rather than emitting \n, so readline() would block until the extract
        # finished and deliver everything at once.
        while True:
            try:
                char = stderr_stream.read(1)
            except OSError as e:
                # EIO on a pty master is how the child closing the slave
                # presents itself. Normal end of stream, not a failure.
                if has_pty and e.errno == errno.EIO:
                    break
                raise
            if not char:
                break
            if char == '\r' or char == '\n':
                line = buffer.strip()
                buffer = ""
                # borg's progress line is "<size> <unit> <count> N <path>".
                # Locate the count by the literal N rather than by position,
                # because the size field's width varies.
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
                        # Extraction owns 45-90% of the restore's progress bar.
                        pct = int((curr_files / total_files) * 45)
                        progress_val = 45 + pct
                        if progress_val > last_logged_prog or curr_files - last_logged_files >= 1000:
                            emit_log(f"Extracting files ({curr_files}/{total_files})...", prog=progress_val)
                            last_logged_prog = progress_val
                            last_logged_files = curr_files
                    else:
                        # No file count recorded for this archive, so there is
                        # no percentage to compute — report movement only.
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

    The steps below are ordered by dependency, not by preference — each one
    needs the previous to have happened. Failure at any point unwinds the
    mounts and returns FAILED rather than raising: the caller is a Celery task
    or the kiosk UI, and both want a status to display, not a traceback.
    """

    def emit_log(msg: str, prog: Optional[int] = None, status: Optional[str] = None):
        log_callback(msg, prog, status)

    emit_log(f"Initializing flashing process on target device: {target_dev}", prog=5)

    target_mnt = "/mnt/target"

    try:
        if not os.path.exists(target_dev):
            raise FileNotFoundError(f"Target device {target_dev} does not exist.")

        _guard_host_root_disk(target_dev, emit_log)
        _release_device_locks(target_dev, emit_log)

        emit_log(f"Wiping signatures on {target_dev}...", prog=10)
        subprocess.check_call(["wipefs", "-a", target_dev])

        _partition_disk(target_dev, partitions, emit_log)
        part_devices = _format_partitions(target_dev, partitions, efi_uuid, emit_log)
        _mount_partitions(target_mnt, partitions, part_devices, emit_log)

        parsed_exclusions = _prepare_exclusions(target_mnt, exclusions, emit_log)

        emit_log(f"Extracting archive {archive_name} into {target_mnt}...", prog=45)
        _extract_archive(target_mnt, repo_path, archive_name, parsed_exclusions, total_files, emit_log)

        # From here the target is a filesystem tree, not a disk: see
        # guest_config.py.
        guest_config.configure_network(
            target_mnt,
            keep_network_configs=keep_network_configs,
            wipe_mac_bindings=wipe_mac_bindings,
            network_iface=network_iface,
            emit_log=emit_log,
        )
        guest_config.write_fstab(target_mnt, partitions, efi_uuid, emit_log)

        emit_log("Mounting virtual filesystems...", prog=94)
        mount_virtual_filesystems(target_mnt)

        guest_config.install_bootloader(target_mnt, target_dev, emit_log)
        guest_config.mask_live_config_generators(target_mnt, emit_log)
        guest_config.clean_postgres_state(target_mnt, emit_log)
        guest_config.reinitialise_sentinel_ldk(target_mnt, repo_path, orchestrator_ip, emit_log)

        guest_config.audit_fstab(target_mnt, partitions, efi_uuid, emit_log)
        guest_config.install_checkin_service(target_mnt, orchestrator_ip, available_server_ips, emit_log)

        emit_log("Unmounting virtual filesystems...")
        safe_unmount_target(target_mnt, log_callback=emit_log)

        emit_log("Restore completed successfully! Target device ready to boot.", prog=100, status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        error_msg = f"Restore execution failed: {str(e)}"
        emit_log(error_msg, status="FAILED")
        try:
            safe_unmount_target(target_mnt)
        except Exception:
            pass
        return {"status": "FAILED", "error": str(e)}
