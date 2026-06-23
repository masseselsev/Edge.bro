import random
import secrets
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models
import schemas
from routers.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kiosks", tags=["Kiosks"])

def generate_kiosk_key() -> str:
    # Generate 2 blocks of 4 alphanumeric characters, excluding ambiguous ones (O, 0, I, 1, L)
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    block1 = "".join(random.choice(chars) for _ in range(4))
    block2 = "".join(random.choice(chars) for _ in range(4))
    return f"{block1}-{block2}"

@router.post("", response_model=schemas.KioskResponse)
def create_kiosk(req: schemas.KioskCreate, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    # Check if uuid already registered
    existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid).first()
    if existing:
        raise HTTPException(status_code=400, detail="Kiosk UUID already registered")
    
    # Generate unique key
    key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == key).first():
        key = generate_kiosk_key()

    kiosk = models.Kiosk(name=req.name, uuid=req.uuid, key=key)
    db.add(kiosk)
    db.commit()
    db.refresh(kiosk)
    return kiosk

@router.get("", response_model=List[schemas.KioskResponse])
def list_kiosks(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    return db.query(models.Kiosk).all()

@router.delete("/{kiosk_id}")
def delete_kiosk(kiosk_id: int, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
    
    # Remove SSH key if present before delete
    if kiosk.ssh_pub_key:
        try:
            revoke_ssh_key(kiosk.ssh_pub_key)
        except Exception as e:
            logger.error(f"Failed to revoke SSH key for deleted kiosk: {e}")

    db.delete(kiosk)
    db.commit()
    return {"status": "SUCCESS"}

@router.post("/{kiosk_id}/revoke")
def revoke_kiosk(kiosk_id: int, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
    
    kiosk.status = "REVOKED"
    if kiosk.ssh_pub_key:
        try:
            revoke_ssh_key(kiosk.ssh_pub_key)
        except Exception as e:
            logger.error(f"Failed to revoke SSH key for revoked kiosk: {e}")
            
    db.commit()
    return {"status": "SUCCESS", "kiosk_status": kiosk.status}

@router.post("/handshake")
def handshake(req: schemas.HandshakeRequest, db: Session = Depends(get_db)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid, models.Kiosk.key == req.key).first()
    if not kiosk:
        raise HTTPException(status_code=400, detail="Invalid UUID or security key")
    
    if kiosk.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Kiosk status is {kiosk.status}")

    # Generate unique API token
    token = secrets.token_hex(24)
    kiosk.status = "APPROVED"
    kiosk.ssh_pub_key = req.ssh_pub_key
    kiosk.auth_token = token
    db.commit()

    # Authorize SSH key
    try:
        authorize_ssh_key(req.ssh_pub_key)
    except Exception as e:
        logger.error(f"Failed to authorize kiosk SSH key during handshake: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to authorize SSH key: {str(e)}")

    # Get orchestrator SSH public key
    pub_key_path = "/root/.ssh/id_ed25519.pub"
    orch_pub_key = ""
    if os.path.exists(pub_key_path):
        try:
            with open(pub_key_path, "r") as f:
                orch_pub_key = f.read().strip()
        except Exception as e:
            logger.error(f"Failed to read orchestrator SSH public key: {e}")

    return {
        "status": "SUCCESS",
        "auth_token": token,
        "orchestrator_ssh_pub_key": orch_pub_key
    }

def authorize_ssh_key(pub_key: str):
    path = "/root/.ssh/authorized_keys"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    restriction = 'command="borg serve --restrict-to-path /data/borg/fleet",no-port-forwarding,no-X11-forwarding,no-pty '
    entry = f"{restriction}{pub_key.strip()}\n"
    
    content = ""
    if os.path.exists(path):
        with open(path, "r") as f:
            content = f.read()
            
    if pub_key.strip() not in content:
        with open(path, "a") as f:
            f.write(entry)
            
    # Fix permissions
    from tasks import fix_ssh_permissions
    try:
        fix_ssh_permissions()
    except Exception as e:
        logger.error(f"Failed to fix SSH permissions: {e}")

def revoke_ssh_key(pub_key: str):
    path = "/root/.ssh/authorized_keys"
    if not os.path.exists(path):
        return
        
    with open(path, "r") as f:
        lines = f.readlines()
    
    # Keep lines that do not contain the target public key
    new_lines = [l for l in lines if pub_key.strip() not in l]
    with open(path, "w") as f:
        f.writelines(new_lines)
        
    from tasks import fix_ssh_permissions
    try:
        fix_ssh_permissions()
    except Exception as e:
        logger.error(f"Failed to fix SSH permissions during key revocation: {e}")
