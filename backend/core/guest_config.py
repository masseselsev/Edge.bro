"""Configuring the restored operating system, once its filesystems are mounted.

`disk_ops.py` deals with block devices: it partitions the target, formats it,
mounts it at /mnt/target and extracts the archive into it. Everything after
that point is a different job — editing files inside somebody else's root
filesystem and running commands in a chroot — and that is what lives here.

The two halves fail in different ways, which is the reason for the seam. A
mistake in `disk_ops` destroys data on a disk. A mistake here produces a
machine that boots wrong, or does not boot: a bad fstab, a missing bootloader,
an interface that never comes up. Neither set of hazards helps you reason about
the other.

Every function takes the target mountpoint and an `emit_log(msg, prog, status)`
callback, and none of them opens a database session or knows what a Node is —
this module ships inside the offline kiosk ISO, where none of that exists.

**Precondition for every function here:** the target's partitions are mounted
under `target_mnt`. `install_bootloader` additionally requires the host's
virtual filesystems to be bind-mounted into it (see
`disk_ops.mount_virtual_filesystems`), because grub-install runs in a chroot.
"""
import json
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

#: `emit_log(message, progress, status)`. Progress and status are optional;
#: passing neither logs a line without moving the bar.
EmitLog = Callable[..., None]


def configure_network(
    target_mnt: str,
    *,
    keep_network_configs: bool,
    wipe_mac_bindings: bool,
    network_iface: str,
    emit_log: EmitLog,
) -> None:
    """Make the restored system's network come up on hardware it has never seen.

    Two mutually exclusive strategies, chosen by `keep_network_configs`:

    **Keep (the default).** The restored config is correct for this site — the
    static addresses, the VLANs, the aliases are all wanted. The only thing
    wrong with it is that it names interfaces that may not exist on the new
    machine, and Debian's `auto` stanza is fatal about that: ifupdown blocks at
    boot waiting for an interface that will never appear. Rewriting `auto` to
    `allow-hotplug` keeps the same addresses while letting a missing interface
    be merely absent instead of fatal.

    **Replace.** Throw the config away and DHCP on anything that looks like
    ethernet. Used when the backup came from different hardware entirely.
    """
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

        # Pre-scan every file before editing any of them: an alias (eno1:1) may
        # be declared in a different file from its parent (eno1), and demoting
        # the parent to allow-hotplug would strand the alias. Deciding
        # file-by-file cannot see that.
        static_or_manual = set()
        alias_parents = set()
        for path in paths_to_patch:
            try:
                with open(path, "r") as f:
                    for line in f:
                        stripped_line = line.strip()
                        if stripped_line.startswith("iface "):
                            parts = stripped_line.split()
                            if len(parts) >= 4:
                                iface_name = parts[1]
                                method = parts[3]
                                if method in ["static", "manual"]:
                                    static_or_manual.add(iface_name)
                                if ":" in iface_name:
                                    alias_parents.add(iface_name.split(":")[0])
            except Exception as e:
                emit_log(f"WARNING: Failed to pre-scan network file {path}: {str(e)}")

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

                            auto_ifaces = []
                            hotplug_ifaces = []
                            for iface in non_lo_ifaces:
                                # Three cases stay `auto`, because allow-hotplug
                                # would break them: an alias is not a device the
                                # kernel ever hotplugs, a parent demoted to
                                # hotplug takes its aliases down with it, and a
                                # static address is the thing the operator
                                # configured deliberately.
                                if ":" in iface or iface in alias_parents or iface in static_or_manual:
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

        # The login banner prints the node's address, which is not known until
        # DHCP hands one out — and can change on any later renewal. Regenerating
        # it from a dhclient hook keeps the console honest without a polling
        # service.
        dhcp_hook_dir = f"{target_mnt}/etc/dhcp/dhclient-exit-hooks.d"
        if os.path.exists(f"{target_mnt}/etc/dhcp") or os.path.exists(f"{target_mnt}/etc"):
            os.makedirs(dhcp_hook_dir, exist_ok=True)
            hook_path = os.path.join(dhcp_hook_dir, "edge-banner")
            try:
                with open(hook_path, "w") as f:
                    f.write(
                        "#!/bin/sh\n"
                        "if [ \"$reason\" = \"BOUND\" ] || [ \"$reason\" = \"RENEW\" ] || [ \"$reason\" = \"REBIND\" ] || [ \"$reason\" = \"REBOOT\" ]; then\n"
                        "    if [ -x /opt/edge/bin/banner ]; then\n"
                        "        /opt/edge/bin/banner > /etc/issue.d/20-edge.issue\n"
                        "    fi\n"
                        "fi\n"
                    )
                os.chmod(hook_path, 0o755)
                emit_log("Injected dhclient exit hook for dynamic banner updates.")
            except Exception as e:
                emit_log(f"WARNING: Failed to write dhclient exit hook: {str(e)}")

        return

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
            # JSON is a subset of YAML, so this is a legal netplan file and
            # saves depending on PyYAML — which the offline kiosk payload does
            # not ship.
            yaml_str = json.dumps(np_config)
            f.write(yaml_str)
        emit_log("Injected wildcard Netplan config.")

    interfaces_file = f"{target_mnt}/etc/network/interfaces"
    if os.path.exists(interfaces_file) or os.path.exists(f"{target_mnt}/etc/network"):
        os.makedirs(f"{target_mnt}/etc/network/interfaces.d", exist_ok=True)
        with open(interfaces_file, "w") as f:
            f.write("auto lo\niface lo inet loopback\nsource /etc/network/interfaces.d/*\n")

        # A guess at the names this machine's NICs might have. Naming one that
        # does not exist costs nothing here because every entry is
        # allow-hotplug; missing the real one costs a site visit.
        ifaces_to_configure = ["eth0", "enp1s0", "enp2s0", "enp3s0"]
        if network_iface and network_iface not in ifaces_to_configure:
            ifaces_to_configure.append(network_iface)

        with open(f"{target_mnt}/etc/network/interfaces.d/orchestrator-dhcp", "w") as f:
            for iface in ifaces_to_configure:
                f.write(f"allow-hotplug {iface}\niface {iface} inet dhcp\n\n")
        emit_log(f"Injected /etc/network/interfaces.d config mapping: {', '.join(ifaces_to_configure)}")


def write_fstab(
    target_mnt: str,
    partitions: List[Dict[str, Any]],
    efi_uuid: str,
    emit_log: EmitLog,
) -> None:
    """Replace the restored fstab with one describing the partitions we just made.

    The archived fstab names the UUIDs of the *old* disk. We reformatted, so
    those UUIDs are gone — mount by them and the machine drops to an initramfs
    prompt. Filesystems are addressed by LABEL where one exists, because labels
    are what `disk_ops` set deliberately; UUID is the fallback.

    /boot/efi is the exception: vfat has no UUID, only a volume serial, so it is
    always addressed by the serial and always with `umask=0077` — the ESP is
    world-readable otherwise and it holds the boot chain.
    """
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
            # fsck order: root first, everything else after it.
            pass_num = 1 if mount == "/" else 2
            if label:
                fstab_lines.append(f"LABEL={label}   {mount}           {fstype}    {options}                  0       {pass_num}")
            else:
                fstab_lines.append(f"UUID={uuid}   {mount}           {fstype}    {options}                  0       {pass_num}")

    with open(fstab_path, "w") as f:
        f.write("\n".join(fstab_lines) + "\n")
    emit_log("Dynamic /etc/fstab successfully written.")


def audit_fstab(
    target_mnt: str,
    partitions: List[Dict[str, Any]],
    efi_uuid: str,
    emit_log: EmitLog,
) -> None:
    """Read the fstab back and confirm every partition is in it. Raises if not.

    Deliberately re-reads from disk rather than checking the list we just built:
    the failure this catches is the write not landing where we think it did —
    onto a filesystem that turned out not to be mounted, for instance — and
    comparing our own variables against themselves cannot see that.

    Worth failing the restore over. A missing entry here is a machine that does
    not boot, discovered by whoever powers it on rather than by us.
    """
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


def install_bootloader(target_mnt: str, target_dev: str, emit_log: EmitLog) -> None:
    """Reinstall GRUB into the restored system. Requires the chroot binds.

    The archive contains the target's /boot, but not a bootloader installed on
    *this* disk — grub lives in the ESP or the MBR gap, neither of which borg
    backs up. Without this the machine has a complete operating system and no
    way to start it.

    UEFI vs BIOS is decided by which module directory the restored system
    actually carries, not by how the orchestrator or the kiosk booted: those can
    differ from the target, and installing the wrong flavour produces a
    non-booting machine that looks fine.

    `--removable` writes to the fallback path EFI/BOOT/BOOTX64.EFI, and
    `--no-nvram` skips writing a boot entry — deliberate, because the NVRAM we
    can reach from here belongs to the machine running the restore, not the one
    being restored.
    """
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

    # Copy whatever grub installed to the removable-media fallback path. Some
    # firmware ignores NVRAM boot entries entirely and only looks here; since
    # --no-nvram means we wrote no entry, this is the path that actually boots.
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


def mask_live_config_generators(target_mnt: str, emit_log: EmitLog) -> None:
    """Stop the restored system behaving like the live ISO it may have come from.

    A node imaged from a Debian live environment inherits live-config's systemd
    generators, which reconfigure getty on every boot for a read-only live
    session. On installed hardware that fights the real configuration. Symlinking
    the generator to /dev/null is systemd's own masking convention.
    """
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


def clean_postgres_state(target_mnt: str, emit_log: EmitLog) -> None:
    """Undo the two ways a restored PostgreSQL refuses to start.

    Both come from backing up a running system rather than a stopped one:

    - **postmaster.pid** was written by a live postmaster and captured as-is.
      On the restored machine that PID belongs to nothing, and postgres refuses
      to start rather than risk two postmasters on one data directory. Here we
      know for certain there is no live postmaster, so removing it is safe.
    - **A relocated log directory.** Sites move the cluster log off the data
      volume, leaving `log` as a symlink; the exclusion rules then leave its
      target out of the archive. Postgres does not create the directory itself —
      it fails to start. Recreated with postgres:postgres and 0775 inside the
      chroot, because the numeric uid differs between systems.

    Neither is fatal to the restore: a machine that boots with postgres down is
    recoverable, one that does not boot at all is a site visit.
    """
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


def reinitialise_sentinel_ldk(
    target_mnt: str,
    repo_path: str,
    orchestrator_ip: Optional[str],
    emit_log: EmitLog,
) -> None:
    """Reinstall the Sentinel LDK runtime so the restored node can be licensed.

    The runtime keys its state to the machine it was installed on. Restored onto
    different hardware it carries a fingerprint that no longer matches, and the
    node comes up unlicensed with no obvious cause. Purge-and-reinstall is what
    makes it re-derive that state; `disk_ops` has already wiped /var/hasplm and
    /etc/hasplm so nothing stale survives underneath.

    Skipped entirely when the package is not installed — plenty of nodes do not
    use Sentinel.

    Offline first: the repository may carry the .debs, which matters because the
    machine being restored frequently has no route to the internet yet. The
    online fallback needs two things the chroot does not have — DNS, borrowed
    from the host's resolv.conf and put back afterwards, and a route to the
    archives, via the orchestrator's apt-cacher on :3142.

    Every failure is a warning, not an exception. A node that boots without a
    licence can be licensed remotely; a restore aborted at this point leaves a
    half-configured disk.
    """
    try:
        dpkg_check = subprocess.run(["chroot", target_mnt, "dpkg-query", "-W", "edge-hasp-eoawt3"], capture_output=True, text=True)
        if dpkg_check.returncode != 0:
            return

        emit_log("Re-initializing Sentinel LDK Runtime inside target chroot...")

        local_pkg_src = os.path.join(repo_path, "packages")
        local_pkg_dst = os.path.join(target_mnt, "tmp", "offline_packages")
        has_local_pkgs = False
        deb_files = []

        if os.path.exists(local_pkg_src):
            deb_files = [f for f in os.listdir(local_pkg_src) if f.endswith(".deb")]
            if deb_files:
                emit_log("Found offline Sentinel packages in repository cache. Preparing offline installation...")
                try:
                    # Copied inside the target because the chroot cannot reach
                    # paths outside it.
                    shutil.rmtree(local_pkg_dst, ignore_errors=True)
                    shutil.copytree(local_pkg_src, local_pkg_dst)
                    has_local_pkgs = True
                except Exception as copy_err:
                    emit_log(f"WARNING: Failed to copy offline packages to target: {copy_err}")

        if has_local_pkgs:
            deb_paths_in_chroot = [f"/tmp/offline_packages/{f}" for f in deb_files]
            emit_log(f"Installing offline packages: {', '.join(deb_files)}...")

            p_install = subprocess.run(
                ["chroot", target_mnt, "apt-get", "install", "-y"] + deb_paths_in_chroot,
                capture_output=True,
                text=True
            )
            if p_install.returncode != 0:
                # apt-get resolves dependencies and dpkg does not, so apt is
                # tried first; dpkg still succeeds when the only thing apt
                # objected to was an unreachable index.
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

            shutil.rmtree(local_pkg_dst, ignore_errors=True)
            return

        # Online fallback. Lend the chroot the host's DNS for the duration.
        resolv_conf_path = os.path.join(target_mnt, "etc", "resolv.conf")
        backup_resolv_conf = None
        if os.path.exists(resolv_conf_path) or os.path.islink(resolv_conf_path):
            try:
                # Preserved as symlink-or-file: on a systemd-resolved system
                # this is a link into /run, and replacing it with a plain file
                # breaks name resolution on the restored machine permanently.
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
            # No orchestrator address this time, but the archive may carry a
            # proxy.conf pointing at a site that no longer exists — which would
            # make every apt operation on the restored node hang.
            if os.path.exists(apt_proxy_conf):
                try:
                    os.remove(apt_proxy_conf)
                    emit_log("Removed stale target APT proxy config.")
                except Exception:
                    pass

        emit_log("Purging old configuration of edge-hasp-eoawt3 and edge-aksusbd...")
        p_purge = subprocess.run(["chroot", target_mnt, "apt-get", "purge", "-y", "edge-hasp-eoawt3", "edge-aksusbd"], capture_output=True, text=True)
        if p_purge.returncode != 0:
            emit_log(f"WARNING: apt-get purge failed (exit code {p_purge.returncode}): {p_purge.stderr.strip()}")

        emit_log("Updating package cache...")
        p_update = subprocess.run(["chroot", target_mnt, "apt-get", "update"], capture_output=True, text=True)
        if p_update.returncode != 0:
            emit_log(f"WARNING: apt-get update failed (exit code {p_update.returncode}): {p_update.stderr.strip()}")

        emit_log("Reinstalling edge-hasp-eoawt3 and edge-aksusbd...")
        p_install = subprocess.run(["chroot", target_mnt, "apt-get", "install", "-y", "edge-hasp-eoawt3", "edge-aksusbd"], capture_output=True, text=True)
        if p_install.returncode != 0:
            emit_log(f"WARNING: apt-get install failed (exit code {p_install.returncode}): {p_install.stderr.strip()}")
        else:
            emit_log("Sentinel LDK Runtime successfully re-initialized.")

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


def install_checkin_service(
    target_mnt: str,
    orchestrator_ip: Optional[str],
    available_server_ips: Optional[str],
    emit_log: EmitLog,
) -> None:
    """Install a one-shot service that tells the orchestrator the node is back.

    The restored node's address is not knowable from here — it DHCPs on first
    boot — so the node has to make the first move. It tries every orchestrator
    address it was given, because the machine may come up on a different subnet
    than the one the restore was driven from, and reports the source address the
    kernel actually chose for that route rather than guessing among its own.

    On the first acknowledged check-in it disables the unit and deletes both
    itself and the unit file. Self-deletion rather than a `ConditionPathExists`
    guard: this must leave no trace on a production machine, and a disabled unit
    lying around is something a later operator has to reason about.

    Every failure is a warning. Failing the restore because a convenience
    service could not be written would be the wrong trade — the node is
    otherwise complete, and an operator can register it by hand.
    """
    targets = []
    if available_server_ips:
        targets = [ip.strip() for ip in available_server_ips.split(",") if ip.strip()]
    if orchestrator_ip and orchestrator_ip not in targets:
        targets.append(orchestrator_ip)

    if not targets:
        return

    emit_log("Configuring one-time post-restore checkin script on target system...")
    try:
        targets_bash = " ".join(f'"{ip}"' for ip in targets)
        checkin_sh_path = os.path.join(target_mnt, "usr", "local", "bin", "edge-restore-checkin.sh")
        checkin_sh_content = (
            "#!/bin/bash\n"
            f"SERVER_IPS=({targets_bash})\n\n"
            "while true; do\n"
            "    for ip in \"${SERVER_IPS[@]}\"; do\n"
            "        if ping -c 1 -W 2 \"$ip\" >/dev/null 2>&1; then\n"
            "            HOSTNAME=$(hostname)\n"
            "            IP_ADDR=$(ip route get \"$ip\" 2>/dev/null | awk '{{print $7; exit}}')\n"
            "            if [ -z \"$IP_ADDR\" ]; then\n"
            "                IP_ADDR=$(ip addr show scope global | grep inet | awk '{{print $2}}' | cut -d/ -f1 | head -n1)\n"
            "            fi\n\n"
            "            if [ -n \"$IP_ADDR\" ]; then\n"
            "                res=$(curl -s --connect-timeout 5 -X POST -H \"Content-Type: application/json\" \\\n"
            "                           -d \"{\\\"hostname\\\": \\\"$HOSTNAME\\\", \\\"ip_address\\\": \\\"$IP_ADDR\\\"}\" \\\n"
            "                           \"http://$ip:8000/api/nodes/checkin-restored\" 2>/dev/null)\n\n"
            "                if [[ \"$res\" == *\"success\"* ]]; then\n"
            "                    systemctl disable edge-restore-checkin.service\n"
            "                    rm -f /etc/systemd/system/edge-restore-checkin.service\n"
            "                    rm -f /usr/local/bin/edge-restore-checkin.sh\n"
            "                    systemctl daemon-reload\n"
            "                    exit 0\n"
            "                fi\n"
            "            fi\n"
            "        fi\n"
            "    done\n"
            "    sleep 30\n"
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
Type=simple
ExecStart=/usr/local/bin/edge-restore-checkin.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
        with open(checkin_service_path, "w") as f:
            f.write(checkin_service_content)

        subprocess.run(["chroot", target_mnt, "systemctl", "enable", "edge-restore-checkin.service"], capture_output=True)
        emit_log("One-time post-restore checkin service successfully installed.")
    except Exception as checkin_err:
        emit_log(f"WARNING: Failed to configure post-restore checkin script: {checkin_err}")
