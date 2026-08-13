"""Building the technician's Live-USB image, one step at a time.

`generate_client_iso_task` was a 423-line straight line: unpack a Debian live
ISO, assemble a directory of everything the offline client needs, pack it into
a second initrd, teach both bootloaders to load it, and repack the lot. Every
step wrote into the same handful of local paths, so nothing could be read,
tested or reordered on its own.

## How the payload reaches the running system

Worth understanding before changing anything here, because it is not obvious
and nothing else in the tree explains it.

The stock Debian live ISO is not modified in any interesting way. Instead a
*second* initrd — `payload.img` — is appended to the boot line, and Linux
concatenates multiple initrds into one filesystem. So our files appear
alongside Debian's without touching Debian's own `initrd.img`, which means the
base ISO can be replaced wholesale on the next release without redoing any of
this.

That filesystem is then thrown away at `switch_root`. An `init-bottom` hook
(`scripts/init-bottom/copy-payload`, injected below) copies the payload onto
the real root before that happens. Remove the hook and the ISO still boots,
still shows a desktop, and simply has no client on it.

Both bootloaders have to be patched because it is not knowable in advance which
one runs: GRUB on UEFI machines, isolinux on BIOS ones, and the fleet has both.
Patching one produces an image that works on the bench and fails at the site.
"""
import json
import logging
import os
import re
import shutil
import sys
from typing import Callable, Dict, List, Optional

from core import task_log
from core.task_log import log_to_task

#: Reached through the module rather than bound directly: the ISO build is the
#: one path in the codebase that shells out to multi-gigabyte tooling, and the
#: tests have to be able to substitute it from a single place.

logger = logging.getLogger(__name__)

#: Files from backend/core/ copied into the offline payload. The kiosk runs the
#: same bare-metal restore the orchestrator does, from the same source.
#:
#: **Every module these import from core/ must be in this list.** The payload
#: client wraps its `from core.disk_ops import ...` in a bare except, so a
#: missing file does not crash the kiosk — it produces a kiosk whose Restore
#: button quietly does nothing, discovered by a technician standing in front of
#: a dead server. That is exactly how the routers below shipped broken.
#: `tests/test_iso_payload_injection.py` walks the imports and fails if this
#: list falls behind.
INJECTED_CORE_MODULES = ("disk_ops.py", "guest_config.py")

#: Same contract for backend/routers/. network.py imports both sub-routers at
#: module scope, so all three ship together.
INJECTED_ROUTER_MODULES = ("network.py", "network_dhcp.py", "network_wg.py")

#: Third-party packages the offline client imports. Copied out of the
#: orchestrator's site-packages rather than pip-installed, because the machine
#: running the kiosk has no network by definition.
PAYLOAD_SITE_PACKAGES = (
    "fastapi", "pydantic", "pydantic_core", "uvicorn", "starlette",
    "anyio", "h11", "click", "annotated_types", "idna",
    "annotated_doc", "typing_inspection", "watchfiles", "python_multipart", "multipart",
    "serial",
)

#: systemd units injected as (source filename, whether to enable at boot).
#: Enabling is a symlink into multi-user.target.wants rather than a `systemctl
#: enable`, because there is no running systemd to ask — we are assembling a
#: filesystem, not configuring a machine.
PAYLOAD_SERVICES = (
    "offline-backend.service",
    "offline-ssh-install.service",
    "kiosk-vpn-setup.service",
    "kiosk-storage-setup.service",
)

#: The xorriso invocation that produces a bootable hybrid image, shared by the
#: generic build and the per-kiosk repack. It was written out twice, verbatim,
#: twenty arguments each; the two copies staying in step was luck.
#:
#: "Hybrid" means the same file boots from a DVD and from a USB stick written
#: with dd, which is how these are actually delivered. The isohybrid flags are
#: what make that work, and dropping any of them yields an ISO that mounts
#: fine and does not boot.
def mkisofs_argv(output_iso: str, source_dir: str) -> List[str]:
    return [
        "xorriso",
        "-as", "mkisofs",
        "-r", "-J", "-joliet-long",
        "-l", "-cache-inodes",
        "-isohybrid-mbr", "/usr/lib/ISOLINUX/isohdpfx.bin",
        "-partition_offset", "16",
        "-A", "Borg-Restore-Technician-Client",
        # BIOS boot path.
        "-b", "isolinux/isolinux.bin",
        "-c", "isolinux/boot.cat",
        "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
        # UEFI boot path, in the same image.
        "-eltorito-alt-boot",
        "-e", "boot/grub/efi.img",
        "-no-emul-boot", "-isohybrid-gpt-basdat", "-isohybrid-apm-hfsplus",
        "-o", output_iso,
        source_dir,
    ]


#: Rough size of the Debian live image we ship, used only to turn xorriso's
#: "N files restored" into a percentage when the file cannot be stat'd. Being
#: wrong by a few hundred MB makes the bar finish early or late; it is not
#: worth failing a build over.
FALLBACK_BASE_ISO_MB = 3800.0


def unpack_iso(task_id: str, iso_path: str, dest: str, progress_from: int, progress_to: int) -> None:
    """Extract an ISO to a writable directory, reporting progress as it goes.

    xorriso emits "N files restored (1.2g)" lines; there is no total, so the
    percentage is against the source file's size on disk. Extraction is also
    the longest silent stretch of the build, which is why it is worth parsing
    at all.
    """
    base_size_mb = FALLBACK_BASE_ISO_MB
    try:
        if os.path.exists(iso_path):
            base_size_mb = os.path.getsize(iso_path) / (1024.0 * 1024.0)
    except Exception:
        pass

    span = progress_to - progress_from

    def on_line(line: str) -> None:
        m = re.search(r"files restored \(([0-9.]+)([mg])\)", line)
        if not m:
            return
        try:
            val, unit = float(m.group(1)), m.group(2).lower()
            mb = val * 1024.0 if unit == 'g' else val
            pct = min(100.0, max(0.0, (mb / base_size_mb) * 100.0))
            log_to_task(task_id, f"[PROGRESS] {int(progress_from + (pct * span / 100.0))}:Unpacking base ISO...")
        except Exception:
            pass

    task_log.run_command_with_logging(
        task_id, ["xorriso", "-osirrox", "on", "-indev", iso_path, "-extract", "/", dest],
        on_log_line=on_line,
    )

    # xorriso restores the read-only permissions the ISO recorded, and every
    # step after this writes into the tree.
    task_log.run_command_with_logging(task_id, ["chmod", "-v", "-R", "+w", dest])


def stage_payload_tree(payload_dir: str) -> str:
    """Create the payload's directory skeleton. Returns the client's root."""
    opt_offline = os.path.join(payload_dir, "opt", "offline-client")
    os.makedirs(os.path.join(opt_offline, "backend", "core"), exist_ok=True)
    os.makedirs(os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants"), exist_ok=True)
    os.makedirs(os.path.join(payload_dir, "etc", "xdg", "autostart"), exist_ok=True)
    return opt_offline


def inject_shared_backend_modules(opt_offline: str) -> None:
    """Copy the orchestrator modules the kiosk shares. See INJECTED_* above."""
    for core_file in INJECTED_CORE_MODULES:
        shutil.copy2(f"/app/core/{core_file}", os.path.join(opt_offline, "backend", "core", core_file))

    routers_dir = os.path.join(opt_offline, "backend", "routers")
    os.makedirs(routers_dir, exist_ok=True)
    open(os.path.join(routers_dir, "__init__.py"), "w").close()
    for router_file in INJECTED_ROUTER_MODULES:
        shutil.copy2(f"/app/routers/{router_file}", os.path.join(routers_dir, router_file))

    shutil.copy2("/app/version.py", os.path.join(opt_offline, "backend", "version.py"))


def inject_binaries_and_frontend(payload_dir: str, opt_offline: str) -> None:
    """The statically linked borg, and the built web UI the kiosk serves."""
    borg_src = "/payload_client/bin/borg"
    if os.path.exists(borg_src):
        # Static build: the live environment has no borg and no way to install
        # one, and the restore is the entire point of the image.
        borg_dst_dir = os.path.join(payload_dir, "usr", "local", "bin")
        os.makedirs(borg_dst_dir, exist_ok=True)
        shutil.copy2(borg_src, os.path.join(borg_dst_dir, "borg"))

    if os.path.exists("/opt/frontend_build"):
        shutil.copytree("/opt/frontend_build", os.path.join(opt_offline, "backend", "frontend_build"))


def inject_services(payload_dir: str) -> None:
    """Install and enable the kiosk's systemd units."""
    systemd_dir = os.path.join(payload_dir, "etc", "systemd", "system")
    wants_dir = os.path.join(systemd_dir, "multi-user.target.wants")

    for unit in PAYLOAD_SERVICES:
        shutil.copy2(f"/payload_client/systemd/{unit}", os.path.join(systemd_dir, unit))
        # Absolute link target: it is resolved on the booted system, not here.
        os.symlink(f"/etc/systemd/system/{unit}", os.path.join(wants_dir, unit))

    # The desktop entry goes in two places on purpose: xdg/autostart launches
    # the kiosk UI at login, and the copy in /etc/skel gives the technician a
    # visible icon to relaunch it from if they close it.
    kiosk_src = "/payload_client/systemd/offline-kiosk.desktop"
    shutil.copy2(kiosk_src, os.path.join(payload_dir, "etc", "xdg", "autostart", "offline-kiosk.desktop"))

    desktop_dir = os.path.join(payload_dir, "etc", "skel", "Desktop")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_dst = os.path.join(desktop_dir, "offline-kiosk.desktop")
    shutil.copy2(kiosk_src, desktop_dst)
    os.chmod(desktop_dst, 0o755)


def inject_scripts(payload_dir: str) -> None:
    """The launcher, the USB storage setup, and the initrd survival hook."""
    opt_offline = os.path.join(payload_dir, "opt", "offline-client")

    for script in ("kiosk-launcher.sh", "kiosk-storage-setup.sh"):
        dst = os.path.join(opt_offline, script)
        shutil.copy2(f"/payload_client/{script}", dst)
        os.chmod(dst, 0o755)

    # This is the hook described in the module docstring: without it the
    # payload is discarded at switch_root and the ISO boots to a desktop with
    # no client on it.
    init_bottom_dir = os.path.join(payload_dir, "scripts", "init-bottom")
    os.makedirs(init_bottom_dir, exist_ok=True)
    init_bottom_dst = os.path.join(init_bottom_dir, "copy-payload")
    shutil.copy2("/payload_client/init-bottom-copy-payload.sh", init_bottom_dst)
    os.chmod(init_bottom_dst, 0o755)

    # And this is what makes initramfs-tools run it. The hook alone is inert.
    conf_dir = os.path.join(payload_dir, "conf")
    os.makedirs(conf_dir, exist_ok=True)
    shutil.copy2("/payload_client/conf/param.conf", os.path.join(conf_dir, "param.conf"))


def inject_offline_packages(payload_dir: str) -> None:
    """Debian packages the kiosk installs onto restored nodes with no network."""
    pkg_dst = os.path.join(payload_dir, "opt", "offline-client", "packages")
    os.makedirs(pkg_dst, exist_ok=True)
    if os.path.exists("/opt/offline-packages"):
        for file in os.listdir("/opt/offline-packages"):
            if file.endswith(".deb"):
                shutil.copy2(os.path.join("/opt/offline-packages", file), os.path.join(pkg_dst, file))


def inject_site_packages(opt_offline: str) -> None:
    """Copy the Python dependencies the offline client imports.

    Version-matched to the interpreter building the image, which is the same
    interpreter version the live environment ships — they come from the same
    Debian release. A mismatch would surface as an ImportError on the kiosk.
    """
    py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages_dst = os.path.join(opt_offline, "backend", "site-packages")
    os.makedirs(site_packages_dst, exist_ok=True)

    for pkg in PAYLOAD_SITE_PACKAGES:
        pkg_src = f"/usr/local/lib/{py_ver}/site-packages/{pkg}"
        if os.path.isdir(pkg_src):
            shutil.copytree(pkg_src, os.path.join(site_packages_dst, pkg))
        elif os.path.isfile(pkg_src + ".py"):
            shutil.copy2(pkg_src + ".py", os.path.join(site_packages_dst, pkg + ".py"))

    # Single-module distribution, so it misses the loop above.
    shutil.copy2(
        f"/usr/local/lib/{py_ver}/site-packages/typing_extensions.py",
        os.path.join(site_packages_dst, "typing_extensions.py"),
    )


def write_kiosk_config(opt_offline: str, config: Dict[str, object]) -> None:
    """The kiosk's identity, read by the payload client on first boot."""
    with open(os.path.join(opt_offline, "backend", "config.json"), "w") as f:
        json.dump(config, f, indent=4)


def read_kiosk_config(payload_unpacked: str) -> Dict[str, object]:
    """Read an existing payload's config, for the per-kiosk repack."""
    config_path = kiosk_config_path(payload_unpacked)
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def kiosk_config_path(payload_unpacked: str) -> str:
    return os.path.join(payload_unpacked, "opt", "offline-client", "backend", "config.json")


def inject_orchestrator_ssh_key(opt_offline: str) -> None:
    """Give the kiosk the orchestrator's key so it can reach the borg repo.

    The private key ships inside the image. That is a real exposure and it is
    accepted deliberately: the kiosk must pull archives over SSH with no
    operator present to type anything, and the image is handed to a technician
    who is already trusted with the servers it restores. Mode 0600 is
    housekeeping, not protection.
    """
    backend_dir = os.path.join(opt_offline, "backend")
    shutil.copy2("/root/.ssh/id_ed25519", os.path.join(backend_dir, "id_ed25519"))
    os.chmod(os.path.join(backend_dir, "id_ed25519"), 0o600)
    if os.path.exists("/root/.ssh/id_ed25519.pub"):
        shutil.copy2("/root/.ssh/id_ed25519.pub", os.path.join(backend_dir, "id_ed25519.pub"))


def pack_payload_initrd(task_id: str, payload_dir: str, payload_img: str) -> None:
    """cpio+gzip the staged tree into the second initrd.

    newc is the only format the kernel's initramfs loader accepts. `find
    -print0` with `--null` rather than a plain listing, because the tree
    contains paths from Debian packages and one with a space in it would
    silently truncate the archive.
    """
    task_log.run_command_with_logging(
        task_id,
        f"cd {payload_dir} && find . -print0 | cpio -v --null --create --format=newc | gzip > {payload_img}",
        shell=True,
    )


def patch_grub_config(iso_unpacked: str) -> None:
    """Append payload.img to GRUB's initrd line, and shorten the menu timeout.

    The kernel concatenates every initrd named on the line, so this is the
    whole mechanism — no changes to Debian's own initrd.img.

    Five seconds rather than the stock thirty: a technician standing over the
    machine does not want to wait, and the menu is still there for the rare
    case they need it.
    """
    grub_cfg = os.path.join(iso_unpacked, "boot", "grub", "grub.cfg")
    if not os.path.exists(grub_cfg):
        return

    with open(grub_cfg, "r") as f:
        content = f.read()

    lines = []
    timeout_set = False
    for line in content.splitlines():
        if line.strip().startswith("set timeout="):
            line = "set timeout=5"
            timeout_set = True
        elif line.strip().startswith("initrd") and "/live/initrd.img" in line and "payload.img" not in line:
            # The payload.img guard makes this safe to run over an already
            # patched tree, which the per-kiosk repack does.
            line = line.rstrip() + " /live/payload.img"
        lines.append(line)

    if not timeout_set:
        lines.insert(0, "set timeout=5")

    with open(grub_cfg, "w") as f:
        f.write("\n".join(lines) + "\n")


def patch_syslinux_configs(iso_unpacked: str) -> None:
    """The same edit for isolinux, whose syntax is entirely different.

    GRUB takes a space-separated list of initrds; syslinux takes one
    comma-separated `initrd=` value, and writes it either as a standalone
    `initrd` directive or inline on the append line. Both spellings appear in
    Debian's own configs, hence the two branches.

    Its timeout is in tenths of a second, so 50 is the same five seconds.
    """
    isolinux_dir = os.path.join(iso_unpacked, "isolinux")
    for root_dir, _, files in os.walk(isolinux_dir):
        for file in files:
            if not file.endswith(".cfg"):
                continue
            filepath = os.path.join(root_dir, file)
            with open(filepath, "r") as f:
                content = f.read()

            lines = []
            for line in content.splitlines():
                if line.strip().startswith("#"):
                    lines.append(line)
                    continue

                parts = line.strip().split()
                if parts and parts[0].lower() == "timeout":
                    indent = line[:line.find("timeout")]
                    line = indent + "timeout 50"
                elif "initrd" in line and "/live/initrd.img" in line and "payload.img" not in line:
                    keyword = "initrd=" if "initrd=" in line else "initrd"
                    head, tail = line.split(keyword, 1)
                    val_parts = tail.split(maxsplit=1)
                    val = val_parts[0]
                    rest = " " + val_parts[1] if len(val_parts) > 1 else ""
                    line = head + keyword + val + ",/live/payload.img" + rest
                lines.append(line)

            with open(filepath, "w") as f:
                f.write("\n".join(lines) + "\n")


def update_md5sums(task_id: str, iso_unpacked: str) -> None:
    """Rebuild md5sum.txt so the live image's self-check still passes.

    Debian's installer offers to verify the media against this file. Every
    injection above invalidated it, and a technician who runs the check on an
    unmodified list gets a scary failure on a perfectly good disc.

    isolinux is excluded because isohybrid rewrites it after this point.
    """
    if not os.path.exists(os.path.join(iso_unpacked, "md5sum.txt")):
        return
    task_log.run_command_with_logging(
        task_id,
        f"cd {iso_unpacked} && find . -type f -not -name md5sum.txt "
        f"-not -path './isolinux/*' -exec md5sum {{}} \\; > md5sum.txt",
        shell=True,
    )


def repack_iso(
    task_id: str,
    iso_unpacked: str,
    output_iso: str,
    progress_from: Optional[int] = None,
    progress_to: Optional[int] = None,
    message: str = "Repacking Live-USB ISO...",
    replace_existing: bool = False,
) -> None:
    """Build the final hybrid ISO. Reports progress when given a range.

    `replace_existing` unlinks the target first. The generic template is built
    to a fixed filename and does this, so a failed run cannot leave a truncated
    image sitting where the next build expects a whole one. Per-kiosk ISOs are
    written to a fresh dated filename each time and have nothing to unlink.
    """
    if replace_existing and os.path.exists(output_iso):
        os.remove(output_iso)

    on_line: Optional[Callable[[str], None]] = None
    if progress_from is not None and progress_to is not None:
        span = progress_to - progress_from

        def on_line(line: str) -> None:  # noqa: F811
            m = re.search(r"UPDATE\s*:\s*([0-9.]+)%\s*done", line)
            if not m:
                return
            try:
                pct = float(m.group(1))
                log_to_task(task_id, f"[PROGRESS] {int(progress_from + (pct * span / 100.0))}:{message}")
            except Exception:
                pass

    task_log.run_command_with_logging(task_id, mkisofs_argv(output_iso, iso_unpacked), on_log_line=on_line)


def prune_kiosk_iso_history(history_dir: str, keep: int) -> None:
    """Keep only the newest `keep` per-kiosk ISOs. Each is several gigabytes."""
    iso_files = []
    for file in os.listdir(history_dir):
        if file.endswith(".iso") and "-kiosk-" in file:
            filepath = os.path.join(history_dir, file)
            iso_files.append((filepath, os.path.getmtime(filepath)))

    iso_files.sort(key=lambda x: x[1])  # oldest first
    for filepath, _ in iso_files[:max(0, len(iso_files) - keep)]:
        try:
            os.remove(filepath)
            logger.info(f"Pruned old kiosk ISO: {filepath}")
        except Exception as pe:
            logger.error(f"Failed to prune old ISO {filepath}: {pe}")
