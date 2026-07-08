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
    import struct
    import os

    exclude_prefixes = ("br-", "docker", "veth", "lo", "virbr", "kube", "cali", "flannel")

    def ip_to_int(ip_str):
        try:
            return struct.unpack(">I", socket.inet_aton(ip_str))[0]
        except Exception:
            return 0

    ips = []
    
    # Try reading host's network interfaces from process 1 network namespace (since /proc is mapped to /host/proc)
    if os.path.exists("/host/proc/1/net/fib_trie") and os.path.exists("/host/proc/1/net/route"):
        try:
            routes = []
            with open("/host/proc/1/net/route", "r") as f:
                lines = f.readlines()
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) >= 8:
                    iface = parts[0]
                    if any(iface.startswith(prefix) for prefix in exclude_prefixes):
                        continue
                    dest_hex = parts[1]
                    mask_hex = parts[7]
                    try:
                        dest_val = int(dest_hex, 16)
                        mask_val = int(mask_hex, 16)
                        dest_int = struct.unpack(">I", struct.pack("<I", dest_val))[0]
                        mask_int = struct.unpack(">I", struct.pack("<I", mask_val))[0]
                        routes.append((iface, dest_int, mask_int))
                    except Exception:
                        pass

            trie_ips = []
            with open("/host/proc/1/net/fib_trie", "r") as f:
                lines = f.readlines()
            current_ip = None
            for line in lines:
                line = line.strip()
                if "|--" in line:
                    parts = line.split("|--")
                    if len(parts) > 1:
                        current_ip = parts[1].strip()
                elif "/32 host LOCAL" in line:
                    if current_ip and current_ip != "127.0.0.1":
                        trie_ips.append(current_ip)

            for ip in set(trie_ips):
                ip_int = ip_to_int(ip)
                for iface, dest_int, mask_int in routes:
                    if (ip_int & mask_int) == dest_int:
                        ips.append(ip)
                        break
        except Exception:
            pass

    # Fallback to standard socket.getifaddrs() or container hostname lookup
    if not ips:
        try:
            for interface in socket.getifaddrs():
                if any(interface.name.startswith(prefix) for prefix in exclude_prefixes):
                    continue
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

    changes = []
    fields = [
        ("borg_ssh_port", "Borg SSH Port"),
        ("borg_repo_path", "Repository Path"),
        ("keep_daily", "Keep Daily"),
        ("keep_weekly", "Keep Weekly"),
        ("keep_monthly", "Keep Monthly"),
        ("global_exclusions", "Global Exclusions"),
        ("orchestrator_ip", "Orchestrator IP"),
        ("timezone", "Timezone"),
        ("language", "Language"),
        ("default_compression", "Compression"),
        ("default_cpu_quota", "CPU Quota"),
        ("server_ips", "Server IPs"),
        ("server_name", "Server Name"),
    ]
    for attr, label in fields:
        old_val = getattr(settings, attr, None)
        new_val = getattr(payload, attr, None)
        if attr in ("global_exclusions", "server_ips"):
            old_str = ",".join(old_val) if isinstance(old_val, list) else str(old_val)
            new_str = ",".join(new_val) if isinstance(new_val, list) else str(new_val)
            if old_str != new_str:
                changes.append(f"{label}: '{old_str}' ➔ '{new_str}'")
        else:
            if old_val != new_val:
                changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")

    old_policy = settings.retention_policy or {}
    new_policy = payload.retention_policy.model_dump() if payload.retention_policy else {}
    if old_policy != new_policy:
        policy_changes = []
        for pk in ["type", "keep_last", "within_value", "within_unit"]:
            op_val = old_policy.get(pk)
            np_val = new_policy.get(pk)
            if op_val != np_val:
                policy_changes.append(f"Retention {pk.replace('_', ' ')}: '{op_val}' ➔ '{np_val}'")
        if policy_changes:
            changes.extend(policy_changes)

    rebuild_needed = (
        settings.language != payload.language or 
        settings.server_ips != payload.server_ips or 
        settings.orchestrator_ip != payload.orchestrator_ip
    )

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
    settings.server_name = payload.server_name
    db.commit()

    if rebuild_needed:
        try:
            from iso_tasks import trigger_base_iso_rebuild
            trigger_base_iso_rebuild(db)
        except Exception as ex:
            import logging
            logging.getLogger(__name__).error(f"Failed to trigger base ISO rebuild: {ex}")

    from database import log_user_action
    details_str = f"Update Settings: {', '.join(changes)}" if changes else "Updated global orchestrator settings (no values changed)"
    log_user_action(db, current_user.username, "Update Settings", details_str, request)

    settings.available_ips = get_local_ips()
    import os
    settings.borg_host_data_path = os.getenv("BORG_HOST_DATA_PATH", "borg-data")
    return settings
