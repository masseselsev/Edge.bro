from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from version import VERSION
from routers.users import require_admin

router = APIRouter(prefix="/api")

@router.get("/version")
def get_version():
    """
    Returns the current application version.
    """
    return {"version": VERSION, "is_kiosk": False}


def get_local_ips():
    import socket
    ips = []
    try:
        for interface in socket.getifaddrs():
            addr = interface.addr
            if addr and addr.family == socket.AF_INET:
                ip = addr.address
                if ip != "127.0.0.1":
                    ips.append(ip)
    except Exception:
        try:
            hostname = socket.gethostname()
            ips = [socket.gethostbyname(hostname)]
        except Exception:
            pass
    return sorted(list(set(ips)))


@router.get("/settings", response_model=schemas.SettingsResponse)
def get_settings(db: Session = Depends(get_db), current_user: models.User = Depends(require_admin)):
    """
    Retrieves global orchestrator settings.
    """
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
    settings.available_ips = get_local_ips()
    import os
    settings.borg_host_data_path = os.getenv("BORG_HOST_DATA_PATH", "borg-data")
    return settings


@router.post("/settings", response_model=schemas.SettingsResponse)
def update_settings(payload: schemas.SettingsBase, request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(require_admin)):
    """
    Updates global orchestrator settings.
    """
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)

    settings.borg_ssh_port = payload.borg_ssh_port
    settings.borg_repo_path = payload.borg_repo_path
    settings.keep_daily = payload.keep_daily
    settings.keep_weekly = payload.keep_weekly
    settings.keep_monthly = payload.keep_monthly
    settings.global_exclusions = payload.global_exclusions
    settings.orchestrator_ip = payload.orchestrator_ip
    settings.timezone = payload.timezone
    settings.language = payload.language
    settings.retention_policy = payload.retention_policy.model_dump() if payload.retention_policy else None
    settings.default_compression = payload.default_compression
    settings.default_cpu_quota = payload.default_cpu_quota
    settings.server_ips = payload.server_ips
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Update Settings", "Updated global orchestrator settings", request)

    settings.available_ips = get_local_ips()
    import os
    settings.borg_host_data_path = os.getenv("BORG_HOST_DATA_PATH", "borg-data")
    return settings
