import os
import subprocess
import ipaddress
import redis
import json
import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from tasks import run_bootstrap_task, run_prepare_task, run_backup_task, purge_node_archives

router = APIRouter(prefix="/api/nodes")

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


@router.get("", response_model=List[schemas.NodeResponse])
def get_nodes(db: Session = Depends(get_db)):
    """
    Retrieves lists of all nodes.
    """
    nodes = db.query(models.Node).all()
    results = []
    for node in nodes:
        # Calculate repository size on disk
        repo_size = None
        repo_dir = f"/data/borg/fleet/{node.hostname}"
        if os.path.exists(repo_dir):
            try:
                repo_size = 0
                for root, dirs, files in os.walk(repo_dir):
                    for file in files:
                        repo_size += os.path.getsize(os.path.join(root, file))
            except Exception:
                repo_size = None

        node_dict = {
            "id": node.id,
            "hostname": node.hostname,
            "ip_address": node.ip_address,
            "ssh_port": node.ssh_port,
            "status": node.status,
            "last_backup": node.last_backup,
            "disk_type": node.disk_type,
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
            "notes": node.notes
        }
        if node.status == "OFFLINE":
            try:
                next_retry = redis_client.get(f"node_next_retry:{node.id}")
                if next_retry:
                    node_dict["next_retry_at"] = datetime.datetime.fromtimestamp(int(next_retry), tz=datetime.timezone.utc)
            except Exception:
                pass
        results.append(node_dict)
    return results


@router.get("/history", response_model=List[schemas.BackupHistoryResponse])
def get_all_history(db: Session = Depends(get_db)):
    """
    Retrieves backup snapshot history records for all nodes.
    """
    return db.query(models.BackupHistory).order_by(models.BackupHistory.timestamp.desc()).all()


@router.post("", status_code=status.HTTP_201_CREATED)
def add_node(payload: schemas.NodeCreate, db: Session = Depends(get_db)):
    """
    Registers one or more new nodes and triggers bootstrap.
    """
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

    return {
        "message": f"Successfully registered {len(created_nodes)} node(s). Bootstrap triggered.",
        "task_id": task_ids[0],
        "node_id": node_ids[0],
        "all_task_ids": task_ids,
        "all_node_ids": node_ids
    }


@router.post("/{node_id}/prepare")
def trigger_prepare(node_id: int, db: Session = Depends(get_db)):
    """
    Triggers the Auto-Prepare disk labels playbook task for a node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    node.status = "NEEDS_FIX"
    db.commit()

    task = run_prepare_task.delay(node.id)
    return {"message": "Auto-prepare playbook execution triggered.", "task_id": task.id}


@router.post("/{node_id}/backup")
def trigger_backup(node_id: int, payload: schemas.BackupTriggerRequest = None, db: Session = Depends(get_db)):
    """
    Triggers immediate remote backup execution.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    comment = payload.comment if payload else None
    task = run_backup_task.delay(node.id, comment=comment)
    return {"message": "Backup execution task triggered.", "task_id": task.id}


@router.get("/{node_id}/history", response_model=List[schemas.BackupHistoryResponse])
def get_node_history(node_id: int, db: Session = Depends(get_db)):
    """
    Retrieves the backup snapshot history records for a specific node.
    """
    return db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).all()


@router.delete("/{node_id}/archives")
def purge_node_backups(node_id: int, db: Session = Depends(get_db)):
    """
    Deletes all Borg backup archives for a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    task = purge_node_archives.delay(node.id)
    return {"message": f"Purge of all archives for '{node.hostname}' started.", "task_id": task.id}


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(node_id: int, db: Session = Depends(get_db)):
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


@router.post("/{node_id}/provision")
def trigger_provision(node_id: int, payload: schemas.NodeProvisionRequest, db: Session = Depends(get_db)):
    """
    Triggers bootstrap on an existing node, caching its credentials in Redis.
    """
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
    return {"message": "Provisioning triggered.", "task_id": task.id}


@router.post("/{node_id}/notes")
def update_node_notes(node_id: int, payload: schemas.NodeNotesUpdate, db: Session = Depends(get_db)):
    """
    Updates the notes field for a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.notes = payload.notes
    db.commit()
    return {"message": "Node notes updated successfully."}


@router.post("/{node_id}/backup-today")
def trigger_backup_today(node_id: int, db: Session = Depends(get_db)):
    """
    Sets backup_today to True for the node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.backup_today = True
    db.commit()
    return {"message": "Node queued for backup execution during the next window."}


@router.post("/{node_id}/toggle-pause")
def toggle_backup_pause(node_id: int, db: Session = Depends(get_db)):
    """
    Toggles backup_paused state for the node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    
    node.backup_paused = not node.backup_paused
    db.commit()
    return {"message": "Backup status toggled successfully.", "backup_paused": node.backup_paused}


@router.post("/{node_id}/assign-group/{group_id}")
def assign_node_group(node_id: int, group_id: int, db: Session = Depends(get_db)):
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
    return {"message": "Node group assignment updated successfully."}


@router.get("/{node_id}/task-logs", response_model=List[schemas.TaskLogResponse])
def get_node_task_logs(node_id: int, db: Session = Depends(get_db)):
    """
    Retrieves background execution logs associated with a specific node.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    return db.query(models.TaskLog).filter(models.TaskLog.node_id == node_id).order_by(models.TaskLog.created_at.desc()).limit(20).all()
