import os
import subprocess
import redis
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from tasks import run_bootstrap_task, run_prepare_task, run_backup_task, purge_node_archives
from routers.users import require_admin, require_kiosk_or_admin

router = APIRouter(prefix="/api/nodes", tags=["Nodes"])

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)

@router.post("/{node_id}/prepare")
def trigger_prepare(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Triggers the Auto-Prepare disk labels playbook task for a node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    node.status = "NEEDS_FIX"
    db.commit()

    task = run_prepare_task.delay(node.id)
    from database import log_user_action
    log_user_action(db, current_user.username, "Prepare Node", f"Triggered disk Auto-Prepare for node '{node.hostname}'", request)
    return {"message": "Auto-prepare playbook execution triggered.", "task_id": task.id}


@router.post("/{node_id}/backup")
def trigger_backup(node_id: int, request: Request = None, payload: schemas.BackupTriggerRequest = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Triggers immediate remote backup execution.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    comment = payload.comment if payload else None
    task = run_backup_task.delay(node.id, comment=comment)
    from database import log_user_action
    log_user_action(db, current_user.username, "Backup Node", f"Triggered immediate remote backup for node '{node.hostname}' (comment: {comment})", request)
    return {"message": "Backup execution task triggered.", "task_id": task.id}


@router.delete("/{node_id}/archives")
def purge_node_backups(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Deletes all Borg backup archives for a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    task = purge_node_archives.delay(node.id)
    from database import log_user_action
    log_user_action(db, current_user.username, "Purge Node Backups", f"Purged all Borg backup archives for node '{node.hostname}'", request)
    return {"message": f"Purge of all archives for '{node.hostname}' started.", "task_id": task.id}


@router.post("/{node_id}/provision")
def trigger_provision(node_id: int, payload: schemas.NodeProvisionRequest, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Triggers bootstrap on an existing node, caching its credentials in Redis.
    """
    try:
        settings = db.query(models.Settings).first()
        if settings and settings.bootstrap_credentials:
            for cred in settings.bootstrap_credentials:
                if cred.get("username") == payload.bootstrap_user and cred.get("password") == payload.bootstrap_password:
                    if settings.default_credentials_id != cred.get("id"):
                        settings.default_credentials_id = cred.get("id")
                        db.commit()
                    break
    except Exception:
        pass

    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    node.status = "NEEDS_BOOTSTRAP"
    db.commit()

    # Clear next retry redis key
    try:
        redis_client.delete(f"node_next_retry:{node.id}")
    except Exception:
        pass

    # Store credentials in Redis
    creds = {
        "bootstrap_user": payload.bootstrap_user,
        "bootstrap_password": payload.bootstrap_password,
        "force_orchestrator_proxy": payload.force_orchestrator_proxy
    }
    redis_client.setex(f"bootstrap_creds:{node.id}", 86400, json.dumps(creds))

    task = run_bootstrap_task.delay(node.id, payload.bootstrap_password, payload.bootstrap_user, payload.force_orchestrator_proxy)
    from database import log_user_action
    log_user_action(db, current_user.username, "Provision Node", f"Triggered provisioning/bootstrap for node '{node.hostname}'", request)
    return {"message": "Provisioning triggered.", "task_id": task.id}


@router.post("/{node_id}/notes")
def update_node_notes(node_id: int, payload: schemas.NodeNotesUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Updates the notes field for a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.notes = payload.notes
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node Notes", f"Updated notes/notes for node '{node.hostname}'", request)
    return {"message": "Node notes updated successfully."}


@router.post("/{node_id}/backup-today")
def trigger_backup_today(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Sets backup_today to True for the node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.backup_today = True
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Queue Node Backup", f"Queued node '{node.hostname}' for backup execution in next window", request)
    return {"message": "Node queued for backup execution during the next window."}


@router.post("/{node_id}/toggle-pause")
def toggle_backup_pause(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Toggles backup_paused state for the node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.backup_paused = not node.backup_paused
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Toggle Node Backup Pause", f"Toggled backup pause for '{node.hostname}' to {node.backup_paused}", request)
    return {"message": "Backup status toggled successfully.", "backup_paused": node.backup_paused}


@router.post("/{node_id}/assign-group/{group_id}")
def assign_node_group(node_id: int, group_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Assigns the node to a backup group. If group_id is 0 or negative, unassigns the node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    if group_id <= 0:
        node.group_id = None
    else:
        group = db.query(models.BackupGroup).filter(models.BackupGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup group not found.")
        node.group_id = group_id
        
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Assign Node Group", f"Updated group assignment for node '{node.hostname}' to group ID {group_id}", request)
    return {"message": "Node group assignment updated successfully."}


@router.get("/{node_id}/task-logs", response_model=List[schemas.TaskLogResponse])
def get_node_task_logs(node_id: int, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Retrieves background execution logs associated with a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    return db.query(models.TaskLog).filter(models.TaskLog.node_id == node_id).order_by(models.TaskLog.created_at.desc()).limit(20).all()


def apply_saved_license_task(node_id: int, db: Session = None):
    """Background task to apply a saved base64 V2C license over SSH to a restored node."""
    should_close = False
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        should_close = True
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node or not node.hasp_license_v2c:
        return
        
    try:
        b64_content = node.hasp_license_v2c
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-p", str(node.ssh_port),
            "-i", "/root/.ssh/id_ed25519",
            f"root@{node.ip_address}",
            f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
            f"(. /opt/edge/rc.setenv && /opt/edge/bin/hasp_update u /tmp/license.v2c 2>/dev/null && echo 'CLI_SUCCESS' || echo 'CLI_FAILED') && "
            f"rm -f /tmp/license.v2c"
        ]
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        
        # Fallback to ACC HTTP checkin if CLI fails
        if res.returncode != 0 or "CLI_SUCCESS" not in res.stdout:
            ssh_cmd_acc = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-p", str(node.ssh_port),
                "-i", "/root/.ssh/id_ed25519",
                f"root@{node.ip_address}",
                f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
                f"curl -s -F \"check_in_file=@/tmp/license.v2c\" http://localhost:1947/_int_/checkin_file.html && "
                f"rm -f /tmp/license.v2c"
            ]
            subprocess.run(ssh_cmd_acc, capture_output=True, timeout=30)
            
        # Re-fetch new HASP details to update Node status
        from routers.restore import get_node_hasp_status
        hasp_res = get_node_hasp_status(node_id=node.id, db=db, current_user=None)
        if hasp_res.get("status") == "active":
            node.status = "READY"
            db.commit()
    except Exception as e:
        print(f"Error applying saved license to node {node_id}: {e}")
    finally:
        if should_close:
            db.close()


@router.post("/checkin-restored")
def checkin_restored_node(
    req: schemas.NodeCheckinRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    db: Session = Depends(get_db)
):
    """
    Called by a restored node on first boot to notify orchestrator
    and automatically request/apply its saved Sentinel HASP license.
    """
    node = db.query(models.Node).filter(models.Node.hostname == req.hostname).first()
    if not node and req.ip_address:
        node = db.query(models.Node).filter(models.Node.ip_address == req.ip_address).first()
    if not node and request and request.client:
        node = db.query(models.Node).filter(models.Node.ip_address == request.client.host).first()
        
    if not node:
        raise HTTPException(status_code=404, detail="Restored node not matched with any registered node.")
        
    # Log audit event
    from database import log_user_action
    log_user_action(db, "System: Restored Node", "Restored Node Checkin", f"Node '{node.hostname}' checked in after bare-metal restore", request)
    
    # Update status to RESTORED to indicate license update is needed
    node.status = "RESTORED"
    db.commit()
    
    # Trigger auto-activation if saved license is present in DB
    if node.hasp_license_v2c:
        background_tasks.add_task(apply_saved_license_task, node.id)
        return {"status": "success", "message": "Node check-in accepted. HASP license application triggered."}
        
    return {"status": "success", "message": "Node check-in accepted. No saved HASP license found."}
