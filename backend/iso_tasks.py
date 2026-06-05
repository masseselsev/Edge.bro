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
def download_base_iso_task(self) -> Dict[str, Any]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(BASE_ISO_PATH):
        if os.path.getsize(BASE_ISO_PATH) > 1000 * 1024 * 1024:
            return {"status": "SUCCESS", "message": "Base ISO already cached."}
        else:
            os.remove(BASE_ISO_PATH)
    
    try:
        logger.info(f"Downloading Base ISO from {BASE_ISO_URL}...")
        
        # Fetch the real size of the ISO
        try:
            import urllib.request
            req = urllib.request.Request(BASE_ISO_URL, method='HEAD')
            with urllib.request.urlopen(req) as response:
                content_length = response.getheader('Content-Length')
                if content_length:
                    with open(os.path.join(CACHE_DIR, "base.iso.size"), "w") as f:
                        f.write(content_length)
        except Exception as e:
            logger.warning(f"Could not fetch ISO size dynamically: {e}")

        # Use curl to download the file safely to a temporary path with fail-fast (-f)
        subprocess.check_call(["curl", "-f", "-L", "-o", BASE_ISO_PATH_TMP, BASE_ISO_URL])

        logger.info("Downloading SHA512SUMS for validation...")
        sums_url = BASE_ISO_URL.rsplit('/', 1)[0] + "/SHA512SUMS"
        sums_path = os.path.join(CACHE_DIR, "SHA512SUMS")
        subprocess.check_call(["curl", "-f", "-sL", "-o", sums_path, sums_url])
        
        iso_filename = os.path.basename(BASE_ISO_URL)
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
            
        os.rename(BASE_ISO_PATH_TMP, BASE_ISO_PATH)
        return {"status": "SUCCESS", "message": "Base ISO downloaded and verified successfully."}
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

    if not os.path.exists(BASE_ISO_PATH):
        log_to_task(task_id, "[PROGRESS] 5:Downloading Base ISO...")
        subprocess.check_call(["curl", "-L", "-o", BASE_ISO_PATH, BASE_ISO_URL])

    work_dir = f"/tmp/iso_gen_{task_id}"
    iso_unpacked = os.path.join(work_dir, "iso_unpacked")
    payload_dir = os.path.join(work_dir, "payload_initrd")
    
    try:
        # 1. Unpack Base ISO
        log_to_task(task_id, "[PROGRESS] 10:Unpacking base ISO...")
        os.makedirs(work_dir, exist_ok=True)
        subprocess.check_call(["xorriso", "-osirrox", "on", "-indev", BASE_ISO_PATH, "-extract", "/", iso_unpacked])

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

        # Inject Systemd Service
        svc_src = "/payload_client/systemd/offline-backend.service"
        svc_dst = os.path.join(payload_dir, "etc", "systemd", "system", "offline-backend.service")
        shutil.copy2(svc_src, svc_dst)
        os.symlink("/etc/systemd/system/offline-backend.service", os.path.join(payload_dir, "etc", "systemd", "system", "multi-user.target.wants", "offline-backend.service"))

        # Inject Kiosk Desktop Entry
        kiosk_src = "/payload_client/systemd/offline-kiosk.desktop"
        kiosk_dst = os.path.join(payload_dir, "etc", "xdg", "autostart", "offline-kiosk.desktop")
        shutil.copy2(kiosk_src, kiosk_dst)

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
            content = content.replace("initrd /live/initrd.img", "initrd /live/initrd.img /live/payload.img")
            with open(grub_cfg, "w") as f:
                f.write(content)

        # Update Syslinux (isolinux)
        for root_dir, _, files in os.walk(os.path.join(iso_unpacked, "isolinux")):
            for file in files:
                if file.endswith(".cfg"):
                    filepath = os.path.join(root_dir, file)
                    with open(filepath, "r") as f:
                        content = f.read()
                    content = content.replace("initrd=/live/initrd.img", "initrd=/live/initrd.img,/live/payload.img")
                    with open(filepath, "w") as f:
                        f.write(content)

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
