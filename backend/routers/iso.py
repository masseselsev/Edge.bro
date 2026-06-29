import os
import json
import time
import redis
import uuid
import shutil
import subprocess
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional

from iso_tasks import generate_client_iso_task, download_base_iso_task, CACHE_DIR
from models import TaskLog
from database import SessionLocal, get_db
from sqlalchemy.orm import Session
from routers.users import require_admin, require_kiosk_or_admin
import models
import schemas

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)

router = APIRouter()

class GenerateIsoRequest(BaseModel):
    target_ip: str
    auth_token: str

class BaseIsoDownloadRequest(BaseModel):
    url: Optional[str] = None

@router.post("/generate")
def generate_iso(req: GenerateIsoRequest, db: Session = Depends(get_db), auth = Depends(require_admin)):
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
    client_exists = os.path.exists(os.path.join(CACHE_DIR, "technician_client_v1.iso"))
    
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

    return {
        "base_iso_cached": base_exists or client_exists,
        "base_iso_progress": progress,
        "base_iso_speed": speed_str,
        "client_iso_ready": client_exists,
        "iso_cache_free_space": free,
        "iso_cache_total_space": total
    }

import subprocess
from fastapi.responses import StreamingResponse

@router.get("/repos/{hostname}/download")
def download_repo(
    hostname: str,
    token: str,
    request: Request = None,
    db: Session = Depends(get_db),
    auth = Depends(require_kiosk_or_admin)
):
    node = db.query(models.Node).filter(models.Node.hostname == hostname).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    shared_repo = "/data/borg/fleet"
    if not os.path.exists(shared_repo) or not os.path.exists(os.path.join(shared_repo, "config")):
        raise HTTPException(status_code=404, detail="Shared repository not found")

    # Get the list of archives for this node from the shared repository
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    
    try:
        list_res = subprocess.run(
            ["borg", "list", "--json", shared_repo],
            env=env,
            capture_output=True,
            text=True,
            check=True
        )
        all_archives = json.loads(list_res.stdout).get("archives", [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to query shared repository: {str(e)}")

    node_archives = [a["name"] for a in all_archives if a["name"].startswith(f"{hostname}-")]
    if not node_archives:
        raise HTTPException(status_code=404, detail="No backups found for this node")

    # Create isolated temporary repository path on NVMe disk under /data/borg/tmp
    temp_uuid = uuid.uuid4().hex
    temp_parent = f"/data/borg/tmp/download_{temp_uuid}"
    temp_repo_dir = os.path.join(temp_parent, hostname)
    os.makedirs(temp_repo_dir, exist_ok=True)
    
    # Use distinct temporary HOME directory to avoid cache and security history lockups/conflicts
    temp_home = f"/tmp/borg_home_{temp_uuid}"
    env["HOME"] = temp_home

    # Initialize the temporary repository
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

    # Transfer only the node's archives from shared repository to temporary repository
    try:
        for archive in node_archives:
            export_proc = subprocess.Popen(
                ["borg", "export-tar", f"{shared_repo}::{archive}", "-"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            import_proc = subprocess.Popen(
                ["borg", "import-tar", f"{temp_repo_dir}::{archive}", "-"],
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
            kiosk_name = f"Kiosk: {auth.name} (UUID: {auth.uuid})" if auth.name else f"Kiosk: {auth.uuid}"
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
    from routers.kiosks import generate_kiosk_key, generate_kiosk_token, generate_kiosk_uuid
    import secrets

    # Generate auth token (kiosk token style, e.g. AB1234)
    auth_token = generate_kiosk_token()
    while db.query(models.Kiosk).filter(models.Kiosk.auth_token == auth_token).first():
        auth_token = generate_kiosk_token()

    # Generate a unique memorable uuid (format: KS1234)
    uuid_val = generate_kiosk_uuid()
    while db.query(models.Kiosk).filter(models.Kiosk.uuid == uuid_val).first():
        uuid_val = generate_kiosk_uuid()

    # Generate pairing key (connection token style, e.g. 1234AB)
    pairing_key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == pairing_key).first():
        pairing_key = generate_kiosk_key()

    # Create kiosk record directly approved
    kiosk = models.Kiosk(
        name=req.name,
        uuid=uuid_val,
        key=pairing_key,
        contact=req.contact,
        comment=req.comment,
        status="APPROVED",
        auth_token=auth_token
    )
    db.add(kiosk)
    db.commit()
    db.refresh(kiosk)

    # Trigger repack Celery task
    from iso_tasks import repack_kiosk_iso_task
    task = repack_kiosk_iso_task.delay(kiosk.id)
    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Issue Kiosk", f"Issued kiosk {kiosk.uuid} (token: {kiosk.auth_token}) for recipient {kiosk.name}", request)

    # Return kiosk response + task_id to follow progress
    return {"kiosk": kiosk, "task_id": task.id}


@router.post("/kiosks/{id}/recreate")
def recreate_kiosk_iso(id: int, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    from iso_tasks import repack_kiosk_iso_task
    task = repack_kiosk_iso_task.delay(kiosk.id)
    
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Recreate Kiosk ISO", f"Triggered recreation of Kiosk {kiosk.uuid} ISO (token: {kiosk.auth_token})", request)

    return {"task_id": task.id, "message": "Recreation task started"}


@router.get("/kiosks/{id}/download")
def download_kiosk_iso(id: int, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    if not kiosk.auth_token:
        raise HTTPException(status_code=400, detail="Kiosk does not have a dynamic auth token")
        
    settings = db.query(models.Settings).first()
    server_name = settings.server_name if (settings and settings.server_name) else "Edge.bro"

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
    log_user_action(db, username, "Download Kiosk ISO", f"Downloaded Kiosk {kiosk.uuid} ISO (token: {kiosk.auth_token})", request)

    return FileResponse(
        path=iso_path,
        filename=filename,
        media_type="application/x-iso9660-image"
    )


