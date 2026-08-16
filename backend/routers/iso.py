import os
import json
import time
import uuid
import shutil
import subprocess
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional

from iso_tasks import generate_client_iso_task, download_base_iso_task, CACHE_DIR, BASE_ISO_URL
from models import TaskLog
from database import SessionLocal, get_db
from sqlalchemy.orm import Session
from auth import require_admin, require_kiosk_or_admin
from core import compression, repo_paths
from core.repo_lock import LOCK_WAIT_SECONDS
from core.borg_local import borg_kwargs
import models
import schemas
from core.clock import utcnow
from core.redis_client import make_client as make_redis_client

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = make_redis_client(REDIS_URL)

router = APIRouter()

def check_concurrent_iso_task(db: Session):
    # 1. Check download lock
    lock_path = os.path.join(CACHE_DIR, "download.lock")
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    base_exists = os.path.exists(base_iso_path) and os.path.getsize(base_iso_path) > 1000 * 1024 * 1024
    if not base_exists and os.path.exists(lock_path):
        raise HTTPException(
            status_code=400,
            detail="Base ISO download is currently in progress. Please wait for it to complete."
        )

    # 2. Check running ISO generation or repack tasks
    active_task = db.query(models.TaskLog).filter(
        models.TaskLog.task_type == "ISO_GEN",
        models.TaskLog.status == "RUNNING"
    ).first()
    if active_task:
        from datetime import datetime
        age = utcnow() - active_task.created_at
        if age.total_seconds() < 45 * 60:
            raise HTTPException(
                status_code=400,
                detail="An ISO generation or repack task is already in progress. Please wait for it to complete."
            )
        else:
            # Mark stale task as FAILED
            active_task.status = "FAILED"
            active_task.log_output += "\n[SYSTEM] Task assumed dead after 45 minutes timeout."
            db.commit()

class GenerateIsoRequest(BaseModel):
    target_ip: str
    auth_token: str

class BaseIsoDownloadRequest(BaseModel):
    url: Optional[str] = None

@router.post("/generate")
def generate_iso(req: GenerateIsoRequest, db: Session = Depends(get_db), auth = Depends(require_admin)):
    check_concurrent_iso_task(db)
    try:
        # Save the orchestrator_ip in the settings database so it is preserved
        settings = db.query(models.Settings).first()
        if not settings:
            settings = models.Settings(orchestrator_ip=req.target_ip)
            db.add(settings)
        else:
            settings.orchestrator_ip = req.target_ip
        db.commit()

        task = generate_client_iso_task.delay(req.target_ip, req.auth_token)
        return {"task_id": task.id, "message": "ISO generation task started."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/download_base")
def trigger_base_download(req: BaseIsoDownloadRequest = None, auth = Depends(require_admin)):
    # Prevent concurrent duplicate download tasks if one is already running
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    base_exists = os.path.exists(base_iso_path) and os.path.getsize(base_iso_path) > 1000 * 1024 * 1024
    lock_path = os.path.join(CACHE_DIR, "download.lock")
    
    if not base_exists and os.path.exists(lock_path):
        raise HTTPException(status_code=400, detail="Base ISO download is already in progress.")

    if not base_exists:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(lock_path, "w") as f:
            f.write("LOCKED")

    try:
        url = req.url if req else None
        task = download_base_iso_task.delay(url=url)
        return {"task_id": task.id, "message": "Base ISO download started."}
    except Exception as e:
        if not base_exists and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload_base")
def upload_base_iso(file: UploadFile = File(...), auth = Depends(require_admin)):
    if not file.filename.endswith(".iso"):
        raise HTTPException(status_code=400, detail="Only .iso files are allowed")
    
    os.makedirs(CACHE_DIR, exist_ok=True)
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    
    try:
        with open(base_iso_path, "wb") as f:
            import shutil
            shutil.copyfileobj(file.file, f)
        
        # Save actual size for progress UI
        with open(os.path.join(CACHE_DIR, "base.iso.size"), "w") as f:
            f.write(str(os.path.getsize(base_iso_path)))
            
        return {"status": "SUCCESS", "message": "Base ISO uploaded successfully."}
    except Exception as e:
        if os.path.exists(base_iso_path):
            os.remove(base_iso_path)
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

@router.delete("/base")
def clear_base_iso(auth = Depends(require_admin)):
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    tmp_iso_path = os.path.join(CACHE_DIR, "base.iso.tmp")
    size_file = os.path.join(CACHE_DIR, "base.iso.size")
    client_iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    if os.path.exists(base_iso_path):
        os.remove(base_iso_path)
    if os.path.exists(tmp_iso_path):
        os.remove(tmp_iso_path)
    if os.path.exists(size_file):
        os.remove(size_file)
    if os.path.exists(client_iso_path):
        os.remove(client_iso_path)
    return {"status": "SUCCESS", "message": "Base ISO cache cleared."}

@router.get("/download")
def download_iso(auth = Depends(require_admin)):
    iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    if not os.path.exists(iso_path):
        raise HTTPException(status_code=404, detail="Client ISO not found. Generate it first.")
    
    return FileResponse(
        path=iso_path,
        filename="Borg_Restore_Technician_Client.iso",
        media_type="application/x-iso9660-image"
    )

@router.get("/status")
def get_iso_status(auth = Depends(require_admin)):
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    base_exists = os.path.exists(base_iso_path) and os.path.getsize(base_iso_path) > 1000 * 1024 * 1024
    tmp_path = os.path.join(CACHE_DIR, "base.iso.tmp")
    lock_path = os.path.join(CACHE_DIR, "download.lock")
    client_iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    client_exists = os.path.exists(client_iso_path)
    
    client_created_at = None
    if client_exists:
        try:
            mtime = os.path.getmtime(client_iso_path)
            from datetime import datetime, timezone
            client_created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except:
            pass
    
    progress = -1
    speed_str = ""
    if not base_exists and os.path.exists(lock_path):
        progress = 0
        if os.path.exists(tmp_path):
            size = os.path.getsize(tmp_path)
            total_size = 4139925504
            size_file = os.path.join(CACHE_DIR, "base.iso.size")
            if os.path.exists(size_file):
                try:
                    with open(size_file, "r") as f:
                        total_size = int(f.read().strip())
                except:
                    pass
            progress = min(100, int((size / total_size) * 100))
            
            # Speed calculation logic using Redis state
            try:
                current_time = time.time()
                last_data_raw = redis_client.get("base_iso_download_last")
                if last_data_raw:
                    last_data = json.loads(last_data_raw)
                    last_size = last_data.get("size", 0)
                    last_time = last_data.get("time", 0.0)
                    
                    time_diff = current_time - last_time
                    if time_diff >= 0.5:
                        size_diff = size - last_size
                        if size_diff >= 0:
                            speed_bps = size_diff / time_diff
                            if speed_bps >= 1024 * 1024:
                                speed_str = f"{speed_bps / (1024 * 1024):.1f} MB/s"
                            elif speed_bps >= 1024:
                                speed_str = f"{speed_bps / 1024:.1f} KB/s"
                            else:
                                speed_str = f"{speed_bps:.0f} B/s"
                            
                            redis_client.setex("base_iso_download_speed", 10, speed_str)
                            redis_client.setex("base_iso_download_last", 60, json.dumps({"size": size, "time": current_time}))
                else:
                    redis_client.setex("base_iso_download_last", 60, json.dumps({"size": size, "time": current_time}))
            except Exception:
                pass
                
        if not speed_str:
            try:
                cached_speed = redis_client.get("base_iso_download_speed")
                if cached_speed:
                    speed_str = cached_speed.decode('utf-8')
            except:
                pass
        
    import shutil
    try:
        total, used, free = shutil.disk_usage(CACHE_DIR)
    except Exception:
        total, free = 0, 0

    # Check if an ISO_GEN rebuild task is actively running
    from sqlalchemy.orm import Session as _Session
    _db = SessionLocal()
    try:
        active_regen = _db.query(models.TaskLog).filter(
            models.TaskLog.task_type == "ISO_GEN",
            models.TaskLog.status == "RUNNING"
        ).first()
        client_iso_rebuilding = bool(active_regen) and client_exists
    finally:
        _db.close()

    # Check payload hash mismatch (stale) — only when not already rebuilding
    client_iso_stale = False
    if client_exists and not client_iso_rebuilding:
        try:
            from payload_hash import compute_payload_hash, read_stored_hash
            stored = read_stored_hash()
            client_iso_stale = compute_payload_hash() != (stored or "")
        except Exception:
            pass

    return {
        "base_iso_cached": base_exists or client_exists,
        "base_iso_official_url": BASE_ISO_URL,
        "base_iso_progress": progress,
        "base_iso_speed": speed_str,
        "client_iso_ready": client_exists,
        "client_iso_created_at": client_created_at,
        "client_iso_rebuilding": client_iso_rebuilding,
        "client_iso_stale": client_iso_stale,
        "iso_cache_free_space": free,
        "iso_cache_total_space": total
    }


import subprocess
import logging
import urllib.request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

def download_kiosk_packages(os_version: str, target_dir: str):
    """
    Downloads edge-hasp-eoawt3 and edge-aksusbd deb packages for the target
    OS version from vitcompany repo.
    """
    import urllib.request
    import re
    
    # Map OS version to release name
    release = "bookworm"
    if not os_version:
        release = "bookworm"
    elif "12" in os_version or "bookworm" in os_version.lower():
        release = "bookworm"
    elif "11" in os_version or "bullseye" in os_version.lower():
        release = "bullseye"
    elif "10" in os_version or "buster" in os_version.lower():
        release = "buster"
        
    os.makedirs(target_dir, exist_ok=True)
    
    # Fetch Packages index
    base_url = f"http://edge.vitcompany.com/repo/{release}/stable"
    packages_url = f"{base_url}/dists/{release}/main/binary-amd64/Packages"
    
    try:
        req = urllib.request.Request(packages_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8', errors='ignore')
            
        # Parse Packages to find Filename for edge-hasp-eoawt3 and edge-aksusbd
        packages = content.split("\n\n")
        filenames = []
        for pkg_info in packages:
            lines = pkg_info.strip().split("\n")
            pkg_name = None
            pkg_file = None
            for line in lines:
                if line.startswith("Package:"):
                    pkg_name = line.split(":", 1)[1].strip()
                elif line.startswith("Filename:"):
                    pkg_file = line.split(":", 1)[1].strip()
            if pkg_name in ("edge-hasp-eoawt3", "edge-aksusbd") and pkg_file:
                filenames.append(pkg_file)
                
        if not filenames:
            # Fallback filenames if Packages file couldn't be parsed
            filenames = [
                "pool/main/e/edge-hasp-eoawt3/edge-hasp-eoawt3_3.2.0-2_amd64.deb",
            ]
            
        for filename in set(filenames):
            deb_url = f"{base_url}/{filename}"
            deb_name = os.path.basename(filename)
            dest_path = os.path.join(target_dir, deb_name)
            
            # Download file
            req_deb = urllib.request.Request(deb_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_deb, timeout=15) as deb_resp:
                with open(dest_path, "wb") as f:
                    f.write(deb_resp.read())
                    
        logger.info(f"Successfully downloaded offline packages for {release} into {target_dir}")
    except Exception as e:
        logger.warning(f"Failed to download offline packages for {release}: {e}")


#: Where the repack builds the mini-repo the kiosk downloads, and the private
#: HOME borg gets so its cache and security history do not collide with the
#: orchestrator's own.
DOWNLOAD_TEMP_PARENT = "/data/borg/tmp"
DOWNLOAD_TEMP_PREFIX = "download_"
DOWNLOAD_HOME_PREFIX = "/tmp/borg_home_"

#: A build older than this cannot still be serving anyone. A repack takes
#: minutes and the kiosk's own read timeout is five, so half a day is far past
#: any download that is still alive, while still reclaiming the space the same
#: day rather than at the next restart.
STALE_DOWNLOAD_AGE_SECONDS = 12 * 3600


def sweep_stale_download_temps(max_age_seconds: int = STALE_DOWNLOAD_AGE_SECONDS) -> int:
    """Delete mini-repo builds that no download can still be reading.

    The normal cleanup is the `finally` in the streaming generator, and it
    handles the normal endings — including a client that disconnects mid
    transfer, which closes the generator. What it cannot handle is the process
    not living to run it: a backend restarted or OOM-killed during a download
    leaves the whole build behind, and each one is as large as the archive.
    Nine of them had accumulated on a three-node deployment.

    Age is the only safe discriminator. There is no registry of live downloads,
    and a build's directory looks identical whether it is being streamed or was
    abandoned an hour ago.

    Never raises: this is opportunistic housekeeping on the way to doing the
    thing the caller actually asked for.
    """
    import glob
    import time as _time

    removed = 0
    cutoff = _time.time() - max_age_seconds
    patterns = (
        os.path.join(DOWNLOAD_TEMP_PARENT, f"{DOWNLOAD_TEMP_PREFIX}*"),
        f"{DOWNLOAD_HOME_PREFIX}*",
    )
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                if os.path.getmtime(path) > cutoff:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            except OSError:
                continue
    return removed


@router.get("/repos/{hostname}/download")
def download_repo(
    hostname: str,
    token: str,
    archives: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    auth = Depends(require_kiosk_or_admin)
):
    # Before building another one, reclaim any that were abandoned. Doing it
    # here as well as at startup catches the case where the process survives
    # but the download did not.
    sweep_stale_download_temps()

    node = db.query(models.Node).filter(models.Node.hostname == hostname).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    shared_repo = repo_paths.repo_path_for_node(node)
    if not repo_paths.is_initialized(shared_repo):
        raise HTTPException(status_code=404, detail="Shared repository not found")

    # Resolved before the repack so the mini-repo is written the same way the
    # archives already are — see core/compression.
    node_compression = compression.for_node(
        node,
        group=db.query(models.BackupGroup).filter(
            models.BackupGroup.id == node.group_id
        ).first() if node.group_id else None,
        settings=db.query(models.Settings).first(),
    )

    # Get the list of archives for this node from the shared repository
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    
    try:
        list_res = subprocess.run(
            ["borg", "list", "--lock-wait", str(LOCK_WAIT_SECONDS), "--json", shared_repo],
            env=env,
            capture_output=True,
            text=True,
            **borg_kwargs(shared_repo, env),
            check=True
        )
        all_archives = json.loads(list_res.stdout).get("archives", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query shared repository: {str(e)}")

    node_archives = [a["name"] for a in all_archives if a["name"].startswith(f"{hostname}-")]
    if archives:
        allowed_archives = [a.strip() for a in archives.split(",") if a.strip()]
        node_archives = [a for a in node_archives if a in allowed_archives]

    if not node_archives:
        raise HTTPException(status_code=404, detail="No backups found for this node matching selection")

    # Create isolated temporary repository path on NVMe disk under /data/borg/tmp
    temp_uuid = uuid.uuid4().hex
    temp_parent = f"/data/borg/tmp/download_{temp_uuid}"
    temp_repo_dir = os.path.join(temp_parent, hostname)
    os.makedirs(temp_repo_dir, exist_ok=True)
    
    # Use distinct temporary HOME directory to avoid cache and security history lockups/conflicts
    temp_home = f"/tmp/borg_home_{temp_uuid}"
    env["HOME"] = temp_home

    # Initialize the temporary repository first while the directory is empty
    try:
        subprocess.run(
            ["borg", "init", "--encryption=repokey", temp_repo_dir],
            env=env,
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(temp_parent, ignore_errors=True)
        shutil.rmtree(temp_home, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to initialize temporary repository: {e.stderr.decode()}")

    # Fetch offline Sentinel package cache into repository folder after initialization
    packages_dir = os.path.join(temp_repo_dir, "packages")
    download_kiosk_packages(node.os_version, packages_dir)

    # Transfer only the node's archives from shared repository to temporary repository
    # Only the export side reads the fleet repository, so only it needs that
    # repository's identity; the temporary repository was created by this
    # process and stays owned by it.
    export_env = env.copy()
    export_kwargs = borg_kwargs(shared_repo, export_env)
    try:
        for archive in node_archives:
            export_proc = subprocess.Popen(
                ["borg", "export-tar", "--lock-wait", str(LOCK_WAIT_SECONDS), f"{shared_repo}::{archive}", "-"],
                env=export_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **export_kwargs
            )
            import_proc = subprocess.Popen(
                # Same compression the archive was written with. Without it
                # borg falls back to its default of lz4, so a zstd:3 archive is
                # decompressed and recompressed worse on the way into the
                # mini-repo — 1.46 GiB of source became 1.81 GiB of repository,
                # every byte of which the technician then downloads.
                ["borg", "import-tar",
                 "--compression", compression.to_borg_arg(node_compression),
                 f"{temp_repo_dir}::{archive}", "-"],
                env=env,
                stdin=export_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Allow export_proc to receive SIGPIPE if import_proc exits early
            export_proc.stdout.close()
            
            _, import_err = import_proc.communicate()
            _, export_err = export_proc.communicate()
            
            if export_proc.returncode != 0 or import_proc.returncode != 0:
                err_msg = (
                    f"Archive copy failed for {archive}. "
                    f"Export status: {export_proc.returncode}, Import status: {import_proc.returncode}. "
                    f"Export error: {export_err.decode()}, Import error: {import_err.decode()}"
                )
                raise Exception(err_msg)
    except Exception as e:
        shutil.rmtree(temp_parent, ignore_errors=True)
        shutil.rmtree(temp_home, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to construct repository download: {str(e)}")

    # Calculate total size of the compiled temporary repository
    total_size = 0
    try:
        du_out = subprocess.check_output(["du", "-sb", temp_repo_dir]).decode().strip()
        total_size = int(du_out.split()[0])
    except Exception:
        for root, dirs, files in os.walk(temp_repo_dir):
            for file in files:
                total_size += os.path.getsize(os.path.join(root, file))

    def tar_generator():
        try:
            proc = subprocess.Popen(
                ["tar", "-cf", "-", "-C", temp_parent, hostname],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
            proc.wait()
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)
            shutil.rmtree(temp_home, ignore_errors=True)

    # Format size
    def get_format_size(size_bytes):
        if size_bytes == 0: return "0 B"
        import math
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    formatted_size = get_format_size(total_size)
    kiosk_name = "Kiosk"
    if isinstance(auth, models.Kiosk):
        if auth.name == "Offline Restore Client":
            kiosk_name = "Kiosk: Offline Restore Client"
        else:
            kiosk_name = f"Kiosk: {auth.name} (KioskID: {auth.kiosk_id})" if auth.name else f"Kiosk: {auth.kiosk_id}"
    elif isinstance(auth, models.User):
        kiosk_name = f"Admin: {auth.username}"

    from database import log_user_action
    log_user_action(db, kiosk_name, "Download Repository", f"Downloaded archive/repository for node '{hostname}' (Size: {formatted_size})", request)

    return StreamingResponse(
        tar_generator(),
        media_type="application/x-tar",
        headers={
            "Content-Disposition": f"attachment; filename={hostname}.tar",
            "X-Total-Size": str(total_size)
        }
    )


@router.post("/kiosks/issue")
def issue_kiosk(req: schemas.KioskIssueRequest, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    check_concurrent_iso_task(db)
    from routers.kiosks import generate_kiosk_key, generate_kiosk_token, generate_kiosk_id
    import secrets

    # Generate auth token (kiosk token style, e.g. AB1234)
    auth_token = generate_kiosk_token()
    while db.query(models.Kiosk).filter(models.Kiosk.auth_token == auth_token).first():
        auth_token = generate_kiosk_token()

    # Generate a unique memorable kiosk ID (format: KS1234)
    kiosk_id_val = generate_kiosk_id()
    while db.query(models.Kiosk).filter(models.Kiosk.kiosk_id == kiosk_id_val).first():
        kiosk_id_val = generate_kiosk_id()

    # Generate pairing key (connection token style, e.g. 1234AB)
    pairing_key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == pairing_key).first():
        pairing_key = generate_kiosk_key()

    settings = db.query(models.Settings).first()
    default_target_ip = settings.orchestrator_ip if settings else "127.0.0.1"
    target_ip_val = req.target_ip if req.target_ip else default_target_ip

    # Create kiosk record directly approved
    kiosk = models.Kiosk(
        name=req.name,
        kiosk_id=kiosk_id_val,
        key=pairing_key,
        contact=req.contact,
        comment=req.comment,
        status="APPROVED",
        auth_token=auth_token,
        target_ip=target_ip_val,
        rebuild_required=False
    )
    db.add(kiosk)
    db.commit()
    db.refresh(kiosk)

    # Trigger repack Celery task
    from iso_tasks import repack_kiosk_iso_task
    task = repack_kiosk_iso_task.delay(kiosk.id)
    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Issue Kiosk", f"Issued kiosk {kiosk.kiosk_id} (token: {kiosk.auth_token}) for recipient {kiosk.name}", request)

    # Return kiosk response + task_id to follow progress
    return {"kiosk": kiosk, "task_id": task.id}


@router.post("/kiosks/{id}/update_ip")
def update_kiosk_ip(id: int, req: schemas.KioskIpUpdateRequest, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    kiosk.target_ip = req.target_ip.strip()
    kiosk.rebuild_required = True
    db.commit()
    db.refresh(kiosk)
    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Update Kiosk target IP", f"Updated target IP for kiosk {kiosk.kiosk_id} to {kiosk.target_ip}. Marked rebuild required.", request)
    
    return {"message": "Kiosk target IP updated", "kiosk": kiosk}


@router.post("/kiosks/{id}/recreate")
def recreate_kiosk_iso(id: int, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    check_concurrent_iso_task(db)
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    from iso_tasks import repack_kiosk_iso_task
    task = repack_kiosk_iso_task.delay(kiosk.id)
    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Recreate Kiosk ISO", f"Triggered recreation of Kiosk {kiosk.kiosk_id} ISO (token: {kiosk.auth_token})", request)

    return {"task_id": task.id, "message": "Recreation task started"}


@router.get("/kiosks/{id}/download")
def download_kiosk_iso(id: int, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    if not kiosk.auth_token:
        raise HTTPException(status_code=400, detail="Kiosk does not have a dynamic auth token")
        
    settings = db.query(models.Settings).first()
    server_name = settings.server_name if (settings and settings.server_name) else "edge-bro"

    from iso_tasks import CACHE_DIR
    filename = None
    history_dir = os.path.join(CACHE_DIR, "history")
    if os.path.exists(history_dir):
        suffix = f"-{kiosk.auth_token}.iso"
        for file in os.listdir(history_dir):
            if file.endswith(suffix) and "-kiosk-" in file:
                filename = file
                break
    if not filename:
        raise HTTPException(status_code=404, detail="ISO image has been pruned from cache. Re-create it first.")
    iso_path = os.path.join(history_dir, filename)

    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Download Kiosk ISO", f"Downloaded Kiosk {kiosk.kiosk_id} ISO (token: {kiosk.auth_token})", request)

    return FileResponse(
        path=iso_path,
        filename=filename,
        media_type="application/x-iso9660-image"
    )


