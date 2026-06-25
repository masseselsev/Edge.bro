import random
import secrets
import os
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import List
from database import get_db
import models
import schemas
from routers.users import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kiosks", tags=["Kiosks"])

def generate_kiosk_key() -> str:
    # Generate 4 digits followed by 2 letters, excluding confusing ones (O, 0, I, 1, L, Z, 2)
    digits = "".join(random.choice("3456789") for _ in range(4))
    letters = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXY") for _ in range(2))
    return f"{digits}{letters}"


def generate_kiosk_token() -> str:
    # Generate 2 letters followed by 4 digits, excluding confusing ones (O, 0, I, 1, L, Z, 2)
    letters = "".join(random.choice("ABCDEFGHJKMNPQRSTUVWXY") for _ in range(2))
    digits = "".join(random.choice("3456789") for _ in range(4))
    return f"{letters}{digits}"


def generate_kiosk_uuid() -> str:
    # Generate 'KS' followed by 4 digits, excluding confusing ones (0, 1, 2)
    digits = "".join(random.choice("3456789") for _ in range(4))
    return f"KS{digits}"


@router.post("", response_model=schemas.KioskResponse)
def create_kiosk(req: schemas.KioskCreate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    # Generate unique placeholder UUID if none provided
    kiosk_uuid = req.uuid
    if kiosk_uuid:
        existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == kiosk_uuid).first()
        if existing:
            raise HTTPException(status_code=400, detail="Kiosk UUID already registered")
    else:
        kiosk_uuid = f"PENDING-{secrets.token_hex(8)}"
    
    # Generate unique key
    key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == key).first():
        key = generate_kiosk_key()

    kiosk = models.Kiosk(
        name=req.name, 
        uuid=kiosk_uuid, 
        key=key,
        contact=req.contact,
        comment=req.comment,
        status="PENDING"
    )
    db.add(kiosk)
    db.commit()
    db.refresh(kiosk)
    from database import log_user_action
    username = getattr(current_user, "username", "test_admin")
    log_user_action(db, username, "Create Kiosk", f"Created kiosk pairing record with friendly name '{kiosk.name}' (UUID placeholder: {kiosk.uuid})", request)
    return kiosk

@router.get("", response_model=List[schemas.KioskResponse])
def list_kiosks(db: Session = Depends(get_db), current_user = Depends(require_admin)):
    kiosks = db.query(models.Kiosk).all()
    from iso_tasks import CACHE_DIR
    settings = db.query(models.Settings).first()
    server_name = settings.server_name if (settings and settings.server_name) else "Edge.bro"
    for k in kiosks:
        if k.auth_token:
            created_date = k.created_at.strftime("%Y%m%d") if k.created_at else "unknown"
            iso_name = f"{server_name}-kiosk-{created_date}-{k.auth_token}.iso"
            iso_path = os.path.join(CACHE_DIR, "history", iso_name)
            exists = os.path.exists(iso_path)
            k.iso_exists = exists
            if exists:
                k.iso_path = iso_path
                k.iso_name = iso_name
                k.iso_size = os.path.getsize(iso_path)
            else:
                k.iso_path = None
                k.iso_name = None
                k.iso_size = None
        else:
            k.iso_exists = False
            k.iso_path = None
            k.iso_name = None
            k.iso_size = None
    return kiosks

@router.delete("/{kiosk_id}")
def delete_kiosk(kiosk_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
    
    # Remove SSH key if present before delete
    if kiosk.ssh_pub_key:
        try:
            revoke_ssh_key(kiosk.ssh_pub_key)
        except Exception as e:
            logger.error(f"Failed to revoke SSH key for deleted kiosk: {e}")

    # Remove compiled ISO file if exists
    if kiosk.auth_token:
        try:
            from iso_tasks import CACHE_DIR
            import os
            iso_path = os.path.join(CACHE_DIR, "history", f"Edge.bro-kiosk-{kiosk.auth_token}.iso")
            if os.path.exists(iso_path):
                os.remove(iso_path)
        except Exception as e:
            logger.error(f"Failed to remove ISO for deleted kiosk: {e}")

    db.delete(kiosk)
    db.commit()
    from database import log_user_action
    username = getattr(current_user, "username", "test_admin")
    log_user_action(db, username, "Delete Kiosk", f"Deleted kiosk {kiosk.uuid} (token: {kiosk.auth_token})", request)
    return {"status": "SUCCESS"}

@router.post("/{kiosk_id}/revoke")
def revoke_kiosk(kiosk_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
    from database import log_user_action
    username = getattr(current_user, "username", "test_admin")
    log_user_action(db, username, "Block Kiosk", f"Revoked/blocked kiosk {kiosk.uuid}", request)
    return {"status": "SUCCESS", "kiosk_status": kiosk.status}

@router.post("/handshake")
def handshake(req: schemas.HandshakeRequest, request: Request = None, db: Session = Depends(get_db)):
    normalized_key = req.key.strip().upper()
    
    # Look up kiosk by unique pairing key
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.key == normalized_key).first()

    if not kiosk:
        raise HTTPException(status_code=400, detail="Invalid security key")
        
    if kiosk.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Kiosk status is {kiosk.status}")

    # Verify UUID if the kiosk was pre-registered with a specific one
    if kiosk.uuid and not kiosk.uuid.startswith("PENDING-"):
        if kiosk.uuid != req.uuid:
            raise HTTPException(status_code=400, detail="UUID mismatch for this key")

    # Check if this UUID is already associated with another kiosk
    existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid, models.Kiosk.id != kiosk.id).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Kiosk UUID {req.uuid} is already registered")

    # Update kiosk record with actual client UUID
    kiosk.uuid = req.uuid
    
    # Generate unique API token in format AB1234
    token = generate_kiosk_token()
    while db.query(models.Kiosk).filter(models.Kiosk.auth_token == token).first():
        token = generate_kiosk_token()
        
    kiosk.status = "APPROVED"
    kiosk.approved_at = datetime.utcnow()
    kiosk.ssh_pub_key = req.ssh_pub_key
    kiosk.auth_token = token
    db.commit()

    # Log successful handshake
    kiosk_username = f"Kiosk: {kiosk.name} (UUID: {kiosk.uuid})" if kiosk.name else f"Kiosk: {kiosk.uuid}"
    from database import log_user_action
    log_user_action(db, kiosk_username, "Kiosk Connected (Handshake)", "Kiosk paired and initialized SSH public key", request)

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

@router.post("/enroll")
def enroll_kiosk(req: schemas.KioskEnrollRequest, db: Session = Depends(get_db)):
    # Check if UUID already registered
    existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid).first()
    
    # Generate unique auth token
    token = generate_kiosk_token()
    while db.query(models.Kiosk).filter(models.Kiosk.auth_token == token).first():
        token = generate_kiosk_token()

    if existing:
        if existing.status == "APPROVED":
            raise HTTPException(status_code=400, detail="Kiosk is already approved")
        
        # Update metadata
        existing.name = req.name
        existing.contact = req.contact
        existing.comment = req.comment
        existing.ssh_pub_key = req.ssh_pub_key
        existing.status = "PENDING"
        existing.auth_token = token
        existing.key = generate_kiosk_key()
        db.commit()
        db.refresh(existing)
        return {"status": "PENDING", "key": existing.key, "auth_token": existing.auth_token}

    key = generate_kiosk_key()
    while db.query(models.Kiosk).filter(models.Kiosk.key == key).first():
        key = generate_kiosk_key()

    kiosk = models.Kiosk(
        uuid=req.uuid,
        name=req.name,
        contact=req.contact,
        comment=req.comment,
        ssh_pub_key=req.ssh_pub_key,
        status="PENDING",
        key=key,
        auth_token=token
    )
    db.add(kiosk)
    db.commit()
    db.refresh(kiosk)
    return {"status": "PENDING", "key": key, "auth_token": token}


@router.post("/{id}/toggle-active")
def toggle_kiosk_active(id: int, request: Request = None, db: Session = Depends(get_db), auth = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    if kiosk.status == "APPROVED":
        kiosk.status = "DISABLED"
        if kiosk.ssh_pub_key:
            try:
                revoke_ssh_key(kiosk.ssh_pub_key)
            except Exception as e:
                logger.error(f"Failed to revoke kiosk SSH key during disable: {e}")
    elif kiosk.status in ["DISABLED", "PENDING"]:
        kiosk.status = "APPROVED"
        kiosk.approved_at = datetime.utcnow()
        if kiosk.ssh_pub_key:
            try:
                authorize_ssh_key(kiosk.ssh_pub_key)
            except Exception as e:
                logger.error(f"Failed to authorize kiosk SSH key during approval: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to authorize SSH key: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail=f"Cannot toggle active state for status {kiosk.status}")
        
    db.commit()
    db.refresh(kiosk)
    from database import log_user_action
    username = getattr(auth, "username", "test_admin")
    log_user_action(db, username, "Toggle Kiosk State", f"Toggled kiosk {kiosk.uuid} status to {kiosk.status}", request)
    return {"status": "SUCCESS", "kiosk_status": kiosk.status}


@router.post("/request-activation")
def request_kiosk_activation(req: schemas.RequestActivationRequest, request: Request = None, db: Session = Depends(get_db)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.auth_token == req.token).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    if kiosk.status != "DISABLED":
        raise HTTPException(status_code=400, detail=f"Kiosk is not in disabled state (current status: {kiosk.status})")
        
    kiosk.status = "PENDING"
    db.commit()
    from database import log_user_action
    log_user_action(db, f"Kiosk {kiosk.uuid}", "Request Activation", "Requested kiosk reactivation", request)
    return {"status": "SUCCESS", "message": "Activation request submitted"}


@router.post("/auto-handshake")
def auto_handshake(req: schemas.AutoHandshakeRequest, request: Request = None, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization") if request else None
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1].strip()
        
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")

    kiosk = db.query(models.Kiosk).filter(models.Kiosk.auth_token == token).first()
    if not kiosk:
        raise HTTPException(status_code=401, detail="Invalid auth token")

    if kiosk.status == "DISABLED":
        raise HTTPException(status_code=403, detail="Kiosk status is DISABLED")
        
    if kiosk.status == "PENDING":
        # Keep it pending, update metadata if needed, but do not authorize SSH
        kiosk.uuid = req.uuid
        kiosk.ssh_pub_key = req.ssh_pub_key
        db.commit()
        from database import log_user_action
        kiosk_username = f"Kiosk: {kiosk.name} (UUID: {kiosk.uuid})" if kiosk.name else f"Kiosk: {kiosk.uuid}"
        log_user_action(db, kiosk_username, "Auto Handshake Pending", "Kiosk requested status check, remains pending activation", request)
        return {"status": "PENDING"}

    if kiosk.status == "APPROVED":
        # Check if this UUID is already associated with another kiosk
        existing = db.query(models.Kiosk).filter(models.Kiosk.uuid == req.uuid, models.Kiosk.id != kiosk.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="UUID is already registered under another kiosk")

        # Update kiosk details
        kiosk.uuid = req.uuid
        kiosk.ssh_pub_key = req.ssh_pub_key

        # Authorize SSH key
        try:
            from routers.kiosks import authorize_ssh_key
            authorize_ssh_key(req.ssh_pub_key)
        except Exception as e:
            logger.error(f"Failed to authorize kiosk SSH key during auto-handshake: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to authorize SSH key: {str(e)}")

        db.commit()
        from database import log_user_action
        kiosk_username = f"Kiosk: {kiosk.name} (UUID: {kiosk.uuid})" if kiosk.name else f"Kiosk: {kiosk.uuid}"
        log_user_action(db, kiosk_username, "Auto Handshake Approved", "Authorized kiosk and registered public key", request)
        return {"status": "APPROVED"}
        
    raise HTTPException(status_code=403, detail=f"Kiosk status is {kiosk.status}")


@router.put("/{kiosk_id}", response_model=schemas.KioskResponse)
def update_kiosk(kiosk_id: int, req: schemas.KioskUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    kiosk = db.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    if not kiosk:
        raise HTTPException(status_code=404, detail="Kiosk not found")
        
    changes = []
    fields = [
        ("name", "Name"),
        ("contact", "Contact"),
        ("comment", "Comment"),
    ]
    for attr, label in fields:
        old_val = getattr(kiosk, attr, None)
        new_val = getattr(req, attr, None)
        if new_val is not None and old_val != new_val:
            changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")

    if req.name is not None:
        kiosk.name = req.name
    if req.contact is not None:
        kiosk.contact = req.contact
    if req.comment is not None:
        kiosk.comment = req.comment
        
    db.commit()
    db.refresh(kiosk)
    from database import log_user_action
    username = getattr(current_user, "username", "test_admin")
    details_str = f"Update Kiosk '{kiosk.uuid}': {', '.join(changes)}" if changes else f"Updated kiosk profile '{kiosk.uuid}' (friendly name={kiosk.name}) (no values changed)"
    log_user_action(db, username, "Update Kiosk", details_str, request)
    return kiosk


