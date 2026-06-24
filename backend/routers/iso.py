import os
import json
import time
import redis
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Depends
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
def generate_iso(req: GenerateIsoRequest, auth = Depends(require_admin)):
    try:
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
    size_file = os.path.join(CACHE_DIR, "base.iso.size")
    client_iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    if os.path.exists(base_iso_path):
        os.remove(base_iso_path)
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
        
    return {
        "base_iso_cached": base_exists or client_exists,
        "base_iso_progress": progress,
        "base_iso_speed": speed_str,
        "client_iso_ready": client_exists
    }

import subprocess
from fastapi.responses import StreamingResponse

@router.get("/repos/{hostname}/download")
def download_repo(hostname: str, token: str, auth = Depends(require_kiosk_or_admin)):
    token_path = os.path.join(CACHE_DIR, "auth_token.txt")
    expected_token = "offline-token-1234"
    if os.path.exists(token_path):
        try:
            with open(token_path, "r") as f:
                expected_token = f.read().strip()
        except:
            pass
    
    if token.strip().upper() != expected_token.strip().upper():
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    repo_dir = f"/data/borg/fleet/{hostname}"
    if not os.path.exists(repo_dir):
        raise HTTPException(status_code=404, detail="Repository not found")
        
    # Get total size of repository directory to send in X-Total-Size header
    total_size = 0
    try:
        du_out = subprocess.check_output(["du", "-sb", repo_dir]).decode().strip()
        total_size = int(du_out.split()[0])
    except Exception as e:
        for root, dirs, files in os.walk(repo_dir):
            for file in files:
                total_size += os.path.getsize(os.path.join(root, file))
                
    def tar_generator():
        proc = subprocess.Popen(
            ["tar", "-cf", "-", "-C", "/data/borg/fleet", hostname],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.terminate()
            proc.wait()
            
    return StreamingResponse(
        tar_generator(),
        media_type="application/x-tar",
        headers={
            "Content-Disposition": f"attachment; filename={hostname}.tar",
            "X-Total-Size": str(total_size)
        }
    )


@router.post("/kiosks/issue")
def issue_kiosk(req: schemas.KioskIssueRequest, db: Session = Depends(get_db), auth = Depends(require_admin)):
    from routers.kiosks import generate_kiosk_key
    import secrets

    # Generate auth token (pairing key style, e.g. 1234AB)
    auth_token = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.auth_token == auth_token).first():
        auth_token = generate_kiosk_key()

    # Generate a unique pending uuid placeholder
    uuid_val = f"PENDING-{secrets.token_hex(8)}"
    # Generate another pairing key (redundant but satisfies non-null constraint)
    pairing_key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == pairing_key).first():
        pairing_key = generate_kiosk_key()

    # Create kiosk record directly approved
    kiosk = models.Kiosk(
        name=req.name,
        uuid=uuid_val,
        key=pairing_key,
        phone=req.phone,
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
    
    # Return kiosk response + task_id to follow progress
    return {"kiosk": kiosk, "task_id": task.id}


@router.post("/kiosks/{id}/recreate")
def recreate_kiosk_iso(id: int, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    from iso_tasks import repack_kiosk_iso_task
    task = repack_kiosk_iso_task.delay(kiosk.id)
    return {"task_id": task.id, "message": "Recreation task started"}


@router.get("/kiosks/{id}/download")
def download_kiosk_iso(id: int, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    if not kiosk.auth_token:
        raise HTTPException(status_code=400, detail="Kiosk does not have a dynamic auth token")
        
    from iso_tasks import CACHE_DIR
    iso_path = os.path.join(CACHE_DIR, "history", f"Edge.bro-kiosk-{kiosk.auth_token}.iso")
    if not os.path.exists(iso_path):
        raise HTTPException(status_code=404, detail="ISO image has been pruned from cache. Re-create it first.")
        
    filename = f"Edge.bro-kiosk-{kiosk.auth_token}.iso"
    return FileResponse(
        path=iso_path,
        filename=filename,
        media_type="application/x-iso9660-image"
    )


