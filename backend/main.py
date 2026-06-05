import os
import subprocess
import uuid
import ipaddress
from typing import List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from tasks import run_bootstrap_task, run_prepare_task, run_backup_task, flash_restore_device, ensure_orchestrator_ssh_key, purge_node_archives
from version import VERSION

app = FastAPI(title="Borg Backup & Bare-Metal Restore Orchestrator API", version=VERSION)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_ip_input(ip_input: str) -> List[str]:
    """
    Parses IP input which can be single IP, comma/newline-separated list,
    ranges (e.g. 192.168.1.50-100 or 192.168.1.50-192.168.1.60), or CIDR block.

    Args:
        ip_input: The raw string from the user form.

    Returns:
        List of parsed valid IP address strings.
    """
    # Clean input - convert newlines and spaces to commas
    cleaned = ip_input.replace("\n", ",").replace(" ", ",")
    raw_entries = [r.strip() for r in cleaned.split(",") if r.strip()]
    ips = []
    for entry in raw_entries:
        if "/" in entry:
            # CIDR block
            try:
                net = ipaddress.ip_network(entry, strict=False)
                # Add all hosts in network
                ips.extend([str(ip) for ip in net.hosts()])
            except Exception:
                pass
        elif "-" in entry:
            # IP range
            try:
                parts = entry.split("-")
                start_str = parts[0].strip()
                end_str = parts[1].strip()
                if "." in end_str:
                    # Full IP range e.g. 192.168.1.50-192.168.1.60
                    start_ip = ipaddress.ip_address(start_str)
                    end_ip = ipaddress.ip_address(end_str)
                    curr = start_ip
                    while curr <= end_ip:
                        ips.append(str(curr))
                        curr += 1
                else:
                    # Short range e.g. 192.168.1.50-60
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
            # Single IP
            try:
                ipaddress.ip_address(entry)
                ips.append(entry)
            except Exception:
                pass
    return list(dict.fromkeys(ips)) # remove duplicates

@app.on_event("startup")
def startup_db_init():
    """
    Ensure settings are initialized in the database on startup and orchestrator SSH keys are ready.
    """
    try:
        ensure_orchestrator_ssh_key()
    except Exception as e:
        print(f"Error ensuring SSH keypair on startup: {str(e)}")

    # Ensure permissions of the shared borg storage are correct from day one
    try:
        from tasks import fix_repo_permissions
        fix_repo_permissions("/data/borg/fleet")
    except Exception as e:
        print(f"Error ensuring repository permissions on startup: {str(e)}")

    db = next(get_db())
    upgrade_settings(db)
    db.close()


def upgrade_settings(db: Session):
    """
    Upgrade old default exclusions to the new default if unchanged by user.
    """
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
    else:
        # Upgrade old default exclusions to the new default if unchanged by user
        old_defaults = [
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1'
        ]
        new_default = (
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,'
            '/var/log/edge/*,/var/opt/edge/blobstore/*,/var/spool/edge/*,/var/log/journal/*,'
            '/var/log/**/*.gz,/var/log/**/*.1'
        )
        if settings.global_exclusions in old_defaults:
            settings.global_exclusions = new_default
            db.commit()

@app.get("/api/version")
def get_version():
    """
    Returns the current application version.
    """
    return {"version": VERSION}


@app.get("/api/settings", response_model=schemas.SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    """
    Retrieves global orchestrator settings.
    """
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
    return settings


@app.post("/api/settings", response_model=schemas.SettingsResponse)
def update_settings(payload: schemas.SettingsBase, db: Session = Depends(get_db)):
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
    db.commit()
    return settings


@app.get("/api/nodes", response_model=List[schemas.NodeResponse])
def get_nodes(db: Session = Depends(get_db)):
    """
    Retrieves lists of all nodes.
    """
    return db.query(models.Node).all()


@app.post("/api/nodes", status_code=status.HTTP_201_CREATED)
def add_node(payload: schemas.NodeCreate, db: Session = Depends(get_db)):
    """
    Registers one or more new nodes (by parsing comma-separated, ranges, or CIDR)
    and triggers their background bootstrap tasks.
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
        # Determine hostname suffix
        current_hostname = payload.hostname if len(ips) == 1 else f"{payload.hostname}-{idx+1}"

        # Check duplicate
        existing = db.query(models.Node).filter(
            (models.Node.hostname == current_hostname) | 
            (models.Node.ip_address == ip)
        ).first()
        
        if existing:
            # Skip duplicates to allow partial successes in range additions
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

        # Spawn bootstrap task
        task = run_bootstrap_task.delay(node.id, payload.bootstrap_password, payload.bootstrap_user)
        
        created_nodes.append(node)
        task_ids.append(task.id)
        node_ids.append(node.id)

    if not created_nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All parsed nodes already exist in the database."
        )

    # Return the first task_id and node_id so the frontend logs drawer triggers immediately
    return {
        "message": f"Successfully registered {len(created_nodes)} node(s). Bootstrap triggered.",
        "task_id": task_ids[0],
        "node_id": node_ids[0],
        "all_task_ids": task_ids,
        "all_node_ids": node_ids
    }


@app.post("/api/nodes/{node_id}/prepare")
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


@app.post("/api/nodes/{node_id}/backup")
def trigger_backup(node_id: int, db: Session = Depends(get_db)):
    """
    Triggers immediate remote backup execution.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    task = run_backup_task.delay(node.id)
    return {"message": "Backup execution task triggered.", "task_id": task.id}


@app.get("/api/nodes/{node_id}/history", response_model=List[schemas.BackupHistoryResponse])
def get_node_history(node_id: int, db: Session = Depends(get_db)):
    """
    Retrieves the backup snapshot history records for a specific node.
    """
    return db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).all()


@app.get("/api/tasks/{task_id}", response_model=schemas.TaskLogResponse)
def get_task_logs(task_id: str, db: Session = Depends(get_db)):
    """
    Fetches execution logs and status of a background task.
    """
    task = db.query(models.TaskLog).filter(models.TaskLog.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@app.get("/api/scanner/devices", response_model=List[schemas.DeviceResponse])
def scan_devices():
    """
    Scans the orchestrator host for physical block devices (SATA/NVMe).
    Filters out the orchestrator's own root system drive.
    """
    devices = []
    try:
        # Resolve parent physical disks of the container's volume mounts to filter them out
        host_root_disks = set()
        
        # We try to detect the physical disk where /app, /root/.ssh, or /data/borg reside
        for mp in ["/app", "/root/.ssh", "/data/borg", "/"]:
            app_dev = None
            if os.path.exists("/proc/self/mountinfo"):
                with open("/proc/self/mountinfo", "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 5 and parts[4] == mp:
                            app_dev = parts[2]
                            break
            if app_dev:
                try:
                    sys_path = os.path.realpath(f"/sys/dev/block/{app_dev}")
                    block_name = os.path.basename(sys_path)
                    
                    def resolve_physical_disks(name):
                        slaves_path = f"/sys/block/{name}/slaves"
                        if os.path.exists(slaves_path) and os.listdir(slaves_path):
                            for slave in os.listdir(slaves_path):
                                resolve_physical_disks(slave)
                        else:
                            real_path = os.path.realpath(f"/sys/class/block/{name}")
                            parts = real_path.split("/")
                            if "block" in parts:
                                idx = parts.index("block")
                                if idx + 1 < len(parts):
                                    host_root_disks.add(parts[idx+1])
                            else:
                                host_root_disks.add(name)

                    resolve_physical_disks(block_name)
                except Exception:
                    pass

        # Also fallback to basic findmnt detection if possible
        try:
            findmnt_out = subprocess.check_output("findmnt -n -o SOURCE /", shell=True, text=True).strip()
            host_root_disk = os.path.basename(findmnt_out)
            if host_root_disk != "overlay":
                if "nvme" in host_root_disk:
                    host_root_disk = host_root_disk.split("p")[0]
                else:
                    host_root_disk = "".join([c for c in host_root_disk if not c.isdigit()])
                host_root_disks.add(host_root_disk)
        except Exception:
            pass

        # Run lsblk to list devices
        lsblk_cmd = "lsblk -dno NAME,SIZE,MODEL,RO || lsblk -dno NAME,SIZE,MODEL"
        lsblk_out = subprocess.check_output(lsblk_cmd, shell=True, text=True).strip()

        for line in lsblk_out.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            size_str = parts[1].strip()
            model = parts[2].strip() if len(parts) > 2 else "Generic Disk"

            # Skip loop, ram, and host root drives
            if name.startswith("loop") or name.startswith("ram") or name in host_root_disks:
                continue

            # Check rotational flag
            rotational_path = f"/sys/block/{name}/queue/rotational"
            rotational = True
            if os.path.exists(rotational_path):
                with open(rotational_path, "r") as f:
                    rotational = f.read().strip() == "1"

            # Check if connected via USB
            is_usb = False
            try:
                real_block_path = os.path.realpath(f"/sys/block/{name}")
                is_usb = any(part.startswith("usb") for part in real_block_path.split("/"))
            except Exception:
                pass

            # Disk Type classification
            disk_type = "SATA"
            if "nvme" in name.lower() or "nvme" in model.lower() or "pcie" in model.lower():
                disk_type = "NVME"

            # Convert human size string to bytes estimation
            size_bytes = 0
            try:
                numeric_part = float("".join([c for c in size_str if c.isdigit() or c == "."]))
                if "G" in size_str:
                    size_bytes = int(numeric_part * 1024 * 1024 * 1024)
                elif "T" in size_str:
                    size_bytes = int(numeric_part * 1024 * 1024 * 1024 * 1024)
                elif "M" in size_str:
                    size_bytes = int(numeric_part * 1024 * 1024)
                else:
                    size_bytes = int(numeric_part)
            except Exception:
                pass

            devices.append(schemas.DeviceResponse(
                name=f"/dev/{name}",
                size=size_bytes,
                model=model,
                rotational=rotational,
                disk_type=disk_type,
                is_usb=is_usb
            ))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to scan local devices: {str(e)}"
        )
    return devices


@app.post("/api/restore")
def trigger_restore(payload: schemas.RestoreRequest, db: Session = Depends(get_db)):
    """
    Triggers bare-metal flashing restore process.
    Validates NVMe/SATA mismatch and starts flashing task.
    """
    node = db.query(models.Node).filter(models.Node.id == payload.node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    if not node.efi_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot restore. The node's EFI ESP partition UUID was not collected. Run 'Auto-Prepare' on the node first."
        )

    # Hardware Mismatch Check
    target_name = os.path.basename(payload.target_dev)
    target_disk_type = "SATA"
    if "nvme" in target_name.lower():
        target_disk_type = "NVME"
    else:
        # Check model name in sysfs for USB bridges
        model_path = f"/sys/block/{target_name}/device/model"
        if os.path.exists(model_path):
            try:
                with open(model_path, "r") as f:
                    model_content = f.read().strip().lower()
                if "nvme" in model_content or "pcie" in model_content:
                    target_disk_type = "NVME"
            except Exception:
                pass

    if node.disk_type != "UNKNOWN" and node.disk_type != target_disk_type:
        if not payload.override_mismatch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"DISK TYPE MISMATCH WARNING: The backup node used {node.disk_type} but the target is {target_disk_type}. Confirmation required to proceed."
            )

    task = flash_restore_device.delay(
        node.id, 
        payload.archive_name, 
        payload.target_dev,
        keep_network_configs=payload.keep_network_configs,
        wipe_mac_bindings=payload.wipe_mac_bindings
    )
    return {"message": "Restore flashing process started.", "task_id": task.id}



@app.delete("/api/nodes/{node_id}/archives")
def purge_node_backups(node_id: int, db: Session = Depends(get_db)):
    """
    Deletes all Borg backup archives for a specific node.
    The Borg repository itself is preserved (initialization is kept).
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    task = purge_node_archives.delay(node.id)
    return {"message": f"Purge of all archives for '{node.hostname}' started.", "task_id": task.id}


@app.delete("/api/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
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
            
            # Ensure permissions are kept aligned
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
    
    db.delete(node)
    db.commit()


@app.get("/api/stats")
def get_global_stats(db: Session = Depends(get_db)):
    """
    Retrieves global metrics including storage dedup ratios.
    """
    histories = db.query(models.BackupHistory).filter(models.BackupHistory.status == "SUCCESS").all()
    total_original = sum(h.original_size for h in histories)
    total_deduplicated = sum(h.deduplicated_size for h in histories)
    
    ratio = 1.0
    if total_deduplicated > 0:
        ratio = round(total_original / total_deduplicated, 2)

    return {
        "total_nodes": db.query(models.Node).count(),
        "total_original_size_bytes": total_original,
        "total_deduplicated_size_bytes": total_deduplicated,
        "deduplication_ratio": ratio
    }
