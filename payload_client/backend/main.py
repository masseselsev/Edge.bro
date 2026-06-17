import os
import subprocess
import json
import logging
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# We will inject the shared disk_ops.py into core/disk_ops.py during ISO generation
try:
    from core.disk_ops import format_and_restore
except ImportError:
    pass # Will be resolved at ISO runtime

# Centralized version logic
try:
    from version import VERSION
except ImportError:
    VERSION = "v0.7beta"

app = FastAPI(title="Offline Technician Client", version=VERSION)

import urllib.request
import urllib.error

# Load Kiosk configuration if present
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
orchestrator_ip = "127.0.0.1"
orchestrator_api_port = 8000
orchestrator_ssh_port = 12345
auth_token = ""
language = "en"
kiosk_uuid = ""

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            orchestrator_ip = cfg.get("orchestrator_ip", "127.0.0.1")
            orchestrator_api_port = cfg.get("orchestrator_api_port", 8000)
            orchestrator_ssh_port = cfg.get("orchestrator_ssh_port", 12345)
            auth_token = cfg.get("auth_token", "")
            language = cfg.get("language", "en")
            kiosk_uuid = cfg.get("kiosk_uuid", "")
    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")

# Generate persistent UUID if not present
if not kiosk_uuid:
    import uuid
    kiosk_uuid = str(uuid.uuid4())
    try:
        cfg_data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                cfg_data = json.load(f)
        cfg_data["kiosk_uuid"] = kiosk_uuid
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg_data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save kiosk_uuid to config.json: {e}")

# Generate local SSH keypair if missing
SSH_KEY_PATH = os.path.join(os.path.dirname(__file__), "id_ed25519")
if not os.path.exists(SSH_KEY_PATH):
    try:
        subprocess.run([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", SSH_KEY_PATH
        ], check=True)
    except Exception as e:
        logging.error(f"Failed to generate kiosk SSH keypair: {e}")


restore_mode = "offline"

# Try to register the shared network configurations router if available
try:
    from routers.network import router as network_router
    app.include_router(network_router, prefix="/api")
except ImportError:
    pass

# Local state to track task progress
task_logs: Dict[str, str] = {}
task_status: Dict[str, str] = {}
task_progress: Dict[str, int] = {}

class RestoreRequest(BaseModel):
    node_id: int
    archive_name: str
    target_dev: str
    override_mismatch: bool = False
    keep_network_configs: bool = True
    wipe_mac_bindings: bool = False

def run_offline_restore(task_id: str, req: RestoreRequest):
    task_status[task_id] = "RUNNING"
    task_progress[task_id] = 0
    task_logs[task_id] = f"Starting bare-metal restore for archive {req.archive_name} to {req.target_dev}\n"

    def log_callback(msg: str, prog: Optional[int] = None, status: Optional[str] = None):
        if prog is not None:
            task_progress[task_id] = prog
            task_logs[task_id] += f"[PROGRESS] {prog}:{msg}\n"
        else:
            task_logs[task_id] += f"{msg}\n"
        if status:
            task_status[task_id] = status

    hostname = None
    partitions = None
    efi_uuid = "458C-37BB"
    
    global restore_mode
    if restore_mode == "online":
        try:
            task_logs[task_id] += "Fetching node configuration from orchestrator...\n"
            nodes_req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(nodes_req, timeout=5) as response:
                nodes_data = json.loads(response.read().decode())
            for n in nodes_data:
                if n["id"] == req.node_id:
                    hostname = n["hostname"]
                    partitions = n.get("partition_layout")
                    efi_uuid = n.get("efi_uuid") or efi_uuid
                    break
        except Exception as e:
            task_logs[task_id] += f"WARNING: Failed to fetch partition layout from orchestrator: {e}. Falling back to default layout.\n"
    else:
        nodes = get_kiosk_nodes()
        for n in nodes:
            if n["id"] == req.node_id:
                hostname = n["hostname"].split(" (")[0]
                break
        if hostname:
            layout_path = f"/media/usb-data/borg/fleet/{hostname}/partition_layout.json"
            if os.path.exists(layout_path):
                try:
                    with open(layout_path, "r") as f:
                        layout_data = json.load(f)
                        partitions = layout_data.get("partition_layout")
                        efi_uuid = layout_data.get("efi_uuid") or efi_uuid
                except Exception as e:
                    task_logs[task_id] += f"WARNING: Failed to load cached partition layout: {e}\n"

    if not hostname:
        task_status[task_id] = "FAILED"
        task_logs[task_id] += "ERROR: Selected node not found.\n"
        return

    if not partitions:
        task_logs[task_id] += "Using default fallback partition layout.\n"
        partitions = [
            {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": "458C-37BB", "size_bytes": 512 * 1024 * 1024},
            {"name": "boot", "mount": "/boot", "fstype": "ext2", "label": "edgeboot", "uuid": "", "size_bytes": 1024 * 1024 * 1024},
            {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 30 * 1024 * 1024 * 1024},
            {"name": "log", "mount": "/var/log/edge", "fstype": "ext4", "label": "edgelog", "uuid": "", "size_bytes": 5 * 1024 * 1024 * 1024},
            {"name": "storage", "mount": "/var/opt/edge", "fstype": "ext4", "label": "edgestor", "uuid": "", "size_bytes": 0}
        ]

    if restore_mode == "online":
        repo_path = f"ssh://borg@{orchestrator_ip}:{orchestrator_ssh_port}/data/borg/fleet/{hostname}"
    else:
        repo_path = f"/media/usb-data/borg/fleet/{hostname}"

    try:
        format_and_restore(
            target_dev=req.target_dev,
            partitions=partitions,
            efi_uuid=efi_uuid,
            archive_name=req.archive_name,
            repo_path=repo_path,
            keep_network_configs=req.keep_network_configs,
            wipe_mac_bindings=req.wipe_mac_bindings,
            network_iface="eth0",
            total_files=0,
            log_callback=log_callback
        )
    except Exception as e:
        log_callback(f"FATAL EXCEPTION: {str(e)}", status="FAILED")

class ConnectRequest(BaseModel):
    orchestrator_ip: str
    key: str

@app.get("/api/version")
def get_version():
    """Returns the unified application version and kiosk configurations."""
    return {
        "version": VERSION,
        "is_kiosk": True,
        "orchestrator_ip": orchestrator_ip,
        "auth_token": auth_token,
        "language": language,
        "kiosk_uuid": kiosk_uuid
    }

@app.post("/api/kiosk/connect")
def connect_to_orchestrator(req: ConnectRequest):
    # Read local SSH public key
    pub_key_path = SSH_KEY_PATH + ".pub"
    if not os.path.exists(pub_key_path):
        raise HTTPException(status_code=500, detail="Local SSH public key is missing")
    
    try:
        with open(pub_key_path, "r") as f:
            pub_key_data = f.read().strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read local SSH public key: {str(e)}")
    
    # Perform HTTP request to orchestrator handshake API
    handshake_url = f"http://{req.orchestrator_ip}:8000/api/kiosks/handshake"
    payload = {
        "uuid": kiosk_uuid,
        "key": req.key.strip(),
        "ssh_pub_key": pub_key_data
    }
    
    try:
        post_data = json.dumps(payload).encode("utf-8")
        req_obj = urllib.request.Request(
            handshake_url, 
            data=post_data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_obj, timeout=10) as response:
            res_data = json.loads(response.read().decode())
            
        token = res_data.get("auth_token")
        orch_ssh_pub = res_data.get("orchestrator_ssh_pub_key")
        
        # Update current runtime state
        global orchestrator_ip, auth_token, restore_mode
        orchestrator_ip = req.orchestrator_ip
        auth_token = token
        restore_mode = "online"
        
        # Update config.json file
        cfg_data = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    cfg_data = json.load(f)
            except Exception as e:
                logging.error(f"Failed to parse config.json for writing: {e}")
        
        cfg_data["orchestrator_ip"] = req.orchestrator_ip
        cfg_data["auth_token"] = token
        cfg_data["restore_mode"] = "online"
        
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg_data, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to write config.json during connection pairing: {e}")
            
        # Append orchestrator public SSH key to kiosk authorized_keys if returned
        if orch_ssh_pub:
            kiosk_auth_path = "/root/.ssh/authorized_keys"
            try:
                os.makedirs(os.path.dirname(kiosk_auth_path), exist_ok=True)
                content = ""
                if os.path.exists(kiosk_auth_path):
                    with open(kiosk_auth_path, "r") as f:
                        content = f.read()
                if orch_ssh_pub.strip() not in content:
                    with open(kiosk_auth_path, "a") as f:
                        f.write(orch_ssh_pub.strip() + "\n")
            except Exception as e:
                logging.error(f"Failed to write orchestrator public SSH key to kiosk authorized_keys: {e}")
                
        return {"status": "SUCCESS"}
    except urllib.error.HTTPError as he:
        err_body = he.read().decode()
        try:
            err_json = json.loads(err_body)
            detail = err_json.get("detail", "Handshake failed")
        except:
            detail = f"Orchestrator returned error code {he.code}"
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to orchestrator: {str(e)}")


@app.get("/api/scanner/devices")
def scan_devices():
    try:
        out = subprocess.check_output("lsblk -J -b -o NAME,SIZE,MODEL,ROTA,TRAN", shell=True, text=True)
        data = json.loads(out)
        devices = []
        for bd in data.get("blockdevices", []):
            name = bd.get("name") or ""
            if name.startswith("loop") or name.startswith("sr") or name.startswith("vd"):
                continue
                
            model = bd.get("model") or "Unknown Model"
            model_lower = model.lower()
            if any(term in model_lower for term in ["vbox", "qemu", "vmware", "virtual", "xen"]):
                continue

            is_usb = bd.get("tran") == "usb"
            # Exclude the USB drive we booted from if possible.
            try:
                mounts = subprocess.check_output(f"lsblk -J -o MOUNTPOINT /dev/{name}", shell=True, text=True)
                if "live" in mounts.lower():
                    continue
            except:
                pass
            
            devices.append({
                "name": f"/dev/{name}",
                "size": bd.get("size", 0),
                "model": model,
                "rotational": bd.get("rota", False),
                "disk_type": "NVME" if "nvme" in name else "SATA",
                "is_usb": is_usb
            })
        return devices
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/nodes")
def get_kiosk_nodes():
    global restore_mode
    if restore_mode == "online":
        try:
            req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logging.error(f"Failed to fetch nodes from orchestrator: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to contact orchestrator: {str(e)}")
    else:
        # Scan /media/usb-data/borg/fleet for cached directories
        nodes = []
        base_path = "/media/usb-data/borg/fleet"
        if os.path.exists(base_path):
            try:
                for entry in os.listdir(base_path):
                    if os.path.isdir(os.path.join(base_path, entry)) and not entry.startswith("."):
                        node_path = os.path.join(base_path, entry)
                        repo_size = 0
                        try:
                            for root, dirs, files in os.walk(node_path):
                                for file in files:
                                    repo_size += os.path.getsize(os.path.join(root, file))
                        except Exception:
                            repo_size = 0

                        nodes.append({
                            "id": len(nodes) + 1,
                            "hostname": f"{entry} (Local Cache)",
                            "ip_address": "127.0.0.1",
                            "disk_type": "UNKNOWN",
                            "efi_uuid": "458C-37BB",
                            "last_backup": "Available",
                            "repo_size_bytes": repo_size
                        })
            except Exception as e:
                logging.error(f"Error scanning local USB cache: {e}")
                
        if not nodes:
            nodes.append({
                "id": 1,
                "hostname": "Offline Mode (No local cache found)",
                "ip_address": "127.0.0.1",
                "disk_type": "UNKNOWN",
                "efi_uuid": "458C-37BB",
                "last_backup": "None"
            })
        return nodes

@app.get("/api/stats")
def get_mock_stats():
    repo_path = "/media/usb-data/borg/fleet"
    total_original = 0
    total_dedup = 0
    
    if os.path.exists(repo_path):
        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
        try:
            out = subprocess.check_output(["borg", "info", "--json", repo_path], env=env, text=True)
            data = json.loads(out)
            cache_stats = data.get("cache", {}).get("stats", {})
            total_original = cache_stats.get("total_size", 0)
            total_dedup = cache_stats.get("total_csize", 0)
        except Exception:
            pass

    ratio = 1.0
    if total_dedup > 0:
        ratio = round(total_original / total_dedup, 2)

    return {
        "total_nodes": 1,
        "total_original_size_bytes": total_original,
        "total_deduplicated_size_bytes": total_dedup,
        "deduplication_ratio": ratio
    }

@app.get("/api/nodes/history")
def get_all_history():
    return get_local_history(node_id=1)

@app.get("/api/nodes/{node_id}/history")
def get_local_history(node_id: int):
    global restore_mode
    if restore_mode == "online":
        try:
            req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes/{node_id}/history",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logging.error(f"Failed to fetch history from orchestrator: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to contact orchestrator: {str(e)}")
            
    # For offline cache, scan local borg repo of the corresponding node
    nodes = get_kiosk_nodes()
    hostname = None
    for n in nodes:
        if n["id"] == node_id:
            hostname = n["hostname"].split(" (")[0]
            break
            
    if not hostname or "No local cache" in hostname:
        return []
        
    repo_path = f"/media/usb-data/borg/fleet/{hostname}"
    if not os.path.exists(repo_path):
        return []
    
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    try:
        out = subprocess.check_output(["borg", "list", "--json", repo_path], env=env, text=True)
        data = json.loads(out)
        archives = data.get("archives", [])
        snapshots = []
        for i, a in enumerate(archives):
            snapshots.append({
                "id": i,
                "node_id": node_id,
                "archive_name": a["name"],
                "timestamp": a["start"],
                "original_size": a.get("stats", {}).get("original_size", 0),
                "deduplicated_size": a.get("stats", {}).get("deduplicated_size", 0),
                "comment": a.get("comment", ""),
                "status": "SUCCESS"
            })
        return snapshots
    except Exception as e:
        return []

@app.post("/api/restore")
def trigger_restore(req: RestoreRequest, background_tasks: BackgroundTasks):
    import uuid
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_offline_restore, task_id, req)
    return {"task_id": task_id}

@app.post("/api/kiosk/exit")
def exit_kiosk():
    """Kills the web browser running in kiosk mode to exit back to desktop."""
    try:
        # Kill chromium, chromium-browser, firefox, firefox-esr, and x-www-browser
        subprocess.run("pkill -f 'chromium|firefox|x-www-browser'", shell=True)
        return {"status": "SUCCESS", "message": "Kiosk exited."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task_id,
        "status": task_status[task_id],
        "progress": task_progress.get(task_id, 0),
        "logs": task_logs.get(task_id, "")
    }

def run_kiosk_sync(task_id: str, hostname: str):
    task_status[task_id] = "RUNNING"
    task_progress[task_id] = 0
    task_logs[task_id] = f"Starting USB Cache Sync for node {hostname} from http://{orchestrator_ip}:{orchestrator_api_port}\n"

    try:
        # Step A: Cache partition layout from orchestrator
        try:
            nodes_req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(nodes_req, timeout=5) as response:
                nodes_data = json.loads(response.read().decode())
            for n in nodes_data:
                if n["hostname"] == hostname:
                    layout_dir = f"/media/usb-data/borg/fleet/{hostname}"
                    os.makedirs(layout_dir, exist_ok=True)
                    layout_path = os.path.join(layout_dir, "partition_layout.json")
                    with open(layout_path, "w") as lf:
                        json.dump({
                            "partition_layout": n.get("partition_layout"),
                            "efi_uuid": n.get("efi_uuid")
                        }, lf)
                    task_logs[task_id] += "Successfully cached partition layout configuration.\n"
                    break
        except Exception as e:
            task_logs[task_id] += f"WARNING: Failed to fetch and cache partition layout: {e}\n"

        # Step B: Download tar stream
        url = f"http://{orchestrator_ip}:{orchestrator_api_port}/api/iso/repos/{hostname}/download?token={auth_token}"
        task_logs[task_id] += f"Connecting to download stream: {url}\n"
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as response:
            total_size_header = response.headers.get("X-Total-Size")
            total_size = int(total_size_header) if total_size_header else 0
            task_logs[task_id] += f"Total repository size: {total_size} bytes\n"

            target_dir = f"/media/usb-data/borg/fleet/{hostname}"
            os.makedirs(target_dir, exist_ok=True)

            tar_proc = subprocess.Popen(
                ["tar", "-xf", "-", "-C", "/media/usb-data/borg/fleet/"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            bytes_downloaded = 0
            last_reported_pct = -1
            
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                bytes_downloaded += len(chunk)
                tar_proc.stdin.write(chunk)

                if total_size > 0:
                    pct = int((bytes_downloaded / total_size) * 100)
                    if pct != last_reported_pct:
                        task_progress[task_id] = pct
                        last_reported_pct = pct

            tar_proc.stdin.close()
            tar_err = tar_proc.stderr.read().decode()
            tar_proc.wait()

            if tar_proc.returncode != 0:
                raise Exception(f"tar extraction failed (code {tar_proc.returncode}): {tar_err}")

        task_logs[task_id] += f"Repository sync completed successfully! Total bytes: {bytes_downloaded}\n"
        task_status[task_id] = "SUCCESS"
        task_progress[task_id] = 100
    except Exception as e:
        task_logs[task_id] += f"FATAL ERROR during sync: {str(e)}\n"
        task_status[task_id] = "FAILED"

@app.get("/api/kiosk/mode")
def get_kiosk_mode():
    return {"mode": restore_mode}

@app.post("/api/kiosk/mode")
def set_kiosk_mode(req: dict):
    global restore_mode
    mode = req.get("mode")
    if mode not in ("offline", "online"):
        raise HTTPException(status_code=400, detail="Invalid mode")
    restore_mode = mode
    return {"status": "SUCCESS", "mode": restore_mode}

@app.get("/api/kiosk/storage")
def get_kiosk_storage():
    import shutil
    path = "/media/usb-data"
    if not os.path.exists(path):
        path = "/"
    try:
        total, used, free = shutil.disk_usage(path)
        return {
            "total": total,
            "used": used,
            "free": free,
            "path": path,
            "is_mounted": path == "/media/usb-data" and os.path.ismount(path)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/kiosk/sync/{hostname}")
def trigger_kiosk_sync(hostname: str, background_tasks: BackgroundTasks):
    import uuid
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_kiosk_sync, task_id, hostname)
    return {"task_id": task_id}

# Fallback to serve the React frontend built for the offline client
if os.path.exists("frontend_build"):
    app.mount("/", StaticFiles(directory="frontend_build", html=True), name="frontend")
