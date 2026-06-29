import os
import shutil
import subprocess
import json
import logging
import hashlib
from celery_app import celery_app
from typing import Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_MIRROR_URLS = [
    "https://mirror.yandex.ru/debian-cd/current-live/amd64/iso-hybrid/debian-live-13.5.0-amd64-xfce.iso",
    "https://mirrors.edge.kernel.org/debian-cd/current-live/amd64/iso-hybrid/debian-live-13.5.0-amd64-xfce.iso",
    "https://cdimage.debian.org/cdimage/weekly-live-builds/amd64/iso-hybrid/debian-live-testing-amd64-xfce.iso"
]
BASE_ISO_URL = DEFAULT_MIRROR_URLS[0]
CACHE_DIR = "/opt/data/iso_cache"
BASE_ISO_PATH = os.path.join(CACHE_DIR, "base.iso")
BASE_ISO_PATH_TMP = BASE_ISO_PATH + ".tmp"

@celery_app.task(bind=True)
def download_base_iso_task(self, url: str = None) -> Dict[str, Any]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    lock_path = os.path.join(CACHE_DIR, "download.lock")
    download_completed = False
    try:
        if os.path.exists(BASE_ISO_PATH):
            if os.path.getsize(BASE_ISO_PATH) > 1000 * 1024 * 1024:
                return {"status": "SUCCESS", "message": "Base ISO already cached."}
            else:
                os.remove(BASE_ISO_PATH)
        
        urls_to_try = [url] if url else DEFAULT_MIRROR_URLS
        download_url = None
        content_length = None

        for attempt_url in urls_to_try:
            logger.info(f"Checking mirror: {attempt_url}")
            try:
                header_out = subprocess.check_output([
                    "curl", "-4", "--connect-timeout", "5", "--retry", "1", "-s", "-I", "-L", attempt_url
                ]).decode('utf-8', errors='ignore')
                
                temp_length = None
                for line in header_out.splitlines():
                    if line.lower().startswith("content-length:"):
                        temp_length = line.split(":", 1)[1].strip()
                
                if temp_length and temp_length.isdigit():
                    content_length = temp_length
                    download_url = attempt_url
                    logger.info(f"Mirror verified. Content-Length: {content_length}. Selected URL: {download_url}")
                    break
            except Exception as e:
                logger.warning(f"Mirror check failed for {attempt_url}: {e}")

        if not download_url:
            download_url = urls_to_try[0]
            logger.warning(f"All mirror checks failed. Falling back to primary URL: {download_url}")

        if content_length:
            try:
                with open(os.path.join(CACHE_DIR, "base.iso.size"), "w") as f:
                    f.write(content_length)
            except Exception as size_err:
                logger.warning(f"Could not write base.iso.size file: {size_err}")

        is_official = download_url in DEFAULT_MIRROR_URLS
        logger.info(f"Downloading Base ISO from {download_url}...")

        # Check if another curl process is already downloading to the tmp path
        try:
            pgrep_out = subprocess.check_output(["pgrep", "-f", "curl.*base.iso.tmp"]).decode().strip()
            if pgrep_out:
                logger.warning(f"Another curl process (PIDs: {pgrep_out}) is already downloading. Aborting this task to prevent conflict.")
                return {"status": "SUCCESS", "message": "Base ISO download already in progress."}
        except subprocess.CalledProcessError:
            pass

        # Check if the temporary file is already completely downloaded
        if os.path.exists(BASE_ISO_PATH_TMP) and content_length:
            try:
                current_size = os.path.getsize(BASE_ISO_PATH_TMP)
                target_size = int(content_length)
                if current_size == target_size:
                    logger.info("Temporary file is already complete. Skipping curl download.")
                    download_completed = True
                elif current_size > target_size:
                    logger.warning("Local temporary file size is larger than remote content length. Deleting and restarting.")
                    os.remove(BASE_ISO_PATH_TMP)
            except ValueError:
                pass

        if not download_completed:
            # Use curl to download the file safely to a temporary path with fail-fast (-f) and resume (-C -)
            # Relaxed speed limits to prevent download failures on slow connections
            subprocess.check_call([
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-L", "-C", "-", "-o", BASE_ISO_PATH_TMP, download_url
            ])
            download_completed = True

        if is_official:
            logger.info("Downloading SHA512SUMS for validation...")
            sums_url = download_url.rsplit('/', 1)[0] + "/SHA512SUMS"
            sums_path = os.path.join(CACHE_DIR, "SHA512SUMS")
            subprocess.check_call([
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-sL", "-o", sums_path, sums_url
            ])
            
            iso_filename = os.path.basename(download_url)
            expected_hash = None
            with open(sums_path, 'r') as f:
                for line in f:
                    if iso_filename in line:
                        expected_hash = line.split()[0]
                        break
                        
            if not expected_hash:
                raise Exception("Could not find expected hash in SHA512SUMS")
                
            logger.info(f"Validating ISO checksum (expected: {expected_hash[:8]}...)..")
            hasher = hashlib.sha512()
            with open(BASE_ISO_PATH_TMP, 'rb') as f:
                for chunk in iter(lambda: f.read(4096 * 1024), b""):
                    hasher.update(chunk)
                    
            actual_hash = hasher.hexdigest()
            if actual_hash != expected_hash:
                raise Exception(f"Checksum mismatch! Expected {expected_hash}, got {actual_hash}")
        else:
            logger.info("Custom ISO URL provided. Skipping SHA512 validation.")
            
        os.rename(BASE_ISO_PATH_TMP, BASE_ISO_PATH)
        return {"status": "SUCCESS", "message": "Base ISO downloaded successfully."}
    except Exception as e:
        logger.error(f"Download or validation failed: {e}")
        # Only remove the temporary file if download completed but checksum validation failed
        if download_completed and os.path.exists(BASE_ISO_PATH_TMP):
            try:
                os.remove(BASE_ISO_PATH_TMP)
            except Exception as re:
                logger.error(f"Failed to remove corrupt temporary file: {re}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        # Only clean up lock file if no other download process is currently active
        try:
            pgrep_out = subprocess.check_output(["pgrep", "-f", "curl.*base.iso.tmp"]).decode().strip()
            has_active_curl = bool(pgrep_out)
        except subprocess.CalledProcessError:
            has_active_curl = False

        if not has_active_curl and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception as le:
                logger.error(f"Failed to remove download lock file: {le}")

def generate_kiosk_id() -> str:
    """Generates a memorable kiosk identifier in XX1234 pattern (2 letters + 4 digits)."""
    import random
    import string
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = "".join(random.choices(string.digits, k=4))
    return f"{letters}{digits}"

@celery_app.task(bind=True)
def generate_client_iso_task(self, target_ip: str, auth_token: str) -> Dict[str, Any]:
    from tasks import log_to_task, run_command_with_logging
    from database import SessionLocal
    from models import TaskLog
    
    task_id = self.request.id

    db = SessionLocal()
    task_log = TaskLog(id=task_id, task_type="ISO_GEN", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()
    db.close()

    # Validate cached ISO size
    if os.path.exists(BASE_ISO_PATH):
        if os.path.getsize(BASE_ISO_PATH) < 1000 * 1024 * 1024:
            logger.warning("Cached Base ISO is too small (corrupted). Deleting it.")
            os.remove(BASE_ISO_PATH)

    output_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    base_iso_to_use = BASE_ISO_PATH

    if not os.path.exists(BASE_ISO_PATH):
        if os.path.exists(output_iso) and os.path.getsize(output_iso) > 1000 * 1024 * 1024:
            logger.info("Base ISO not found, but client ISO exists. Using existing client ISO as base.")
            base_iso_to_use = output_iso
        else:
            log_to_task(task_id, "[PROGRESS] 5:Downloading Base ISO...")
            # Use the mirror sequence checking logic to select the working URL
            download_url = None
            for attempt_url in DEFAULT_MIRROR_URLS:
                logger.info(f"Checking mirror for client ISO generation: {attempt_url}")
                try:
                    subprocess.check_call([
                        "curl", "-4", "--connect-timeout", "5", "--retry", "1", "-s", "-I", "-L", attempt_url
                    ])
                    download_url = attempt_url
                    break
                except Exception as e:
                    logger.warning(f"Mirror check failed for {attempt_url}: {e}")
            
            if not download_url:
                download_url = DEFAULT_MIRROR_URLS[0]
                logger.warning(f"All mirror checks failed. Falling back to primary URL: {download_url}")

            run_command_with_logging(task_id, [
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-L", "-o", BASE_ISO_PATH, download_url
            ])

    work_dir = f"/tmp/iso_gen_{task_id}"
    iso_unpacked = os.path.join(work_dir, "iso_unpacked")
    payload_dir = os.path.join(work_dir, "payload_initrd")
    
    try:
        # 1. Unpack Base ISO
        log_to_task(task_id, "[PROGRESS] 10:Unpacking base ISO...")
        os.makedirs(work_dir, exist_ok=True)
        base_size_mb = 3800.0
        try:
            if os.path.exists(base_iso_to_use):
                base_size_mb = os.path.getsize(base_iso_to_use) / (1024.0 * 1024.0)
        except Exception:
            pass

        def on_unpack_line(line: str) -> None:
            import re
            m = re.search(r"files restored \(([0-9.]+)([mg])\)", line)
            if m:
                try:
                    val, unit = float(m.group(1)), m.group(2).lower()
                    mb = val * 1024.0 if unit == 'g' else val
                    pct = min(100.0, max(0.0, (mb / base_size_mb) * 100.0))
                    log_to_task(task_id, f"[PROGRESS] {int(10 + (pct * 0.10))}:Unpacking base ISO...")
                except Exception:
                    pass

        run_command_with_logging(task_id, ["xorriso", "-osirrox", "on", "-indev", base_iso_to_use, "-extract", "/", iso_unpacked], on_log_line=on_unpack_line)

        # Make iso_unpacked writable
        log_to_task(task_id, "[PROGRESS] 20:Preparing directory write permissions...")
        run_command_with_logging(task_id, ["chmod", "-v", "-R", "+w", iso_unpacked])

        # 2. Prepare Secondary Initrd Payload
        log_to_task(task_id, "[PROGRESS] 30:Injecting payload and configurations...")
        opt_offline = os.path.join(payload_dir, "opt", "offline-client")
        os.makedirs(os.path.join(opt_offline, "backend", "core"), exist_ok=True)
        os.makedirs(os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants"), exist_ok=True)
        os.makedirs(os.path.join(payload_dir, "etc", "xdg", "autostart"), exist_ok=True)

        # Copy Payload Backend
        run_command_with_logging(task_id, f"cp -v -r /payload_client/backend/* {os.path.join(opt_offline, 'backend')}/", shell=True)
        
        # Inject Shared Disk Ops Module
        shutil.copy2("/app/core/disk_ops.py", os.path.join(opt_offline, "backend", "core", "disk_ops.py"))

        # Inject Shared Network settings router
        os.makedirs(os.path.join(opt_offline, "backend", "routers"), exist_ok=True)
        open(os.path.join(opt_offline, "backend", "routers", "__init__.py"), "w").close()
        shutil.copy2("/app/routers/network.py", os.path.join(opt_offline, "backend", "routers", "network.py"))

        # Inject Unified version configuration
        shutil.copy2("/app/version.py", os.path.join(opt_offline, "backend", "version.py"))

        # Inject Frontend Build (mapped via named volume to /opt/frontend_build)
        if os.path.exists("/opt/frontend_build"):
            shutil.copytree("/opt/frontend_build", os.path.join(opt_offline, "backend", "frontend_build"))

        # Inject Systemd Backend Service
        svc_src = "/payload_client/systemd/offline-backend.service"
        svc_dst = os.path.join(payload_dir, "etc", "systemd", "system", "offline-backend.service")
        shutil.copy2(svc_src, svc_dst)
        os.symlink("/etc/systemd/system/offline-backend.service", os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants", "offline-backend.service"))

        # Inject SSH Installer Service
        ssh_svc_src = "/payload_client/systemd/offline-ssh-install.service"
        ssh_svc_dst = os.path.join(payload_dir, "etc", "systemd", "system", "offline-ssh-install.service")
        shutil.copy2(ssh_svc_src, ssh_svc_dst)
        os.symlink("/etc/systemd/system/offline-ssh-install.service", os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants", "offline-ssh-install.service"))

        # Copy Offline SSH Packages (.deb files)
        pkg_dst = os.path.join(payload_dir, "opt", "offline-client", "packages")
        os.makedirs(pkg_dst, exist_ok=True)
        if os.path.exists("/opt/offline-packages"):
            for file in os.listdir("/opt/offline-packages"):
                if file.endswith(".deb"):
                    shutil.copy2(os.path.join("/opt/offline-packages", file), os.path.join(pkg_dst, file))

        # Inject Kiosk Launcher Script
        launcher_src = "/payload_client/kiosk-launcher.sh"
        launcher_dst = os.path.join(payload_dir, "opt", "offline-client", "kiosk-launcher.sh")
        shutil.copy2(launcher_src, launcher_dst)
        os.chmod(launcher_dst, 0o755)

        # Inject Kiosk Persistent USB Storage Setup Script & Service
        setup_script_src = "/payload_client/kiosk-storage-setup.sh"
        setup_script_dst = os.path.join(payload_dir, "opt", "offline-client", "kiosk-storage-setup.sh")
        shutil.copy2(setup_script_src, setup_script_dst)
        os.chmod(setup_script_dst, 0o755)

        setup_svc_src = "/payload_client/systemd/kiosk-storage-setup.service"
        setup_svc_dst = os.path.join(payload_dir, "etc", "systemd", "system", "kiosk-storage-setup.service")
        shutil.copy2(setup_svc_src, setup_svc_dst)
        os.symlink("/etc/systemd/system/kiosk-storage-setup.service", os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants", "kiosk-storage-setup.service"))


        # Inject Kiosk Desktop Entry
        kiosk_src = "/payload_client/systemd/offline-kiosk.desktop"
        kiosk_dst = os.path.join(payload_dir, "etc", "xdg", "autostart", "offline-kiosk.desktop")
        shutil.copy2(kiosk_src, kiosk_dst)

        # Inject Kiosk Desktop Shortcut on User Desktop template
        desktop_dir = os.path.join(payload_dir, "etc", "skel", "Desktop")
        os.makedirs(desktop_dir, exist_ok=True)
        desktop_dst = os.path.join(desktop_dir, "offline-kiosk.desktop")
        shutil.copy2(kiosk_src, desktop_dst)
        os.chmod(desktop_dst, 0o755)

        # Inject Init-bottom Copy Script to persist payload files across switch_root
        init_bottom_dir = os.path.join(payload_dir, "scripts", "init-bottom")
        os.makedirs(init_bottom_dir, exist_ok=True)
        init_bottom_src = "/payload_client/init-bottom-copy-payload.sh"
        init_bottom_dst = os.path.join(init_bottom_dir, "copy-payload")
        shutil.copy2(init_bottom_src, init_bottom_dst)
        os.chmod(init_bottom_dst, 0o755)

        # Inject param.conf trigger to execute init-bottom copy-payload hook
        conf_dir = os.path.join(payload_dir, "conf")
        os.makedirs(conf_dir, exist_ok=True)
        conf_src = "/payload_client/conf/param.conf"
        conf_dst = os.path.join(conf_dir, "param.conf")
        shutil.copy2(conf_src, conf_dst)

        # Inject Python site-packages dependencies
        log_to_task(task_id, "[PROGRESS] 35:Injecting python environment dependencies...")
        import sys
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_packages_dst = os.path.join(opt_offline, "backend", "site-packages")
        os.makedirs(site_packages_dst, exist_ok=True)
        packages_to_copy = [
            "fastapi", "pydantic", "pydantic_core", "uvicorn", "starlette",
            "anyio", "h11", "click", "annotated_types", "idna",
            "annotated_doc", "typing_inspection", "watchfiles", "python_multipart", "multipart",
            "serial"
        ]
        for pkg in packages_to_copy:
            pkg_src = f"/usr/local/lib/{py_ver}/site-packages/{pkg}"
            if os.path.isdir(pkg_src):
                shutil.copytree(pkg_src, os.path.join(site_packages_dst, pkg))
            elif os.path.isfile(pkg_src + ".py"):
                shutil.copy2(pkg_src + ".py", os.path.join(site_packages_dst, pkg + ".py"))
        
        shutil.copy2(f"/usr/local/lib/{py_ver}/site-packages/typing_extensions.py", os.path.join(site_packages_dst, "typing_extensions.py"))

        # Write Config JSON
        log_to_task(task_id, "[PROGRESS] 42:Generating kiosk configuration...")
        import models
        settings = db.query(models.Settings).first()
        lang = settings.language if settings else "en"
        kiosk_uuid = generate_kiosk_id()
        server_ips = settings.server_ips if (settings and settings.server_ips) else []
        config_data = {
            "orchestrator_ip": target_ip,
            "available_server_ips": server_ips,
            "auth_token": auth_token,
            "language": lang,
            "kiosk_uuid": kiosk_uuid
        }
        with open(os.path.join(opt_offline, "backend", "config.json"), "w") as f:
            json.dump(config_data, f, indent=4)



        # Save token for validation in routers/iso.py
        with open(os.path.join(CACHE_DIR, "auth_token.txt"), "w") as f:
            f.write(auth_token.strip())

        # Ensure orchestrator SSH key exists and get public key content
        from tasks import ensure_orchestrator_ssh_key, fix_ssh_permissions
        try:
            orch_pub_key = ensure_orchestrator_ssh_key()
            authorized_keys_path = "/root/.ssh/authorized_keys"
            
            # Append to authorized_keys of the borg-server if not present
            if os.path.exists(authorized_keys_path):
                with open(authorized_keys_path, "r") as f:
                    auth_content = f.read()
            else:
                auth_content = ""
                
            if orch_pub_key not in auth_content:
                command_restriction = (
                    f'command="borg serve --restrict-to-path /data/borg/fleet",'
                    f'no-port-forwarding,no-X11-forwarding,no-pty '
                )
                entry = f"{command_restriction}{orch_pub_key}\n"
                with open(authorized_keys_path, "a") as f:
                    f.write(entry)
                fix_ssh_permissions()
                logger.info("Orchestrator SSH public key appended to authorized_keys for kiosk access.")
        except Exception as ke:
            logger.error(f"Failed to setup SSH authorized_keys for kiosk: {ke}")

        # Copy orchestrator SSH keypair to kiosk backend (both private + public)
        shutil.copy2("/root/.ssh/id_ed25519", os.path.join(opt_offline, "backend", "id_ed25519"))
        os.chmod(os.path.join(opt_offline, "backend", "id_ed25519"), 0o600)
        if os.path.exists("/root/.ssh/id_ed25519.pub"):
            shutil.copy2("/root/.ssh/id_ed25519.pub", os.path.join(opt_offline, "backend", "id_ed25519.pub"))

        # 3. Create payload.img
        log_to_task(task_id, "[PROGRESS] 45:Packaging secondary initrd...")
        payload_img = os.path.join(iso_unpacked, "live", "payload.img")
        run_command_with_logging(task_id, f"cd {payload_dir} && find . -print0 | cpio -v --null --create --format=newc | gzip > {payload_img}", shell=True)

        # 4. Modify Bootloaders (GRUB & Syslinux) to load payload.img
        log_to_task(task_id, "[PROGRESS] 60:Updating bootloader configurations...")
        
        # Update GRUB
        grub_cfg = os.path.join(iso_unpacked, "boot", "grub", "grub.cfg")
        if os.path.exists(grub_cfg):
            with open(grub_cfg, "r") as f:
                content = f.read()
            
            lines = []
            timeout_set = False
            for line in content.splitlines():
                if line.strip().startswith("set timeout="):
                    line = "set timeout=5"
                    timeout_set = True
                elif line.strip().startswith("initrd") and "/live/initrd.img" in line and "payload.img" not in line:
                    line = line.rstrip() + " /live/payload.img"
                lines.append(line)
            
            if not timeout_set:
                lines.insert(0, "set timeout=5")
                
            new_content = "\n".join(lines) + "\n"
            
            with open(grub_cfg, "w") as f:
                f.write(new_content)

        # Update Syslinux (isolinux)
        for root_dir, _, files in os.walk(os.path.join(iso_unpacked, "isolinux")):
            for file in files:
                if file.endswith(".cfg"):
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
                            if "initrd=" in line:
                                parts_initrd = line.split("initrd=", 1)
                                val_parts = parts_initrd[1].split(maxsplit=1)
                                val = val_parts[0]
                                rest = " " + val_parts[1] if len(val_parts) > 1 else ""
                                line = parts_initrd[0] + "initrd=" + val + ",/live/payload.img" + rest
                            else:
                                parts_initrd = line.split("initrd", 1)
                                val_parts = parts_initrd[1].split(maxsplit=1)
                                val = val_parts[0]
                                rest = " " + val_parts[1] if len(val_parts) > 1 else ""
                                line = parts_initrd[0] + "initrd" + val + ",/live/payload.img" + rest
                        lines.append(line)
                    new_content = "\n".join(lines) + "\n"
                    
                    with open(filepath, "w") as f:
                        f.write(new_content)

        # 5. Update MD5 Sums
        log_to_task(task_id, "[PROGRESS] 75:Updating ISO checksums...")
        md5_txt = os.path.join(iso_unpacked, "md5sum.txt")
        if os.path.exists(md5_txt):
            run_command_with_logging(task_id, f"cd {iso_unpacked} && find . -type f -not -name md5sum.txt -not -path './isolinux/*' -exec md5sum {{}} \\; > md5sum.txt", shell=True)

        # 6. Repack ISO
        log_to_task(task_id, "[PROGRESS] 85:Repacking Live-USB ISO...")
        output_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        if os.path.exists(output_iso):
            os.remove(output_iso)

        def on_repack_line(line: str) -> None:
            import re
            m = re.search(r"UPDATE\s*:\s*([0-9.]+)%\s*done", line)
            if m:
                try:
                    pct = float(m.group(1))
                    overall_pct = int(85 + (pct * 0.14)) # map 0-100% to 85-99%
                    log_to_task(task_id, f"[PROGRESS] {overall_pct}:Repacking Live-USB ISO...")
                except Exception:
                    pass

        run_command_with_logging(task_id, [
            "xorriso",
            "-as", "mkisofs",
            "-r", "-J", "-joliet-long",
            "-l", "-cache-inodes",
            "-isohybrid-mbr", "/usr/lib/ISOLINUX/isohdpfx.bin",
            "-partition_offset", "16",
            "-A", "Borg-Restore-Technician-Client",
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
            "-eltorito-alt-boot",
            "-e", "boot/grub/efi.img",
            "-no-emul-boot", "-isohybrid-gpt-basdat", "-isohybrid-apm-hfsplus",
            "-o", output_iso,
            iso_unpacked
        ], on_log_line=on_repack_line)

        log_to_task(task_id, "[PROGRESS] 100:Client ISO generated successfully!", status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        log_to_task(task_id, f"Client ISO generation failed: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@celery_app.task(bind=True)
def repack_kiosk_iso_task(self, kiosk_id: int) -> Dict[str, Any]:
    from tasks import log_to_task, run_command_with_logging
    from database import SessionLocal
    from models import TaskLog, Kiosk, Settings
    
    task_id = self.request.id

    db = SessionLocal()
    task_log = TaskLog(id=task_id, task_type="ISO_GEN", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    try:
        kiosk = db.query(Kiosk).filter(Kiosk.id == kiosk_id).first()
        if not kiosk:
            raise Exception(f"Kiosk record {kiosk_id} not found")

        settings = db.query(Settings).first()
        max_kiosk_isos = settings.max_kiosk_isos if settings else 5
        target_ip = settings.orchestrator_ip if settings else "127.0.0.1"
        available_ips = settings.server_ips if (settings and settings.server_ips) else []
        lang = settings.language if settings else "en"

        template_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        if not os.path.exists(template_iso):
            raise Exception("Base template ISO not found. Compile generic Live-USB first.")

        history_dir = os.path.join(CACHE_DIR, "history")
        os.makedirs(history_dir, exist_ok=True)
        server_name = settings.server_name if (settings and settings.server_name) else "Edge.bro"
        
        # Clean up any existing ISO files for this kiosk token first to ensure clean generation and save space
        for file in os.listdir(history_dir):
            if file.endswith(f"-{kiosk.auth_token}.iso") and "-kiosk-" in file:
                try:
                    os.remove(os.path.join(history_dir, file))
                except Exception:
                    pass
                    
        from datetime import datetime
        created_date = datetime.now().strftime("%Y%m%d")
        output_kiosk_iso = os.path.join(history_dir, f"{server_name}-kiosk-{created_date}-{kiosk.auth_token}.iso")

        work_dir = f"/tmp/repack_{kiosk_id}_{task_id}"
        iso_unpacked = os.path.join(work_dir, "iso_unpacked")
        payload_unpacked = os.path.join(work_dir, "payload_unpacked")
        os.makedirs(work_dir, exist_ok=True)

        # 1. Unpack generic template ISO
        log_to_task(task_id, "[PROGRESS] 10:Extracting generic ISO template...")
        run_command_with_logging(task_id, ["xorriso", "-osirrox", "on", "-indev", template_iso, "-extract", "/", iso_unpacked])
        run_command_with_logging(task_id, ["chmod", "-R", "+w", iso_unpacked])

        # 2. Extract payload.img
        log_to_task(task_id, "[PROGRESS] 30:Extracting secondary initrd payload...")
        os.makedirs(payload_unpacked, exist_ok=True)
        payload_img_path = os.path.join(iso_unpacked, "live", "payload.img")
        if not os.path.exists(payload_img_path):
            raise Exception("payload.img not found in template ISO")
            
        subprocess.run(
            f"gzip -dc {payload_img_path} | cpio -idmv",
            shell=True,
            cwd=payload_unpacked,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # 3. Update config.json with kiosk details
        log_to_task(task_id, "[PROGRESS] 50:Updating configuration token...")
        config_path = os.path.join(payload_unpacked, "opt", "offline-client", "backend", "config.json")
        config_data = {}
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config_data = json.load(f)
                
        config_data["auth_token"] = kiosk.auth_token
        config_data["kiosk_uuid"] = kiosk.uuid
        config_data["available_server_ips"] = available_ips
        config_data["orchestrator_ip"] = target_ip
        config_data["language"] = lang

        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=4)

        # 4. Repack payload.img
        log_to_task(task_id, "[PROGRESS] 70:Repacking secondary initrd...")
        subprocess.run(
            f"find . -print0 | cpio -o -H newc --null | gzip > {payload_img_path}",
            shell=True,
            cwd=payload_unpacked,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # 5. Compile custom ISO
        log_to_task(task_id, "[PROGRESS] 80:Compiling custom kiosk ISO...")
        run_command_with_logging(task_id, [
            "xorriso",
            "-as", "mkisofs",
            "-r", "-J", "-joliet-long",
            "-l", "-cache-inodes",
            "-isohybrid-mbr", "/usr/lib/ISOLINUX/isohdpfx.bin",
            "-partition_offset", "16",
            "-A", "Borg-Restore-Technician-Client",
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
            "-eltorito-alt-boot",
            "-e", "boot/grub/efi.img",
            "-no-emul-boot", "-isohybrid-gpt-basdat", "-isohybrid-apm-hfsplus",
            "-o", output_kiosk_iso,
            iso_unpacked
        ])

        # 6. Run history pruning
        log_to_task(task_id, "[PROGRESS] 95:Pruning old repository ISOs...")
        iso_files = []
        for file in os.listdir(history_dir):
            if file.endswith(".iso") and "-kiosk-" in file:
                filepath = os.path.join(history_dir, file)
                iso_files.append((filepath, os.path.getmtime(filepath)))
                
        # Sort by mtime ascending (oldest first)
        iso_files.sort(key=lambda x: x[1])
        if len(iso_files) > max_kiosk_isos:
            to_delete = len(iso_files) - max_kiosk_isos
            for i in range(to_delete):
                try:
                    os.remove(iso_files[i][0])
                    logger.info(f"Pruned old kiosk ISO: {iso_files[i][0]}")
                except Exception as pe:
                    logger.error(f"Failed to prune old ISO {iso_files[i][0]}: {pe}")

        log_to_task(task_id, "[PROGRESS] 100:Kiosk custom ISO generated successfully!", status="SUCCESS")
        return {"status": "SUCCESS", "iso_path": output_kiosk_iso}

    except Exception as e:
        logger.error(f"Kiosk ISO repackaging failed: {e}")
        log_to_task(task_id, f"Kiosk ISO repackaging failed: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        db.close()

