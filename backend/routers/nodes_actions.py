import os
import subprocess
import json
from dataclasses import dataclass
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db
from core import ssh, scheduler, transfer_retry
import models
import schemas
from tasks import run_bootstrap_task, run_prepare_task, run_backup_task, purge_node_archives
from auth import require_admin, require_kiosk_or_admin
from routers.deps import node_or_404
from core.redis_client import make_client as make_redis_client

router = APIRouter(prefix="/api/nodes", tags=["Nodes"])

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = make_redis_client(REDIS_URL)

@router.post("/{node_id}/prepare")
def trigger_prepare(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Triggers the Auto-Prepare disk labels playbook task for a node.
    """
    node = node_or_404(db, node_id)

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
    node = node_or_404(db, node_id)

    # A second press cannot make the first run go faster. Backups bound for one
    # repository serialise on borg's lock, so the duplicate would sit out
    # `--lock-wait` behind the transfer it just duplicated and then be recorded
    # as a failure. The scheduler has always counted running backups before
    # dispatching; this button used to bypass that.
    if scheduler.is_backup_lock_live(node.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A backup is already running for '{node.hostname}'.",
        )

    # A previous stop request naming a run that has since ended would abort
    # this one the moment it looked. Spend it here rather than making the
    # operator wonder why Backup does nothing.
    transfer_retry.clear_cancel(redis_client, node.id)

    comment = payload.comment if payload else None
    task = run_backup_task.delay(node.id, comment=comment)
    from database import log_user_action
    log_user_action(db, current_user.username, "Backup Node", f"Triggered immediate remote backup for node '{node.hostname}' (comment: {comment})", request)
    return {"message": "Backup execution task triggered.", "task_id": task.id}


@router.post("/{node_id}/backup/stop")
def stop_backup(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Stops the backup currently running or queued for this node.

    A run waiting out a busy repository retries for hours by design, so there
    has to be a way to call one off — queued by mistake, or the repository is
    needed for something else.

    The request names the task rather than the node, so a run that has already
    ended cannot have its stop request applied to whatever the scheduler starts
    next. The task acts on it: a queued run gives up before transferring, and
    one already in flight closes its end of the SSH connection, which leaves
    borg's checkpoints in place for the next attempt to resume from.
    """
    node = node_or_404(db, node_id)

    raw = redis_client.get(f"backup_running:{node.id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No backup is running for '{node.hostname}'.",
        )

    value = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    task_id = value.split(":", 1)[1] if ":" in value else None
    if not task_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The running backup cannot be identified, so it cannot be stopped.",
        )

    transfer_retry.request_cancel(redis_client, node.id, task_id)

    # Cleared here rather than left to the task. A queued run does not wake for
    # up to ten minutes, and until then the fleet would go on showing a backup
    # the operator has already stopped.
    for key in (f"backup_running:{node.id}", f"backup_speed:{node.id}"):
        try:
            redis_client.delete(key)
        except Exception:
            pass

    from database import log_user_action
    log_user_action(db, current_user.username, "Stop Backup", f"Stopped the backup running for node '{node.hostname}'", request)
    return {"message": "Backup stop requested.", "task_id": task_id}


@router.delete("/{node_id}/archives")
def purge_node_backups(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Deletes all Borg backup archives for a specific node.
    """
    node = node_or_404(db, node_id)

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

    node = node_or_404(db, node_id)

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
    node = node_or_404(db, node_id)
    
    node.notes = payload.notes
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node Notes", f"Updated notes/notes for node '{node.hostname}'", request)
    return {"message": "Node notes updated successfully."}


@router.post("/{node_id}/ssh-login")
def update_node_ssh_login(node_id: int, payload: schemas.NodeSshLoginUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Updates the saved ssh:// login for a specific node's quick-connect link.
    """
    node = node_or_404(db, node_id)

    node.ssh_login = payload.ssh_login
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node SSH Login", f"Updated the saved SSH login for node '{node.hostname}'", request)
    return {"message": "Node SSH login updated successfully."}


@router.post("/{node_id}/nat-override")
def update_node_nat_override(node_id: int, payload: schemas.NodeNatOverrideUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Overrides whether the orchestrator is behind NAT for this specific node.
    None clears the override so the node inherits from its group, then global.
    """
    node = node_or_404(db, node_id)

    node.orchestrator_behind_nat = payload.orchestrator_behind_nat
    db.commit()

    if payload.orchestrator_behind_nat is None:
        described = "inherit"
    else:
        described = "behind NAT" if payload.orchestrator_behind_nat else "direct"
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node NAT Override", f"Set NAT mode for node '{node.hostname}' to {described}", request)
    return {"message": "Node NAT override updated successfully."}


@router.post("/{node_id}/rate-limit")
def update_node_rate_limit(node_id: int, payload: schemas.NodeRateLimitUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Overrides the upload rate limit for this specific node, in KiB/s.
    None clears the override so the node inherits its group limit, then unlimited.
    """
    node = node_or_404(db, node_id)

    if payload.upload_rate_limit is not None and payload.upload_rate_limit < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rate limit cannot be negative.")

    node.upload_rate_limit = payload.upload_rate_limit
    db.commit()

    described = "inherit" if payload.upload_rate_limit is None else f"{payload.upload_rate_limit} KiB/s"
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node Rate Limit", f"Set upload rate limit for node '{node.hostname}' to {described}", request)
    return {"message": "Node rate limit updated successfully."}


@router.post("/{node_id}/cpu-quota-override")
def update_node_cpu_quota_override(node_id: int, payload: schemas.NodeCpuQuotaOverrideUpdate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Overrides the CPU quota (percent of one core) for this specific node.
    None clears the override so the node inherits its group's value, then
    the global default. 0 is a distinct, valid value meaning explicit no
    limit for this node only.
    """
    node = node_or_404(db, node_id)

    node.cpu_quota = payload.cpu_quota
    db.commit()

    if payload.cpu_quota is None:
        described = "inherit"
    elif payload.cpu_quota == 0:
        described = "no limit"
    else:
        described = f"{payload.cpu_quota}%"
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Node CPU Quota Override", f"Set CPU quota for node '{node.hostname}' to {described}", request)
    return {"message": "Node CPU quota override updated successfully."}


@router.post("/{node_id}/backup-today")
def trigger_backup_today(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Sets backup_today to True for the node.
    """
    node = node_or_404(db, node_id)
    
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
    node = node_or_404(db, node_id)
    
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
    node = node_or_404(db, node_id)
    
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
    node = node_or_404(db, node_id)
    return db.query(models.TaskLog).filter(models.TaskLog.node_id == node_id).order_by(models.TaskLog.created_at.desc()).limit(20).all()


@dataclass
class _LicensedNode:
    """The node fields the license flow needs, detached from any session.

    Applying a license is up to three SSH round trips at 30s timeouts each.
    Running that with a session open — worse, with the *request's* session
    open, which is what this did when called with a db — parks a connection
    idle in transaction for the duration. See core.db_session.
    """
    id: int
    ssh_port: int
    ip_address: str
    hasp_runtime_version: Optional[str]
    hasp_license_v2c: Optional[str]


def apply_saved_license_task(node_id: int):
    """Background task to apply a saved base64 V2C license over SSH to a restored node."""
    from core.db_session import session_scope
    from core.hasp_helper import check_hasp_status_on_node

    with session_scope() as db:
        node = db.query(models.Node).filter(models.Node.id == node_id).first()
        if not node or not node.hasp_license_v2c:
            return
        target = _LicensedNode(
            id=node.id,
            ssh_port=node.ssh_port,
            ip_address=node.ip_address,
            hasp_runtime_version=node.hasp_runtime_version,
            hasp_license_v2c=node.hasp_license_v2c,
        )

    try:
        b64_content = target.hasp_license_v2c
        ssh_cmd = ssh.command(
            target.ip_address, target.ssh_port,
            f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
            f"(. /opt/edge/rc.setenv && /opt/edge/bin/hasp_update u /tmp/license.v2c 2>/dev/null && echo 'CLI_SUCCESS' || echo 'CLI_FAILED') && "
            f"rm -f /tmp/license.v2c",
        )
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)

        # Fallback to ACC HTTP checkin if CLI fails
        if res.returncode != 0 or "CLI_SUCCESS" not in res.stdout:
            ssh_cmd_acc = ssh.command(
                target.ip_address, target.ssh_port,
                f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
                f"curl -s -F \"check_in_file=@/tmp/license.v2c\" http://localhost:1947/_int_/checkin_file.html && "
                f"rm -f /tmp/license.v2c",
            )
            subprocess.run(ssh_cmd_acc, capture_output=True, timeout=30)

        # Re-fetch new HASP details to update Node status
        if check_hasp_status_on_node(target) == "active":
            with session_scope() as db:
                node = db.query(models.Node).filter(models.Node.id == node_id).first()
                if node:
                    node.status = "READY"
    except Exception as e:
        print(f"Error applying saved license to node {node_id}: {e}")


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
