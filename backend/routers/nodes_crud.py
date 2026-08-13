import os
import subprocess
import ipaddress
import redis
import json
import datetime
import tempfile
import shutil
import zipfile
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, defer
from database import get_db
import models
import schemas
from tasks import run_bootstrap_task, purge_node_archives
from tasks.bootstrap import revoke_node_access_task
from core import repo_usage, ssh_keys
from core.borg_local import borg_kwargs, grant_workdir
from auth import require_admin, require_kiosk_or_admin
from routers.deps import node_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nodes", tags=["Nodes"])

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL)


def _mget(keys):
    """One round trip for many keys, tolerating a Redis that is unavailable."""
    if not keys:
        return {}
    try:
        values = redis_client.mget(keys)
    except Exception:
        return {}
    return dict(zip(keys, values))


def _backup_run_state(node_id: int, raw=None, _prefetched: bool = False):
    """Whether a backup is in flight for this node, and how far along it looks.

    Reads the `backup_running:` key, then confirms against Celery, because the
    key outlives a worker that died mid-backup. Stale keys are deleted on
    sight so the next caller does not pay for the same check.

    `raw` lets a caller that already fetched the key in a batch pass it in,
    so listing a page of nodes costs one MGET instead of one GET per node.

    The progress figure is a fabrication and is labelled as one here so nobody
    mistakes it for a byte count: borg does not report percentage-complete over
    this path, so we render an exponential curve that approaches but never
    reaches 100% over roughly a 45-second time constant. It exists to show
    that something is happening, not how much is left.
    """
    import math
    import time

    is_running = False
    progress = 0
    running_task_id = None
    try:
        val = raw if _prefetched else redis_client.get(f"backup_running:{node_id}")
        if not val:
            return False, 0, None

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
        if start_time == 1 or (not running_task_id and (int(time.time()) - start_time > 600)):
            redis_client.delete(f"backup_running:{node_id}")
            return False, 0, None

        if running_task_id:
            from celery_app import celery_app
            if celery_app.AsyncResult(running_task_id).ready():
                redis_client.delete(f"backup_running:{node_id}")
                return False, 0, None

        # This progress figure is invented, not measured.
        #
        # borg reports its byte counters on the node's stderr, which the backup
        # task consumes but does not publish anywhere the fleet list can read.
        # Rather than show nothing next to a running backup, the bar is drawn
        # from elapsed time through a saturating curve: fast at first, then
        # asymptotic, capped at 99 so it never claims to be finished. 45.0 is
        # the time constant in seconds — after ~45s it reads 63%, after ~2min
        # about 93% — chosen to feel right for a typical incremental, and
        # meaning nothing at all for a first full backup that runs for hours.
        #
        # It is a spinner with numbers on it. Do not use it for anything that
        # needs to be true: BackupHistory.duration_seconds is the real figure,
        # recorded once the transfer ends.
        elapsed = max(0, int(time.time()) - start_time)
        progress = max(0, min(99, int(100 * (1 - math.exp(-elapsed / 45.0)))))
    except Exception:
        return False, 0, None

    return is_running, progress, running_task_id


def _serialize_node(
    node: models.Node,
    repo_size: int,
    running_raw=None,
    retry_raw=None,
    prefetched: bool = False,
) -> dict:
    """Shape a Node row the way the API returns it.

    Shared by the list endpoint and the single-node endpoint so the two cannot
    drift into describing the same node differently.

    When `prefetched` is set, the two Redis values have already been fetched in
    bulk by the caller and no per-node round trip is made.
    """
    is_running, progress, running_task_id = _backup_run_state(
        node.id, raw=running_raw, _prefetched=prefetched
    )

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
        "upload_rate_limit": node.upload_rate_limit,
        "is_backup_running": is_running,
        "backup_progress": progress,
        "backup_task_id": running_task_id,
        "last_ping_status": node.last_ping_status,
        "last_available_at": node.last_available_at,
    }

    if node.status == "OFFLINE":
        try:
            next_retry = (
                retry_raw if prefetched
                else redis_client.get(f"node_next_retry:{node.id}")
            )
            if next_retry:
                node_dict["next_retry_at"] = datetime.datetime.fromtimestamp(
                    int(next_retry), tz=datetime.timezone.utc
                )
        except Exception:
            pass

    return node_dict

#: Ceiling on how many addresses one entry may expand to.
#:
#: A CIDR block is expanded eagerly into a list of strings, so the operator
#: who types a /16 in the bulk-add box asks for 65,534 of them — and then a
#: Node row and a ping schedule for each. A /8 would be 16 million and take
#: the API process down with it. /22 (1022 hosts) is comfortably more than any
#: real site and still cheap to materialise.
MAX_EXPANDED_IPS = 1024


def parse_ip_input(ip_input: str) -> List[str]:
    """
    Parses IP input which can be single IP, comma/newline-separated list,
    ranges (e.g. 192.168.1.50-100 or 192.168.1.50-192.168.1.60), or CIDR block.

    Raises ValueError if the input expands past MAX_EXPANDED_IPS, rather than
    quietly producing a truncated list — a bulk add that silently skipped half
    a subnet would be discovered weeks later, as nodes that were never backed
    up.
    """
    cleaned = ip_input.replace("\n", ",").replace(" ", ",")
    raw_entries = [r.strip() for r in cleaned.split(",") if r.strip()]
    ips = []
    for entry in raw_entries:
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
                if net.num_addresses > MAX_EXPANDED_IPS:
                    raise ValueError(
                        f"{entry} covers {net.num_addresses} addresses; "
                        f"at most {MAX_EXPANDED_IPS} can be added at once."
                    )
                ips.extend([str(ip) for ip in net.hosts()])
            except ValueError:
                raise
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
    page: int = Query(1, ge=1),
    # Bounded, unlike the bare `limit: int = 50` this replaced: each node in
    # the page costs Redis and Celery lookups, so an unbounded limit let one
    # request fan out across the entire fleet.
    limit: int = Query(50, ge=1, le=200),
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

    # Shared repository size, from the memoised `du` in core.repo_usage.
    #
    # This used to be an os.walk + getsize over the whole fleet repository,
    # inline in the handler, uncached. The fleet tab polls this endpoint every
    # five seconds per open browser and re-fires it on every search keystroke,
    # so a repository with a few hundred thousand segment files turned the node
    # list into seconds of stat() calls — and, being a sync handler, it did
    # that inside Starlette's bounded threadpool, stalling unrelated requests
    # once enough of them piled up.
    shared_repo_size = repo_usage.repo_size_bytes() or 0

    # Two MGETs for the whole page instead of one or two GETs per node.
    running_raw = _mget([f"backup_running:{n.id}" for n in nodes])
    retry_raw = _mget([
        f"node_next_retry:{n.id}" for n in nodes if n.status == "OFFLINE"
    ])

    results = [
        _serialize_node(
            node,
            shared_repo_size if node.last_backup else 0,
            running_raw=running_raw.get(f"backup_running:{node.id}"),
            retry_raw=retry_raw.get(f"node_next_retry:{node.id}"),
            prefetched=True,
        )
        for node in nodes
    ]

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
    # log_output deferred: it holds the full borg output of every run and the
    # history table never renders it, so shipping it multiplied each page by
    # several megabytes.
    query = (
        db.query(models.BackupHistory)
        .options(defer(models.BackupHistory.log_output))
        .outerjoin(models.Node, models.BackupHistory.node_id == models.Node.id)
    )
    
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

    try:
        ips = parse_ip_input(payload.ip_address)
    except ValueError as e:
        # Too large to expand — say so, rather than working through a million
        # addresses inside the request.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
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
def get_node_history(
    node_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """Backup snapshot history for one node, newest first.

    Bounded and log-free. This used to return every row a node had ever
    produced, `log_output` included — that column holds the full borg output
    of each run, so a node with a year of dailies answered a details-modal
    open with several megabytes of text the UI never renders. The kiosk
    fetches this over the WAN during a restore, where it hurt most.
    """
    return (
        db.query(models.BackupHistory)
        .options(defer(models.BackupHistory.log_output))
        .filter(models.BackupHistory.node_id == node_id)
        .order_by(models.BackupHistory.timestamp.desc())
        .limit(limit)
        .all()
    )


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
        cmd = ["borg", "list", "--bypass-lock", "--json-lines", f"{repo_path}::{history.archive_name}"]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, **borg_kwargs(repo_path, env))
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
        cmd = ["borg", "extract", "--bypass-lock", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **borg_kwargs(repo_path, env))

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
        # Scratch space lives next to the repository itself rather than the
        # container's own /tmp: extracting a whole folder can be as large as
        # the folder in the backup, and repository storage is what deployments
        # size for that — /tmp is typically the small root disk.
        tmp_root = os.path.join(os.path.dirname(repo_path), "tmp")
        os.makedirs(tmp_root, exist_ok=True)
        os.chmod(tmp_root, 0o755)  # traversable regardless of which uid borg_kwargs picks below
        temp_dir = tempfile.mkdtemp(dir=tmp_root)
        zip_path = os.path.join(temp_dir, "archive.zip")
        # borg extracts into its working directory, so hand the temp dir to
        # whichever identity we are about to run it as.
        grant_workdir(temp_dir, repo_path)

        try:
            cmd = ["borg", "extract", "--bypass-lock", f"{repo_path}::{history.archive_name}", clean_path]
            res = subprocess.run(cmd, env=env, cwd=temp_dir, capture_output=True, text=True, timeout=180, **borg_kwargs(repo_path, env))
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

    cmd = ["borg", "extract", "--bypass-lock", "--stdout", f"{repo_path}::{history.archive_name}", clean_path]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **borg_kwargs(repo_path, env))

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
    node = node_or_404(db, node_id)
    
    # 1. Clean up node archives in the shared Borg repository
    repo_path = "/data/borg/fleet"
    if os.path.exists(repo_path) and os.path.exists(os.path.join(repo_path, "config")):
        try:
            env = os.environ.copy()
            env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
            cmd = ["borg", "delete", "--glob-archives", f"{node.hostname}-*", repo_path]
            subprocess.run(cmd, env=env, capture_output=True, text=True, **borg_kwargs(repo_path, env))

            from tasks import fix_repo_permissions
            fix_repo_permissions(repo_path)
        except Exception as e:
            print(f"WARNING: Failed to delete archives for {node.hostname} from shared repo: {str(e)}")

    # 2. Withdraw the node's borg grant, and ask the node to drop ours.
    snapshot = {
        "hostname": node.hostname,
        "ip_address": node.ip_address,
        "ssh_port": node.ssh_port,
    }
    if node.ssh_pub_key:
        try:
            action = ssh_keys.revoke(ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS, node.ssh_pub_key)
            from tasks import fix_ssh_permissions
            fix_ssh_permissions()
            logger.info("Borg grant for %s: %s", node.hostname, action.value)
        except Exception as e:
            logger.warning("Failed to revoke borg grant for %s: %s", node.hostname, str(e))

    try:
        # retry=False so an unreachable broker fails immediately instead of
        # blocking this request; deletion must not depend on the queue.
        revoke_node_access_task.apply_async(
            args=[snapshot["hostname"], snapshot["ip_address"], snapshot["ssh_port"]],
            retry=False,
        )
        logger.info("Dispatched access revocation for %s", snapshot["hostname"])
    except Exception as e:
        logger.warning(
            "Could not dispatch access revocation for %s; the orchestrator key may "
            "remain on that host: %s", snapshot["hostname"], str(e)
        )

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


@router.get("/{node_id}", response_model=schemas.NodeResponse)
def get_node(
    node_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(require_kiosk_or_admin),
):
    """One node, in the same shape the list endpoint returns.

    Registered last on purpose: `/{node_id}` would otherwise swallow the
    literal sibling paths under this prefix (`/history`, and the
    `/history/{history_id}/...` family), which FastAPI matches in
    registration order.

    This exists because the details view used to fetch the *list* and search
    it for one node. The list is paginated at 50, so opening any node beyond
    the first page found nothing and left the modal stuck on its loading
    state — for a 2000-node fleet, that was 97% of them.

    The repository size is deliberately not computed here: it is a
    fleet-shared figure that costs a full walk of the borg repo, and no
    single-node view has ever displayed it.
    """
    node = node_or_404(db, node_id)
    return _serialize_node(node, 0)
