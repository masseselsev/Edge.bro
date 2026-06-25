import os
import subprocess
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas
from tasks import flash_restore_device

from routers.users import require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])

@router.get("/scanner/devices", response_model=List[schemas.DeviceResponse])
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

            # Skip loop, ram, virtual disks (vd*), and host root drives
            if name.startswith("loop") or name.startswith("ram") or name.startswith("vd") or name in host_root_disks:
                continue

            model_lower = model.lower()
            if any(term in model_lower for term in ["vbox", "qemu", "vmware", "virtual", "xen"]):
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


@router.post("/restore")
def trigger_restore(payload: schemas.RestoreRequest, request: Request = None, db: Session = Depends(get_db), current_user = Depends(require_admin)):
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
    from database import log_user_action
    log_user_action(db, current_user.username, "Trigger Restore", f"Triggered bare-metal flashing restore of node '{node.hostname}' using archive '{payload.archive_name}' onto target device '{payload.target_dev}'", request)
    return {"message": "Restore flashing process started.", "task_id": task.id}
