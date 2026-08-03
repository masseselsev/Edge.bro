import os
import subprocess
import ipaddress
import redis
import json
import datetime
import tempfile
import shutil
import zipfile
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from tasks import run_bootstrap_task, purge_node_archives
from routers.users import require_admin, require_kiosk_or_admin

router = APIRouter(prefix="/api/nodes", tags=["Nodes"])

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)

def parse_ip_input(ip_input: str) -> List[str]:
    """
    Parses IP input which can be single IP, comma/newline-separated list,
    ranges (e.g. 192.168.1.50-100 or 192.168.1.50-192.168.1.60), or CIDR block.
    """
    cleaned = ip_input.replace("\n", ",").replace(" ", ",")
    raw_entries = [r.strip() for r in cleaned.split(",") if r.strip()]
    ips = []
    for entry in raw_entries:
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
                ips.extend([str(ip) for ip in net.hosts()])
            except Exception:
                pass
        elif "-" in entry:
            try:
                parts = entry.split("-")
                start_str = parts[0].strip()
                end_str = parts[1].strip()
                if "." in end_str:
                    start_ip = ipaddress.ip_address(start_str)
                    end_ip = ipaddress.ip_address(end_str)
                    curr = start_ip
                    while curr <= end_ip:
                        ips.append(str(curr))
                        curr += 1
                else:
                    start_ip = ipaddress.ip_address(start_str)
                    base_ip_parts = start_str.split(".")
                    start_num = int(base_ip_parts[-1])
                    end_num = int(end_str)
                    prefix = ".".join(base_ip_parts[:-1])
                    for i in range(start_num, end_num + 1):
                        ips.append(f"{prefix}.{i}")
            except Exception:
                pass
        else:
            try:
                ipaddress.ip_address(entry)
                ips.append(entry)
            except Exception:
                pass
    return list(dict.fromkeys(ips))


@router.get("", response_model=schemas.PaginatedNodesResponse)
def get_nodes(
    page: int = 1,
    limit: int = 50,
    q: Optional[str] = None,
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    sort_by: str = "hostname",
    sort_order: str = "asc",
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin)
):
    """
    Retrieves lists of nodes with support for server-side search, filtering, sorting, and pagination.
    """
    query = db.query(models.Node)
    
    # 1. Apply search filter
    if q:
        query = query.filter(
            (models.Node.hostname.ilike(f"%{q}%")) |
            (models.Node.ip_address.ilike(f"%{q}%"))
        )
    
    # 2. Apply group and status filters
    if group_id is not None:
        query = query.filter(models.Node.group_id == group_id)
    if status:
        query = query.filter(models.Node.status == status)
        
    # 3. Apply sorting
    if sort_by in models.Node.__table__.columns:
        col = getattr(models.Node, sort_by)
    else:
        col = models.Node.hostname
        
    if sort_order == "desc":
        query = query.order_by(col.desc())
    else:
        query = query.order_by(col.asc())
        
    # 4. Count and Paginate
    total = query.count()
    offset = (page - 1) * limit
    nodes = query.offset(offset).limit(limit).all()

    # Calculate shared repository size once
    shared_repo_size = 0
    repo_dir = "/data/borg/fleet"
    if os.path.exists(repo_dir):
        try:
            for root, dirs, files in os.walk(repo_dir):
                for file in files:
                    shared_repo_size += os.path.getsize(os.path.join(root, file))
        except Exception:
            shared_repo_size = 0

    results = []
    for node in nodes:
        # Calculate repository size on disk
        repo_size = shared_repo_size if node.last_backup else 0

        # Check if backup is running
        is_running = False
        progress = 0
        running_task_id = None
        try:
            val = redis_client.get(f"backup_running:{node.id}")
            if val:
                val_str = val.decode('utf-8') if isinstance(val, bytes) else str(val)
                is_running = True
                if ":" in val_str:
                    parts = val_str.split(":", 1)
                    try:
                        start_time = int(parts[0])
                    except ValueError:
                        start_time = 1
                    running_task_id = parts[1]
                else:
                    try:
                        start_time = int(val_str)
                    except ValueError:
                        start_time = 1
                    running_task_id = None
                
                # Auto-clear legacy placeholder "1" keys or old keys lacking task IDs (older than 10 mins)
                import time
                if start_time == 1 or (not running_task_id and (int(time.time()) - start_time > 600)):
                    redis_client.delete(f"backup_running:{node.id}")
                    is_running = False
                
                if is_running and running_task_id:
                    from celery_app import celery_app
                    res = celery_app.AsyncResult(running_task_id)
                    if res.ready():
                        redis_client.delete(f"backup_running:{node.id}")
                        is_running = False
                
                if is_running:
                    import time
                    import math
                    elapsed = max(0, int(time.time()) - start_time)
                    progress = max(0, min(99, int(100 * (1 - math.exp(-elapsed / 45.0)))))
        except Exception:
            pass

        node_dict = {
            "id": node.id,
            "hostname": node.hostname,
            "ip_address": node.ip_address,
            "ssh_port": node.ssh_port,
            "status": node.status,
            "last_backup": node.last_backup,
            "disk_type": node.disk_type or "Unknown",
            "network_iface": node.network_iface,
            "efi_uuid": node.efi_uuid,
            "partition_layout": node.partition_layout,
            "os_version": node.os_version,
            "next_retry_at": None,
            "repo_size_bytes": repo_size,
            "group_id": node.group_id,
            "backup_paused": node.backup_paused,
            "backup_today": node.backup_today,
            "missed_window": node.missed_window,
            "cpu_info": node.cpu_info,
            "memory_info": node.memory_info,
            "edge_version": node.edge_version,
            "hasp_runtime_version": node.hasp_runtime_version,
            "notes": node.notes,
            "orchestrator_behind_nat": node.orchestrator_behind_nat,
            "is_backup_running": is_running,
            "backup_progress": progress,
            "backup_task_id": running_task_id,
            "last_ping_status": node.last_ping_status,
            "last_available_at": node.last_available_at
        }
        if node.status == "OFFLINE":
            try:
                next_retry = redis_client.get(f"node_next_retry:{node.id}")
                if next_retry:
                    import datetime
                    node_dict["next_retry_at"] = datetime.datetime.fromtimestamp(int(next_retry), tz=datetime.timezone.utc)
            except Exception:
                pass
        results.append(node_dict)

    return {
        "nodes": results,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit
    }


@router.get("/history", response_model=schemas.PaginatedBackupHistoryResponse)
def get_all_history(
    page: int = 1,
    limit: int = 50,
    q: Optional[str] = None,
    sort_by: str = "timestamp",
    sort_order: str = "desc",
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin)
):
    """
    Retrieves backup snapshot history records for all nodes.
    """
    query = db.query(models.BackupHistory).outerjoin(models.Node, models.BackupHistory.node_id == models.Node.id)
    
    if q:
        query = query.filter(
            (models.Node.hostname.ilike(f"%{q}%")) |
            (models.BackupHistory.archive_name.ilike(f"%{q}%")) |
            (models.BackupHistory.status.ilike(f"%{q}%")) |
            (models.BackupHistory.comment.ilike(f"%{q}%"))
        )
        
    # Apply sorting
    if sort_by == "hostname":
        col = models.Node.hostname
    elif sort_by in models.BackupHistory.__table__.columns:
        col = getattr(models.BackupHistory, sort_by)
    else:
        col = models.BackupHistory.timestamp
        
    if sort_order == "desc":
        query = query.order_by(col.desc())
    else:
        query = query.order_by(col.asc())
        
    total = query.count()
    offset = (page - 1) * limit
    history_records = query.offset(offset).limit(limit).all()
    
    return {
        "history": history_records,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def add_node(payload: schemas.NodeCreate, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Registers one or more new nodes and triggers bootstrap.
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

    ips = parse_ip_input(payload.ip_address)
    if not ips:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid IP addresses, ranges, or CIDR blocks could be parsed from input."
        )

    created_nodes = []
    task_ids = []
    node_ids = []

    for idx, ip in enumerate(ips):
        if payload.auto_detect_hostname:
            current_hostname = ip
        else:
            current_hostname = payload.hostname if len(ips) == 1 else f"{payload.hostname}-{idx+1}"

        # Check duplicate
        existing = db.query(models.Node).filter(
            (models.Node.hostname == current_hostname) | 
            (models.Node.ip_address == ip)
        ).first()
        
        if existing:
            continue

        node = models.Node(
            hostname=current_hostname,
            ip_address=ip,
            ssh_port=payload.ssh_port,
            status="NEEDS_BOOTSTRAP"
        )
        db.add(node)
        db.commit()
        db.refresh(node)

        # Store credentials in Redis for 24 hours for periodic auto-retry provisioning if offline
        creds = {
            "bootstrap_user": payload.bootstrap_user,
            "bootstrap_password": payload.bootstrap_password,
            "force_orchestrator_proxy": payload.force_orchestrator_proxy
        }
        redis_client.setex(f"bootstrap_creds:{node.id}", 86400, json.dumps(creds))

        # Spawn bootstrap task
        task = run_bootstrap_task.delay(node.id, payload.bootstrap_password, payload.bootstrap_user, payload.force_orchestrator_proxy)
        
        created_nodes.append(node)
        task_ids.append(task.id)
        node_ids.append(node.id)

    if not created_nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All parsed nodes already exist in the database."
        )

    from database import log_user_action
    log_user_action(db, current_user.username, "Register Nodes", f"Registered {len(created_nodes)} node(s). Starting bootstrap. First IP: {ips[0]}", request)

    return {
        "message": f"Successfully registered {len(created_nodes)} node(s). Bootstrap triggered.",
        "task_id": task_ids[0],
        "node_id": node_ids[0],
        "all_task_ids": task_ids,
        "all_node_ids": node_ids
    }


@router.get("/{node_id}/history", response_model=List[schemas.BackupHistoryResponse])
def get_node_history(node_id: int, db: Session = Depends(get_db), current_user = Depends(require_kiosk_or_admin)):
    """
    Retrieves the backup snapshot history records for a specific node.
    """
    return db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).all()


@router.get("/history/{history_id}/files", response_model=schemas.ArchiveFileListResponse)
def get_archive_files(history_id: int, db: Session = Depends(get_db), current_user = Depends(require_kiosk_or_admin)):
    """
    Retrieves the list of files and directories contained inside a specific backup archive.
    """
    history = db.query(models.BackupHistory).filter(models.BackupHistory.id == history_id).first()
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup history record not found.")

    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borg repository does not exist.")

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    try:
        cmd = ["borg", "list", "--json-lines", f"{repo_path}::{history.archive_name}"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Borg list failed: {res.stderr.strip()}")

        file_items = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                path = item.get("path", "")
                mode = item.get("mode", "")
                is_dir = mode.startswith("d") if mode else False
                file_items.append(schemas.ArchiveFileInfo(
                    path=path,
                    size=item.get("size", 0),
                    mtime=item.get("mtime"),
                    mode=mode,
                    is_dir=is_dir
                ))
            except json.JSONDecodeError:
                continue

        return schemas.ArchiveFileListResponse(
            archive_name=history.archive_name,
            files=file_items
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Listing archive files timed out.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/history/{history_id}/file-content", response_model=schemas.ArchiveFileContentResponse)
def get_archive_file_content(history_id: int, path: str, db: Session = Depends(get_db), current_user = Depends(require_kiosk_or_admin)):
    """
    Safely extracts and reads text content of a specific file inside a backup archive.
    """
    history = db.query(models.BackupHistory).filter(models.BackupHistory.id == history_id).first()
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup history record not found.")

    clean_path = path.strip().lstrip("/")
    if not clean_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path.")

    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borg repository does not exist.")

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    try:
        cmd = ["borg", "extract", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        max_bytes = 500 * 1024
        raw_bytes = proc.stdout.read(max_bytes + 1)
        proc.stdout.close()
        proc.stderr.close()
        proc.wait(timeout=10)

        if len(raw_bytes) > max_bytes:
            return schemas.ArchiveFileContentResponse(
                path=clean_path,
                is_text=False,
                size=len(raw_bytes),
                content=None,
                message="File exceeds maximum preview size of 500 KB."
            )

        if b"\x00" in raw_bytes:
            return schemas.ArchiveFileContentResponse(
                path=clean_path,
                is_text=False,
                size=len(raw_bytes),
                content=None,
                message="Binary file cannot be displayed as text."
            )

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text_content = raw_bytes.decode("latin-1")
            except Exception:
                return schemas.ArchiveFileContentResponse(
                    path=clean_path,
                    is_text=False,
                    size=len(raw_bytes),
                    content=None,
                    message="File encoding is not readable as text."
                )

        return schemas.ArchiveFileContentResponse(
            path=clean_path,
            is_text=True,
            size=len(raw_bytes),
            content=text_content
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Extracting file content timed out.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/history/{history_id}/download-file")
def download_archive_file(
    history_id: int,
    path: str,
    is_dir: bool = False,
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin)
):
    """
    Streams and downloads a single file or packages an entire folder into a ZIP archive for client download.
    """
    history = db.query(models.BackupHistory).filter(models.BackupHistory.id == history_id).first()
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup history record not found.")

    clean_path = path.strip().lstrip("/")
    if not clean_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path.")

    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borg repository does not exist.")

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    if is_dir:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, "archive.zip")

        try:
            cmd = ["borg", "extract", f"{repo_path}::{history.archive_name}", clean_path]
            res = subprocess.run(cmd, env=env, cwd=temp_dir, capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Borg folder extraction failed: {res.stderr.strip()}"
                )

            extracted_target = os.path.join(temp_dir, clean_path)
            folder_name = os.path.basename(clean_path.rstrip("/")) or "folder"

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                if os.path.exists(extracted_target):
                    if os.path.isdir(extracted_target):
                        for root, _, files in os.walk(extracted_target):
                            for file in files:
                                full_file_path = os.path.join(root, file)
                                arcname = os.path.relpath(full_file_path, os.path.dirname(extracted_target))
                                zf.write(full_file_path, arcname)
                    else:
                        zf.write(extracted_target, os.path.basename(extracted_target))

            def iter_zip():
                try:
                    with open(zip_path, "rb") as f:
                        while True:
                            chunk = f.read(64 * 1024)
                            if not chunk:
                                break
                            yield chunk
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

            encoded_filename = f"{folder_name}.zip".replace('"', '\\"')
            return StreamingResponse(
                iter_zip(),
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{encoded_filename}"'
                }
            )
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    cmd = ["borg", "extract", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def iterfile():
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.stdout.close()
            proc.stderr.close()
            proc.wait()

    filename = os.path.basename(clean_path) or "download"
    encoded_filename = filename.replace('"', '\\"')

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{encoded_filename}"'
        }
    )




@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(node_id: int, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Deletes a node and its related backup history records from the database,
    cleans up its specific backup archives from the shared repository, and removes its restricted
    SSH public key entry from /root/.ssh/authorized_keys.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    # 1. Clean up node archives in the shared Borg repository
    repo_path = "/data/borg/fleet"
    if os.path.exists(repo_path) and os.path.exists(os.path.join(repo_path, "config")):
        try:
            env = os.environ.copy()
            env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
            cmd = ["borg", "delete", "--glob-archives", f"{node.hostname}-*", repo_path]
            subprocess.run(cmd, env=env, capture_output=True, text=True)
            
            from tasks import fix_repo_permissions
            fix_repo_permissions(repo_path)
        except Exception as e:
            print(f"WARNING: Failed to delete archives for {node.hostname} from shared repo: {str(e)}")

    # 2. Clean up SSH authorized_keys entry safely
    authorized_keys_path = "/root/.ssh/authorized_keys"
    if os.path.exists(authorized_keys_path) and node.ssh_pub_key:
        try:
            with open(authorized_keys_path, "r") as f:
                lines = f.readlines()
            
            new_lines = [line for line in lines if node.ssh_pub_key not in line]
            
            with open(authorized_keys_path, "w") as f:
                f.writelines(new_lines)
                
            from tasks import fix_ssh_permissions
            fix_ssh_permissions()
        except Exception as e:
            print(f"WARNING: Failed to clean up SSH authorized_keys for {node.hostname}: {str(e)}")

    # 3. Delete related backup histories first to prevent foreign key errors
    db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).delete()
    
    # Clean up bootstrap credentials from Redis
    try:
        redis_client.delete(f"bootstrap_creds:{node.id}")
    except Exception:
        pass

    db.delete(node)
    db.commit()
    from database import log_user_action
    username = getattr(current_user, "username", "test_admin")
    log_user_action(db, username, "Delete Node", f"Deleted node '{node.hostname}' (IP: {node.ip_address})", request)
