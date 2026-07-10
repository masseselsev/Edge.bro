import os
import subprocess
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Request, File, UploadFile
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

    node_base_type = "NVME" if node.disk_type.upper().startswith("NVME") else ("SATA" if node.disk_type.upper().startswith("SATA") else "UNKNOWN")
    if node_base_type != "UNKNOWN" and node_base_type != target_disk_type:
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


@router.get("/nodes/{node_id}/hasp-fingerprint")
def get_hasp_fingerprint(node_id: int, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Downloads the genuine Sentinel HASP C2V fingerprint file from the node using the hasp_update tool.
    """
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    import redis
    import os
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    lock_key = f"license_lock:{node_id}"
    redis_client.setex(lock_key, 60, "1")
    
    import subprocess
    import xml.etree.ElementTree as ET
    from fastapi.responses import Response
    
    try:
        # 1. Try to list keys with features using the hasp_update tool
        ssh_cmd_lf = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-p", str(node.ssh_port),
            "-i", "/root/.ssh/id_ed25519",
            f"root@{node.ip_address}",
            ". /opt/edge/rc.setenv && /opt/edge/bin/hasp_update lf"
        ]
        
        haspid = None
        try:
            res_lf = subprocess.run(ssh_cmd_lf, capture_output=True, text=True, timeout=10)
            if res_lf.returncode == 0 and res_lf.stdout.strip():
                # Parse XML output to see if there is a connected HASP key
                root = ET.fromstring(res_lf.stdout.strip())
                hasp_elem = root.find(".//hasp")
                if hasp_elem is not None:
                    haspid = hasp_elem.get("id")
        except Exception:
            pass  # Fall back to machine fingerprint or other methods if lf failed/parsed incorrectly
            
        # 2. Build the command to run hasp_update
        if haspid:
            cmd_str = f". /opt/edge/rc.setenv && /opt/edge/bin/hasp_update i {haspid}"
            filename = f"{node.hostname}_key_{haspid}.c2v"
        else:
            cmd_str = ". /opt/edge/rc.setenv && /opt/edge/bin/hasp_update f"
            filename = f"{node.hostname}_fingerprint.c2v"
            
        ssh_cmd_update = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-p", str(node.ssh_port),
            "-i", "/root/.ssh/id_ed25519",
            f"root@{node.ip_address}",
            cmd_str
        ]
        
        res = subprocess.run(ssh_cmd_update, capture_output=True, text=True, timeout=10)
        content = res.stdout.strip()
        if not content or "<?xml" not in content:
            # Secondary fallback: Use ACC HTTP API if hasp_update failed
            ssh_cmd_acc_list = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-p", str(node.ssh_port),
                "-i", "/root/.ssh/id_ed25519",
                f"root@{node.ip_address}",
                "curl -s http://127.0.0.1:1947/_int_/tab_dev.html"
            ]
            acc_haspid = None
            acc_vid = "107392"
            try:
                res_acc_list = subprocess.run(ssh_cmd_acc_list, capture_output=True, text=True, timeout=10)
                if res_acc_list.returncode == 0 and res_acc_list.stdout.strip():
                    blocks = parse_sentinel_json_blocks(res_acc_list.stdout)
                    for b in blocks:
                        if "ndx" in b:
                            if b.get("haspid") and b.get("haspid") != "0":
                                acc_haspid = b.get("haspid")
                            if b.get("vid"):
                                acc_vid = b.get("vid")
                            elif b.get("ven"):
                                acc_vid = b.get("ven")
                            break
            except Exception:
                pass
                
            if acc_haspid:
                acc_target = f"http://127.0.0.1:1947/download/my.c2v?{acc_haspid}"
                filename = f"{node.hostname}_key_{acc_haspid}.c2v"
            else:
                acc_target = f"http://127.0.0.1:1947/download/my.fp?{acc_vid}"
                filename = f"{node.hostname}_fingerprint.c2v"
                
            ssh_cmd_acc_download = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-p", str(node.ssh_port),
                "-i", "/root/.ssh/id_ed25519",
                f"root@{node.ip_address}",
                f"curl -s '{acc_target}'"
            ]
            res_acc = subprocess.run(ssh_cmd_acc_download, capture_output=True, text=True, timeout=10)
            content = res_acc.stdout.strip()
            
            if not content or "<?xml" not in content:
                # Tertiary fallback: file-based cat
                fallback_cmd = [
                    "ssh", "-o", "StrictHostKeyChecking=no",
                    "-p", str(node.ssh_port),
                    "-i", "/root/.ssh/id_ed25519",
                    f"root@{node.ip_address}",
                    "cat /var/hasplm/fingerprint 2>/dev/null || cat /var/hasplm/*.c2v 2>/dev/null || echo 'NO_FINGERPRINT'"
                ]
                res_fb = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=10)
                content = res_fb.stdout.strip()
                filename = f"{node.hostname}_fingerprint.c2v"
                
                if not content or content == "NO_FINGERPRINT":
                    raise HTTPException(status_code=400, detail="Fingerprint file not found on node. Make sure the node is booted and Sentinel runtime is active.")
        
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to fetch fingerprint: {str(e)}")
    finally:
        try:
            redis_client.delete(lock_key)
        except Exception:
            pass


def parse_sentinel_json_blocks(raw_text: str) -> list:
    import json
    import re
    objs = []
    for block in re.findall(r'\{[^{}]+\}', raw_text):
        try:
            block_clean = re.sub(r'\s+', ' ', block)
            objs.append(json.loads(block_clean))
        except Exception:
            continue
    return objs


@router.get("/nodes/{node_id}/hasp-status", response_model=schemas.HaspStatusResponse)
def get_node_hasp_status(node_id: int, db: Session = Depends(get_db), current_user = Depends(require_admin)):
    """
    Retrieves the live Sentinel HASP license activation status and features list from the node.
    """
    import re
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
        
    if not node.hasp_runtime_version or node.hasp_runtime_version == "None":
        return {"status": "inactive", "features": []}
        
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", str(node.ssh_port),
        "-i", "/root/.ssh/id_ed25519",
        f"root@{node.ip_address}",
        "curl -s --connect-timeout 3 http://localhost:1947/_int_/tab_dev.html && echo '---FEATURES_SEPARATOR---' && curl -s --connect-timeout 3 http://localhost:1947/_int_/tab_feat.html"
    ]
    
    try:
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=8)
        if res.returncode != 0 or not res.stdout.strip():
            return {"status": "unreachable", "features": []}
            
        parts = res.stdout.split('---FEATURES_SEPARATOR---')
        dev_section = parts[0] if len(parts) > 0 else ""
        feat_section = parts[1] if len(parts) > 1 else ""
        
        devices = parse_sentinel_json_blocks(dev_section)
        features = parse_sentinel_json_blocks(feat_section)
        
        real_devices = [d for d in devices if d.get("haspid") and d.get("haspid") != "0" and d.get("typ") != "placeholder"]
        real_features = [f for f in features if f.get("fid") is not None]
        
        if not real_devices:
            return {"status": "no_license", "features": []}
            
        is_cloned = any(d.get("cloned") != "0" for d in real_devices)
        is_disabled = any(d.get("key_disabled") != "0" for d in real_devices)
        
        if is_cloned:
            status_str = "clone_detected"
        elif is_disabled:
            status_str = "disabled"
        else:
            active_features = [f for f in real_features if f.get("fid") != "0"]
            if not active_features:
                status_str = "no_license"
            elif all(f.get("unusable") != "0" for f in active_features):
                status_str = "expired"
            else:
                status_str = "active"
                
        # Format features list
        formatted_features = []
        for f in real_features:
            lic_clean = re.sub(r'<[^<]+?>', ' ', f.get("lic", "")).strip()
            lic_clean = lic_clean.replace("&nbsp;", " ")
            lic_clean = re.sub(r'\s+', ' ', lic_clean)
            if "Expiration Date" in lic_clean:
                lic_clean = lic_clean.replace("Expiration Date", "").replace("23:59", "").strip()
                lic_clean = f"Exp: {lic_clean}"
            
            formatted_features.append({
                "id": str(f.get("fid")),
                "name": f.get("fn") or "Unnamed Feature",
                "product_name": f.get("prname") or "N/A",
                "product_id": str(f.get("prid")) if f.get("prid") else "N/A",
                "lic_type": lic_clean,
                "unusable": str(f.get("unusable", "0")),
                "key_id": str(f.get("haspid", ""))
            })
            
        return {
            "status": status_str,
            "features": formatted_features
        }
    except Exception:
        return {"status": "unreachable", "features": []}


@router.post("/nodes/{node_id}/hasp-license")
async def upload_hasp_license(
    node_id: int, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db), 
    current_user = Depends(require_admin)
):
    """
    Uploads and applies a Sentinel V2C license file to the node.
    """
    import base64
    import re
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
        
    import redis
    import os
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    lock_key = f"license_lock:{node_id}"
    redis_client.setex(lock_key, 60, "1")
    
    try:
        # Check file extension case-insensitively!
        if not file.filename.lower().endswith(('.v2c', '.v2cp', '.h2r', '.r2h', '.h2h')):
            raise HTTPException(status_code=400, detail="Invalid file extension. Please upload a valid Sentinel license file (V2C).")
            
        content = await file.read()
        b64_content = base64.b64encode(content).decode('utf-8')
        
        # Save license in database for auto-application after flasher restore
        node.hasp_license_v2c = b64_content
        db.commit()
        
        # Try applying via hasp_update CLI, fallback to ACC HTTP Checkin if it fails
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-p", str(node.ssh_port),
            "-i", "/root/.ssh/id_ed25519",
            f"root@{node.ip_address}",
            f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
            f"(. /opt/edge/rc.setenv && /opt/edge/bin/hasp_update u /tmp/license.v2c 2>/dev/null && echo 'CLI_SUCCESS' || echo 'CLI_FAILED') && "
            f"rm -f /tmp/license.v2c"
        ]
        
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=20)
        if res.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to communicate with node via SSH: {res.stderr}")
            
        stdout = res.stdout.strip()
        
        if "CLI_SUCCESS" in stdout:
            node.status = "READY"
            db.commit()
            return {"status": "success", "message": "License applied successfully via Sentinel hasp_update!"}
            
        # Fallback to ACC HTTP Checkin
        ssh_cmd_acc = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-p", str(node.ssh_port),
            "-i", "/root/.ssh/id_ed25519",
            f"root@{node.ip_address}",
            f"echo '{b64_content}' | base64 -d > /tmp/license.v2c && "
            f"curl -s -F \"check_in_file=@/tmp/license.v2c\" http://localhost:1947/_int_/checkin_file.html && "
            f"rm -f /tmp/license.v2c"
        ]
        
        res_acc = subprocess.run(ssh_cmd_acc, capture_output=True, text=True, timeout=20)
        if res_acc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to communicate with node via SSH fallback: {res_acc.stderr}")
            
        stdout_acc = res_acc.stdout
        
        error_match = re.search(r'var error\s*=\s*(\d+);', stdout_acc)
        ext_match = re.search(r'var acc_extended_error\s*=\s*"([^"]*)";', stdout_acc)
        
        error_code = int(error_match.group(1)) if error_match else 0
        extended_error = ext_match.group(1) if ext_match else "0"
        
        if error_code != 0:
            raise HTTPException(
                status_code=400,
                detail=f"Attach/Update license failed (Sentinel error code: {error_code}, ext: {extended_error})."
            )
            
        node.status = "READY"
        db.commit()
        return {"status": "success", "message": "License applied successfully via ACC HTTP checkin fallback!"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Error applying license file: {str(e)}")
    finally:
        try:
            redis_client.delete(lock_key)
        except Exception:
            pass
