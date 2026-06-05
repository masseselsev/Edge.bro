import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any

from iso_tasks import generate_client_iso_task, download_base_iso_task, CACHE_DIR
from models import TaskLog
from database import SessionLocal

router = APIRouter()

class GenerateIsoRequest(BaseModel):
    target_ip: str
    auth_token: str

@router.post("/generate")
def generate_iso(req: GenerateIsoRequest):
    try:
        task = generate_client_iso_task.delay(req.target_ip, req.auth_token)
        return {"task_id": task.id, "message": "ISO generation task started."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/download_base")
def trigger_base_download():
    try:
        task = download_base_iso_task.delay()
        return {"task_id": task.id, "message": "Base ISO download started."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download")
def download_iso():
    iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    if not os.path.exists(iso_path):
        raise HTTPException(status_code=404, detail="Client ISO not found. Generate it first.")
    
    return FileResponse(
        path=iso_path,
        filename="Borg_Restore_Technician_Client.iso",
        media_type="application/x-iso9660-image"
    )

@router.get("/status")
def get_iso_status():
    base_iso_path = os.path.join(CACHE_DIR, "base.iso")
    base_exists = os.path.exists(base_iso_path) and os.path.getsize(base_iso_path) > 1000 * 1024 * 1024
    tmp_path = os.path.join(CACHE_DIR, "base.iso.tmp")
    client_exists = os.path.exists(os.path.join(CACHE_DIR, "technician_client_v1.iso"))
    
    progress = -1
    if not base_exists and os.path.exists(tmp_path):
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
        
    return {
        "base_iso_cached": base_exists,
        "base_iso_progress": progress,
        "client_iso_ready": client_exists
    }
