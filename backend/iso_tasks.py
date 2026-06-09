import os
import shutil
import subprocess
import json
import logging
import hashlib
from tasks import celery_app
from typing import Dict, Any

logger = logging.getLogger(__name__)

BASE_ISO_URL = "https://cdimage.debian.org/cdimage/weekly-live-builds/amd64/iso-hybrid/debian-live-testing-amd64-xfce.iso"
CACHE_DIR = "/opt/data/iso_cache"
BASE_ISO_PATH = os.path.join(CACHE_DIR, "base.iso")
BASE_ISO_PATH_TMP = BASE_ISO_PATH + ".tmp"

@celery_app.task(bind=True)
def download_base_iso_task(self, url: str = None) -> Dict[str, Any]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(BASE_ISO_PATH):
        if os.path.getsize(BASE_ISO_PATH) > 1000 * 1024 * 1024:
            return {"status": "SUCCESS", "message": "Base ISO already cached."}
        else:
            os.remove(BASE_ISO_PATH)
    
    download_url = url if url else BASE_ISO_URL
    is_official = download_url == BASE_ISO_URL

    try:
        logger.info(f"Downloading Base ISO from {download_url}...")
        
        # Fetch the real size of the ISO
        try:
            import urllib.request
            req = urllib.request.Request(download_url, method='HEAD')
            with urllib.request.urlopen(req) as response:
                content_length = response.getheader('Content-Length')
                if content_length:
                    with open(os.path.join(CACHE_DIR, "base.iso.size"), "w") as f:
                        f.write(content_length)
        except Exception as e:
            logger.warning(f"Could not fetch ISO size dynamically: {e}")

        # Use curl to download the file safely to a temporary path with fail-fast (-f)
        subprocess.check_call(["curl", "-f", "-L", "-o", BASE_ISO_PATH_TMP, download_url])

        if is_official:
            logger.info("Downloading SHA512SUMS for validation...")
            sums_url = download_url.rsplit('/', 1)[0] + "/SHA512SUMS"
            sums_path = os.path.join(CACHE_DIR, "SHA512SUMS")
            subprocess.check_call(["curl", "-f", "-sL", "-o", sums_path, sums_url])
            
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
        if os.path.exists(BASE_ISO_PATH_TMP):
            os.remove(BASE_ISO_PATH_TMP)
        return {"status": "FAILED", "error": str(e)}

@celery_app.task(bind=True)
def generate_client_iso_task(self, target_ip: str, auth_token: str) -> Dict[str, Any]:
    from tasks import log_to_task
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
            subprocess.check_call(["curl", "-L", "-o", BASE_ISO_PATH, BASE_ISO_URL])

    work_dir = f"/tmp/iso_gen_{task_id}"
    iso_unpacked = os.path.join(work_dir, "iso_unpacked")
    payload_dir = os.path.join(work_dir, "payload_initrd")
    
    try:
        # 1. Unpack Base ISO
        log_to_task(task_id, "[PROGRESS] 10:Unpacking base ISO...")
        os.makedirs(work_dir, exist_ok=True)
        subprocess.check_call(["xorriso", "-osirrox", "on", "-indev", base_iso_to_use, "-extract", "/", iso_unpacked])

        # Make iso_unpacked writable
        subprocess.check_call(["chmod", "-R", "+w", iso_unpacked])

        # 2. Prepare Secondary Initrd Payload
        log_to_task(task_id, "[PROGRESS] 30:Injecting payload and configurations...")
        opt_offline = os.path.join(payload_dir, "opt", "offline-client")
        os.makedirs(os.path.join(opt_offline, "backend", "core"), exist_ok=True)
        os.makedirs(os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants"), exist_ok=True)
        os.makedirs(os.path.join(payload_dir, "etc", "xdg", "autostart"), exist_ok=True)

        # Copy Payload Backend
        subprocess.check_call(f"cp -r /payload_client/backend/* {os.path.join(opt_offline, 'backend')}/", shell=True)
        
        # Inject Shared Disk Ops Module
        shutil.copy2("/app/core/disk_ops.py", os.path.join(opt_offline, "backend", "core", "disk_ops.py"))

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

        # Inject Kiosk Desktop Entry
        kiosk_src = "/payload_client/systemd/offline-kiosk.desktop"
        kiosk_dst = os.path.join(payload_dir, "etc", "xdg", "autostart", "offline-kiosk.desktop")
        shutil.copy2(kiosk_src, kiosk_dst)

        # Inject Init-bottom Copy Script to persist payload files across switch_root
        init_bottom_dir = os.path.join(payload_dir, "scripts", "init-bottom")
        os.makedirs(init_bottom_dir, exist_ok=True)
        init_bottom_src = "/payload_client/init-bottom-copy-payload.sh"
        init_bottom_dst = os.path.join(init_bottom_dir, "copy-payload")
        shutil.copy2(init_bottom_src, init_bottom_dst)
        os.chmod(init_bottom_dst, 0o755)

        # Inject Python site-packages dependencies
        site_packages_dst = os.path.join(opt_offline, "backend", "site-packages")
        os.makedirs(site_packages_dst, exist_ok=True)
        packages_to_copy = [
            "fastapi", "pydantic", "pydantic_core", "uvicorn", "starlette",
            "anyio", "h11", "click", "annotated_types", "idna"
        ]
        for pkg in packages_to_copy:
            pkg_src = f"/usr/local/lib/python3.11/site-packages/{pkg}"
            if os.path.isdir(pkg_src):
                shutil.copytree(pkg_src, os.path.join(site_packages_dst, pkg))
            elif os.path.isfile(pkg_src + ".py"):
                shutil.copy2(pkg_src + ".py", os.path.join(site_packages_dst, pkg + ".py"))
        
        # Also copy typing_extensions.py
        shutil.copy2("/usr/local/lib/python3.11/site-packages/typing_extensions.py", os.path.join(site_packages_dst, "typing_extensions.py"))

        # Write Config JSON
        config_data = {
            "orchestrator_ip": target_ip,
            "auth_token": auth_token
        }
        with open(os.path.join(opt_offline, "backend", "config.json"), "w") as f:
            json.dump(config_data, f)

        # 3. Create payload.img
        log_to_task(task_id, "[PROGRESS] 45:Packaging secondary initrd...")
        payload_img = os.path.join(iso_unpacked, "live", "payload.img")
        subprocess.check_call(f"cd {payload_dir} && find . -print0 | cpio --null --create --format=newc | gzip > {payload_img}", shell=True)

        # 4. Modify Bootloaders (GRUB & Syslinux) to load payload.img
        log_to_task(task_id, "[PROGRESS] 60:Updating bootloader configurations...")
        
        # Update GRUB
        grub_cfg = os.path.join(iso_unpacked, "boot", "grub", "grub.cfg")
        if os.path.exists(grub_cfg):
            with open(grub_cfg, "r") as f:
                content = f.read()
            
            lines = []
            for line in content.splitlines():
                if line.strip().startswith("initrd") and "/live/initrd.img" in line and "payload.img" not in line:
                    line = line.rstrip() + " /live/payload.img"
                lines.append(line)
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
                        if "initrd" in line and "/live/initrd.img" in line and "payload.img" not in line:
                            if "initrd=" in line:
                                parts = line.split("initrd=", 1)
                                val_parts = parts[1].split(maxsplit=1)
                                val = val_parts[0]
                                rest = " " + val_parts[1] if len(val_parts) > 1 else ""
                                line = parts[0] + "initrd=" + val + ",/live/payload.img" + rest
                            else:
                                parts = line.split("initrd", 1)
                                val_parts = parts[1].split(maxsplit=1)
                                val = val_parts[0]
                                rest = " " + val_parts[1] if len(val_parts) > 1 else ""
                                line = parts[0] + "initrd" + val + ",/live/payload.img" + rest
                        lines.append(line)
                    new_content = "\n".join(lines) + "\n"
                    
                    with open(filepath, "w") as f:
                        f.write(new_content)

        # 5. Update MD5 Sums
        log_to_task(task_id, "[PROGRESS] 75:Updating ISO checksums...")
        md5_txt = os.path.join(iso_unpacked, "md5sum.txt")
        if os.path.exists(md5_txt):
            subprocess.check_call(f"cd {iso_unpacked} && find . -type f -not -name md5sum.txt -not -path './isolinux/*' -exec md5sum {{}} \\; > md5sum.txt", shell=True)

        # 6. Repack ISO
        log_to_task(task_id, "[PROGRESS] 85:Repacking Live-USB ISO...")
        output_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        if os.path.exists(output_iso):
            os.remove(output_iso)

        subprocess.check_call([
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
        ])

        log_to_task(task_id, "[PROGRESS] 100:Client ISO generated successfully!", status="SUCCESS")
        return {"status": "SUCCESS"}

    except Exception as e:
        log_to_task(task_id, f"Client ISO generation failed: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
