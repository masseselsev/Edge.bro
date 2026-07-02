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
    VERSION = "v0.9.2beta"

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
kiosk_id = ""
kiosk_status = "PENDING"
restore_mode = "offline"
local_storage_path = "/media/usb-data"
available_server_ips = []
autocheck_in_thread_started = False

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = json.load(f)
            orchestrator_ip = cfg.get("orchestrator_ip", "127.0.0.1")
            orchestrator_api_port = cfg.get("orchestrator_api_port", 8000)
            orchestrator_ssh_port = cfg.get("orchestrator_ssh_port", 12345)
            auth_token = cfg.get("auth_token", "")
            language = cfg.get("language", "en")
            kiosk_id = cfg.get("kiosk_id", "")
            restore_mode = cfg.get("restore_mode", "online" if auth_token else "offline")
            local_storage_path = cfg.get("local_storage_path", "/media/usb-data")
            available_server_ips = cfg.get("available_server_ips", [])
            borg_passphrase = cfg.get("borg_passphrase", "")
            if borg_passphrase:
                os.environ["BORG_PASSPHRASE"] = borg_passphrase
    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")


def generate_kiosk_id() -> str:
    """Generates a memorable kiosk identifier in XX1234 pattern (2 letters + 4 digits)."""
    import random
    import string
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = "".join(random.choices(string.digits, k=4))
    return f"{letters}{digits}"

# Generate persistent Kiosk ID if not present
if not kiosk_id:
    kiosk_id = generate_kiosk_id()
    try:
        cfg_data = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                cfg_data = json.load(f)
        cfg_data["kiosk_id"] = kiosk_id
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg_data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save kiosk_id to config.json: {e}")

# SSH keypair path (generated lazily to avoid race with offline-ssh-install.service)
SSH_KEY_PATH = os.path.join(os.path.dirname(__file__), "id_ed25519")

def ensure_ssh_keypair() -> None:
    """Ensures the SSH keypair (private + public) exists at SSH_KEY_PATH.
    
    Handles three scenarios on Live-CD boot:
    1. Both id_ed25519 and id_ed25519.pub exist (baked by ISO generator) → no-op.
    2. id_ed25519 exists but .pub is missing → derive .pub from private key.
    3. Neither exists → generate a fresh keypair (requires openssh-client).
    
    Called lazily so that openssh-client has time to be installed by
    offline-ssh-install.service before first use."""
    pub_key_path = SSH_KEY_PATH + ".pub"
    
    if os.path.exists(SSH_KEY_PATH):
        # Private key exists (likely baked into ISO) — ensure .pub also exists
        if not os.path.exists(pub_key_path):
            try:
                result = subprocess.run(
                    ["ssh-keygen", "-y", "-f", SSH_KEY_PATH],
                    check=True, capture_output=True, text=True
                )
                with open(pub_key_path, "w") as f:
                    f.write(result.stdout.strip() + "\n")
            except FileNotFoundError:
                raise HTTPException(
                    status_code=503,
                    detail="ssh-keygen is not yet available. SSH packages may still be installing — please try again in a few seconds."
                )
            except subprocess.CalledProcessError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to derive public key: {e.stderr if e.stderr else str(e)}"
                )
        return
    
    # No private key at all — generate a fresh keypair
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", SSH_KEY_PATH],
            check=True, capture_output=True
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="ssh-keygen is not yet available. SSH packages may still be installing — please try again in a few seconds."
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate SSH keypair: {e.stderr.decode() if e.stderr else str(e)}"
        )

# Try to register the shared network configurations router if available
try:
    from routers.network import router as network_router
    app.include_router(network_router, prefix="/api")
except ImportError:
    pass

# Register Kiosk Watchdog router
try:
    from routers.watchdog import router as watchdog_router
    app.include_router(watchdog_router, prefix="/api")
except ImportError:
    pass

# Local state to track task progress
task_logs: Dict[str, str] = {}
task_status: Dict[str, str] = {}
task_progress: Dict[str, int] = {}
task_download_speed: Dict[str, str] = {}
task_eta: Dict[str, str] = {}

class RestoreRequest(BaseModel):
    node_id: int
    archive_name: str
    target_dev: str
    override_mismatch: bool = False
    keep_network_configs: bool = True
    wipe_mac_bindings: bool = False

def run_offline_restore(task_id: str, req: RestoreRequest):
    global restore_mode
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

    # Resolve hostname
    nodes = get_kiosk_nodes()
    for n in nodes:
        if n["id"] == req.node_id:
            hostname = n["hostname"].split(" (")[0]
            break

    if not hostname:
        task_status[task_id] = "FAILED"
        task_logs[task_id] += "ERROR: Selected node not found.\n"
        return

    # Check if archive exists in local USB cache
    local_repo = os.path.join(local_storage_path, "borg", "fleet", hostname)
    archive_exists_locally = False
    if os.path.exists(local_repo):
        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "verysecureborgpassphrase")
        try:
            out = subprocess.check_output(["borg", "list", "--json", local_repo], env=env, text=True)
            data = json.loads(out)
            for a in data.get("archives", []):
                if a["name"] == req.archive_name:
                    archive_exists_locally = True
                    break
        except Exception:
            pass

    # Resolve repository path and load layout
    if archive_exists_locally:
        log_callback(f"Archive {req.archive_name} found in local USB cache. Using offline restore.")
        repo_path = local_repo
        
        # Load local partition layout
        layout_path = os.path.join(local_repo, "partition_layout.json")
        if os.path.exists(layout_path):
            try:
                with open(layout_path, "r") as f:
                    layout_data = json.load(f)
                    partitions = layout_data.get("partition_layout")
                    efi_uuid = layout_data.get("efi_uuid") or efi_uuid
                    log_callback("Loaded partition layout from local cache.")
            except Exception as e:
                log_callback(f"WARNING: Failed to load cached partition layout: {e}")
    else:
        if restore_mode == "online":
            log_callback("Using online restore from orchestrator.")
            repo_path = f"ssh://borg@{orchestrator_ip}:{orchestrator_ssh_port}/data/borg/fleet/{hostname}"
        else:
            log_callback("Online mode is unavailable. Attempting offline restore from local cache.")
            repo_path = local_repo

    # Fallback to fetching layout from orchestrator if not yet loaded and online
    if not partitions:
        if restore_mode == "online":
            try:
                log_callback("Fetching node configuration from orchestrator...")
                nodes_req = urllib.request.Request(
                    f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes",
                    headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                )
                with urllib.request.urlopen(nodes_req, timeout=5) as response:
                    nodes_data = json.loads(response.read().decode())
                for n in nodes_data:
                    if n["id"] == req.node_id:
                        partitions = n.get("partition_layout")
                        efi_uuid = n.get("efi_uuid") or efi_uuid
                        break
            except Exception as e:
                log_callback(f"WARNING: Failed to fetch partition layout from orchestrator: {e}")

    # Fallback to cached layout if still not loaded
    if not partitions:
        layout_path = os.path.join(local_repo, "partition_layout.json")
        if os.path.exists(layout_path):
            try:
                with open(layout_path, "r") as f:
                    layout_data = json.load(f)
                    partitions = layout_data.get("partition_layout")
                    efi_uuid = layout_data.get("efi_uuid") or efi_uuid
                    log_callback("Loaded partition layout from local cache fallback.")
            except Exception as e:
                log_callback(f"WARNING: Failed to load cached partition layout: {e}")

    # Ultimate fallback layout
    if not partitions:
        log_callback("Using default fallback partition layout.")
        partitions = [
            {"name": "ESP", "mount": "/boot/efi", "fstype": "vfat", "label": "EFI", "uuid": "458C-37BB", "size_bytes": 512 * 1024 * 1024},
            {"name": "boot", "mount": "/boot", "fstype": "ext2", "label": "edgeboot", "uuid": "", "size_bytes": 1024 * 1024 * 1024},
            {"name": "root", "mount": "/", "fstype": "ext4", "label": "edgeroot", "uuid": "", "size_bytes": 30 * 1024 * 1024 * 1024},
            {"name": "log", "mount": "/var/log/edge", "fstype": "ext4", "label": "edgelog", "uuid": "", "size_bytes": 5 * 1024 * 1024 * 1024},
            {"name": "storage", "mount": "/var/opt/edge", "fstype": "ext4", "label": "edgestor", "uuid": "", "size_bytes": 0}
        ]

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
        "available_server_ips": available_server_ips,
        "auth_token": auth_token,
        "language": language,
        "kiosk_id": kiosk_id,
        "kiosk_status": kiosk_status
    }

class ClientEnrollRequest(BaseModel):
    orchestrator_ip: str
    name: str
    contact: str
    comment: str

@app.post("/api/kiosk/enroll")
def enroll_client_kiosk(req: ClientEnrollRequest):
    # Ensure SSH keypair exists (lazy generation)
    ensure_ssh_keypair()
    
    pub_key_path = SSH_KEY_PATH + ".pub"
    if not os.path.exists(pub_key_path):
        raise HTTPException(status_code=500, detail="Local SSH public key is missing")
    
    try:
        with open(pub_key_path, "r") as f:
            pub_key_data = f.read().strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read local SSH public key: {str(e)}")
        
    url = f"http://{req.orchestrator_ip}:8000/api/kiosks/enroll"
    payload = {
        "kiosk_id": kiosk_id,
        "name": req.name.strip(),
        "contact": req.contact.strip(),
        "comment": req.comment.strip(),
        "ssh_pub_key": pub_key_data
    }
    
    try:
        post_data = json.dumps(payload).encode("utf-8")
        req_obj = urllib.request.Request(
            url, 
            data=post_data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_obj, timeout=10) as response:
            res_data = json.loads(response.read().decode())
            
        token = res_data.get("auth_token")
        if token:
            # Update current runtime state
            global orchestrator_ip, auth_token, restore_mode, kiosk_status, autocheck_in_thread_started
            orchestrator_ip = req.orchestrator_ip
            auth_token = token
            restore_mode = "offline"
            kiosk_status = "PENDING"
            
            # Start auto check-in thread if not already running
            if not autocheck_in_thread_started:
                import threading
                threading.Thread(target=auto_register_with_orchestrator, daemon=True).start()
                autocheck_in_thread_started = True
            
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
            cfg_data["restore_mode"] = "offline"
            
            try:
                with open(CONFIG_PATH, "w") as f:
                    json.dump(cfg_data, f, indent=4)
            except Exception as e:
                logging.error(f"Failed to write config.json during connection enrollment: {e}")
                
        return res_data
    except urllib.error.HTTPError as he:
        err_body = he.read().decode()
        try:
            err_json = json.loads(err_body)
            detail = err_json.get("detail", "Enrollment request failed")
        except:
            detail = f"Server returned error code {he.code}"
        raise HTTPException(status_code=400, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit enrollment to server: {str(e)}")


@app.post("/api/kiosk/connect")
def connect_to_orchestrator(req: ConnectRequest):
    # Ensure SSH keypair exists (lazy generation)
    ensure_ssh_keypair()
    
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
        "kiosk_id": kiosk_id,
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
        global orchestrator_ip, auth_token, restore_mode, kiosk_status, autocheck_in_thread_started
        orchestrator_ip = req.orchestrator_ip
        auth_token = token
        restore_mode = "online"
        kiosk_status = "APPROVED"
        
        # Start auto check-in thread if not already running
        if not autocheck_in_thread_started:
            import threading
            threading.Thread(target=auto_register_with_orchestrator, daemon=True).start()
            autocheck_in_thread_started = True
        
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
        # Scan local cache for cached directories
        nodes = []
        base_path = os.path.join(local_storage_path, "borg", "fleet")
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
    repo_path = os.path.join(local_storage_path, "borg", "fleet")
    total_original = 0
    total_dedup = 0
    
    if os.path.exists(repo_path):
        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "verysecureborgpassphrase")
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
    global restore_mode
    if restore_mode == "online":
        try:
            req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes/history",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logging.error(f"Failed to fetch all history from orchestrator: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to contact orchestrator: {str(e)}")
            
    # Offline mode: scan and aggregate history for all nodes
    nodes = get_kiosk_nodes()
    all_snapshots = []
    for n in nodes:
        node_id = n["id"]
        # Skip dummy Offline/No cache node
        if "No local cache" in n["hostname"]:
            continue
        node_snapshots = get_local_history(node_id=node_id)
        for s in node_snapshots:
            s["id"] = len(all_snapshots) + 1
            all_snapshots.append(s)
    return all_snapshots

@app.get("/api/kiosk/local-history")
def get_kiosk_local_history():
    nodes = get_kiosk_nodes()
    all_snapshots = []
    for n in nodes:
        node_id = n["id"]
        hostname = n["hostname"].split(" (")[0]
        if "No local cache" in hostname:
            continue
        repo_path = os.path.join(local_storage_path, "borg", "fleet", hostname)
        if not os.path.exists(repo_path):
            continue

        # Load cached metadata sizes if present
        metadata_path = os.path.join(repo_path, "archive_metadata.json")
        metadata_by_name = {}
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as mf:
                    m_data = json.load(mf)
                    for item in m_data:
                        metadata_by_name[item["archive_name"]] = item
            except Exception:
                pass

        env = os.environ.copy()
        env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "verysecureborgpassphrase")
        try:
            out = subprocess.check_output(["borg", "list", "--json", repo_path], env=env, text=True)
            data = json.loads(out)
            archives = data.get("archives", [])
            for i, a in enumerate(archives):
                a_name = a["name"]
                original_size = 0
                deduplicated_size = 0
                comment = a.get("comment", "")
                if a_name in metadata_by_name:
                    m_item = metadata_by_name[a_name]
                    original_size = m_item.get("original_size", 0)
                    deduplicated_size = m_item.get("deduplicated_size", 0)
                    comment = m_item.get("comment") or comment

                all_snapshots.append({
                    "id": len(all_snapshots) + 1,
                    "node_id": node_id,
                    "archive_name": a_name,
                    "timestamp": a["start"],
                    "original_size": original_size,
                    "deduplicated_size": deduplicated_size,
                    "comment": comment,
                    "status": "SUCCESS"
                })
        except Exception:
            pass
    return all_snapshots

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
        
    repo_path = os.path.join(local_storage_path, "borg", "fleet", hostname)
    if not os.path.exists(repo_path):
        return []

    # Load cached metadata sizes if present
    metadata_path = os.path.join(repo_path, "archive_metadata.json")
    metadata_by_name = {}
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as mf:
                m_data = json.load(mf)
                for item in m_data:
                    metadata_by_name[item["archive_name"]] = item
        except Exception:
            pass
    
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "verysecureborgpassphrase")
    try:
        out = subprocess.check_output(["borg", "list", "--json", repo_path], env=env, text=True)
        data = json.loads(out)
        archives = data.get("archives", [])
        snapshots = []
        for i, a in enumerate(archives):
            a_name = a["name"]
            original_size = 0
            deduplicated_size = 0
            comment = a.get("comment", "")
            if a_name in metadata_by_name:
                m_item = metadata_by_name[a_name]
                original_size = m_item.get("original_size", 0)
                deduplicated_size = m_item.get("deduplicated_size", 0)
                comment = m_item.get("comment") or comment

            snapshots.append({
                "id": i,
                "node_id": node_id,
                "archive_name": a_name,
                "timestamp": a["start"],
                "original_size": original_size,
                "deduplicated_size": deduplicated_size,
                "comment": comment,
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

@app.get("/api/tasks")
def get_kiosk_tasks():
    global restore_mode
    if restore_mode == "online":
        try:
            req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/tasks",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logging.error(f"Failed to fetch tasks from orchestrator: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to contact orchestrator: {str(e)}")
    else:
        # Construct list from local task logs
        tasks_list = []
        for tid in task_status:
            task_type = "RESTORE" if "restore" in task_logs.get(tid, "").lower() else "SYNC"
            tasks_list.append({
                "id": tid,
                "task_type": task_type,
                "status": task_status[tid],
                "created_at": "2026-06-17T00:00:00Z",
                "updated_at": "2026-06-17T00:00:00Z",
            })
        return tasks_list

@app.get("/api/tasks/debug-logs")
def get_kiosk_debug_logs():
    logs = []
    try:
        # Get logs of offline-backend systemd unit
        out = subprocess.check_output(
            ["journalctl", "-u", "offline-backend", "-n", "500", "--no-hostname", "--output=short-iso"],
            text=True
        )
        for i, line in enumerate(out.splitlines()):
            line = line.strip()
            if not line:
                continue
            # Split by whitespace
            parts = line.split(maxsplit=2)
            # Short-iso format: '2026-06-17T13:08:29+0000 python3[1404]: MESSAGE'
            
            created_at = None
            if len(parts) >= 1:
                # Validate if it looks like a ISO timestamp
                if 'T' in parts[0] and ('+' in parts[0] or '-' in parts[0] or 'Z' in parts[0]):
                    created_at = parts[0]
            
            if not created_at:
                import datetime
                created_at = datetime.datetime.utcnow().isoformat() + "Z"
                message = line
            else:
                if len(parts) >= 3:
                    message = parts[2]
                elif len(parts) == 2:
                    message = parts[1]
                else:
                    message = ""
                    
            # Determine log level from message content
            msg_upper = message.upper()
            if "ERROR" in msg_upper or "EXCEPTION" in msg_upper or "CRITICAL" in msg_upper:
                level = "ERROR"
            elif "WARNING" in msg_upper or "WARN" in msg_upper:
                level = "WARNING"
            elif "DEBUG" in msg_upper:
                level = "DEBUG"
            else:
                level = "INFO"
                
            logs.append({
                "id": i + 1,
                "level": level,
                "message": message,
                "created_at": created_at
            })
    except Exception as e:
        logging.error(f"Failed to read local journal logs: {e}")
        import datetime
        logs = [{
            "id": 1,
            "level": "ERROR",
            "message": f"Failed to retrieve local system logs: {str(e)}",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z"
        }]
    
    logs.reverse()
    return logs

@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    if task_id in task_status:
        return {
            "task_id": task_id,
            "status": task_status[task_id],
            "progress": task_progress.get(task_id, 0),
            "logs": task_logs.get(task_id, ""),
            "log_output": task_logs.get(task_id, ""),
            "download_speed": task_download_speed.get(task_id, ""),
            "eta": task_eta.get(task_id, "")
        }
    global restore_mode
    if restore_mode == "online":
        try:
            req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logging.error(f"Failed to fetch task from orchestrator: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to contact orchestrator: {str(e)}")
    raise HTTPException(status_code=404, detail="Task not found")

def run_kiosk_sync(task_id: str, hostname: str, archive: Optional[str] = None):
    import urllib.request, urllib.parse
    task_status[task_id] = "RUNNING"
    task_progress[task_id] = 0
    sync_desc = f"USB Cache Sync for node {hostname}"
    if archive:
        sync_desc += f" (Archive: {archive})"
    task_logs[task_id] = f"Starting {sync_desc} from http://{orchestrator_ip}:{orchestrator_api_port}\n"

    try:
        # Step A: Cache partition layout and archive metadata from orchestrator
        try:
            nodes_req = urllib.request.Request(
                f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes",
                headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            )
            with urllib.request.urlopen(nodes_req, timeout=5) as response:
                nodes_data = json.loads(response.read().decode())
            for n in nodes_data:
                if n["hostname"] == hostname:
                    layout_dir = os.path.join(local_storage_path, "borg", "fleet", hostname)
                    os.makedirs(layout_dir, exist_ok=True)
                    
                    # Cache partition layout
                    layout_path = os.path.join(layout_dir, "partition_layout.json")
                    with open(layout_path, "w") as lf:
                        json.dump({
                            "partition_layout": n.get("partition_layout"),
                            "efi_uuid": n.get("efi_uuid")
                        }, lf)
                    task_logs[task_id] += "Successfully cached partition layout configuration.\n"
                    
                    # Fetch and cache archive history metadata (sizes)
                    try:
                        history_req = urllib.request.Request(
                            f"http://{orchestrator_ip}:{orchestrator_api_port}/api/nodes/{n['id']}/history",
                            headers={"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                        )
                        with urllib.request.urlopen(history_req, timeout=5) as h_response:
                            history_data = json.loads(h_response.read().decode())
                        
                        metadata_path = os.path.join(layout_dir, "archive_metadata.json")
                        with open(metadata_path, "w") as mf:
                            json.dump(history_data, mf)
                        task_logs[task_id] += "Successfully cached archive metadata size configurations.\n"
                    except Exception as he:
                        task_logs[task_id] += f"WARNING: Failed to fetch and cache archive history: {he}\n"
                    break
        except Exception as e:
            task_logs[task_id] += f"WARNING: Failed to fetch and cache partition layout: {e}\n"

        # Step B: Download tar stream
        url = f"http://{orchestrator_ip}:{orchestrator_api_port}/api/iso/repos/{hostname}/download?token={auth_token}"
        if archive:
            url += f"&archives={urllib.parse.quote(archive)}"
        task_logs[task_id] += f"Connecting to download stream: {url}\n"
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=300) as response:
            total_size_header = response.headers.get("X-Total-Size")
            total_size = int(total_size_header) if total_size_header else 0
            task_logs[task_id] += f"Total repository size: {total_size} bytes\n"

            target_dir = os.path.join(local_storage_path, "borg", "fleet", hostname)
            if os.path.exists(target_dir):
                import shutil
                task_logs[task_id] += f"Cleaning up existing repository cache for {hostname}...\n"
                try:
                    shutil.rmtree(target_dir)
                except Exception as ex:
                    task_logs[task_id] += f"WARNING: Failed to clean directory {target_dir}: {ex}\n"

            os.makedirs(target_dir, exist_ok=True)

            fleet_dir = os.path.join(local_storage_path, "borg", "fleet")
            os.makedirs(fleet_dir, exist_ok=True)

            tar_proc = subprocess.Popen(
                ["tar", "-xf", "-", "-C", fleet_dir],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            bytes_downloaded = 0
            last_reported_pct = -1
            
            import time
            start_time = time.time()
            last_time = start_time
            last_bytes = 0
            
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                bytes_downloaded += len(chunk)
                tar_proc.stdin.write(chunk)

                current_time = time.time()
                elapsed_since_last = current_time - last_time
                if elapsed_since_last >= 1.0 or bytes_downloaded == total_size:
                    speed = (bytes_downloaded - last_bytes) / (elapsed_since_last or 0.001)  # bytes / sec
                    if speed >= 1024 * 1024:
                        speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
                    elif speed >= 1024:
                        speed_str = f"{speed / 1024:.2f} KB/s"
                    else:
                        speed_str = f"{speed:.2f} B/s"
                    task_download_speed[task_id] = speed_str
                    
                    if total_size > 0:
                        remaining_bytes = total_size - bytes_downloaded
                        if speed > 0:
                            eta_sec = int(remaining_bytes / speed)
                            if eta_sec >= 60:
                                eta_str = f"{eta_sec // 60}m {eta_sec % 60}s"
                            else:
                                eta_str = f"{eta_sec}s"
                        else:
                            eta_str = "--"
                        task_eta[task_id] = eta_str
                    else:
                        task_eta[task_id] = "--"
                        
                    last_time = current_time
                    last_bytes = bytes_downloaded

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

def find_potential_storage_paths() -> List[str]:
    paths = [local_storage_path, "/media/usb-data"]
    
    # Scan /media for directories
    if os.path.exists("/media"):
        try:
            for entry in os.listdir("/media"):
                full_path = os.path.join("/media", entry)
                if os.path.isdir(full_path) and not entry.startswith("."):
                    paths.append(full_path)
        except Exception:
            pass

    # Scan /mnt for directories
    if os.path.exists("/mnt"):
        try:
            for entry in os.listdir("/mnt"):
                full_path = os.path.join("/mnt", entry)
                if os.path.isdir(full_path) and not entry.startswith("."):
                    paths.append(full_path)
        except Exception:
            pass

    # Add fallback root path
    paths.append("/")
    
    # Deduplicate and keep existing paths
    unique_paths = []
    for p in paths:
        p_abs = os.path.abspath(p)
        if p_abs not in unique_paths and os.path.exists(p_abs):
            unique_paths.append(p_abs)
            
    return unique_paths

@app.get("/api/kiosk/storage")
def get_kiosk_storage():
    import shutil
    path = local_storage_path
    if not os.path.exists(path):
        fallbacks = ["/media/usb-data", "/"]
        for f in fallbacks:
            if os.path.exists(f):
                path = f
                break
    try:
        total, used, free = shutil.disk_usage(path)
        return {
            "total": total,
            "used": used,
            "free": free,
            "path": path,
            "is_mounted": path != "/",
            "potential_paths": find_potential_storage_paths()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class StoragePathRequest(BaseModel):
    path: str

@app.post("/api/kiosk/storage/path")
def set_kiosk_storage_path(req: StoragePathRequest):
    global local_storage_path
    path = req.path.strip()
    
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Path must be an absolute path starting with '/'")
        
    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to create directory '{path}': {str(e)}")
            
    local_storage_path = path
    
    cfg_data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to parse config.json for writing: {e}")
            
    cfg_data["local_storage_path"] = local_storage_path
    
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg_data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to write config.json during storage path update: {e}")
        
    return get_kiosk_storage()

@app.post("/api/kiosk/sync/{hostname}")
def trigger_kiosk_sync(hostname: str, background_tasks: BackgroundTasks, archive: Optional[str] = None):
    import uuid
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_kiosk_sync, task_id, hostname, archive)
    return {"task_id": task_id}


def auto_register_with_orchestrator():
    global kiosk_status, restore_mode
    import time
    import urllib.error
    ensure_ssh_keypair()
    pub_key_path = SSH_KEY_PATH + ".pub"
    if not os.path.exists(pub_key_path):
        logging.error("SSH public key missing during auto-registration")
        return
        
    try:
        with open(pub_key_path, "r") as f:
            pub_key_data = f.read().strip()
    except Exception as e:
        logging.error(f"Failed to read SSH public key during auto-registration: {e}")
        return

    while True:
        try:
            url = f"http://{orchestrator_ip}:{orchestrator_api_port}/api/kiosks/auto-handshake"
            payload = {
                "kiosk_id": kiosk_id,
                "ssh_pub_key": pub_key_data
            }
            post_data = json.dumps(payload).encode("utf-8")
            req_obj = urllib.request.Request(
                url, 
                data=post_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {auth_token}"
                }
            )
            with urllib.request.urlopen(req_obj, timeout=10) as response:
                res_data = json.loads(response.read().decode())
                
            status_returned = res_data.get("status")
            if status_returned == "SUCCESS" or status_returned == "APPROVED":
                kiosk_status = "APPROVED"
                restore_mode = "online"
            elif status_returned == "DISABLED":
                kiosk_status = "DISABLED"
                restore_mode = "offline"
            elif status_returned == "PENDING":
                kiosk_status = "PENDING"
                restore_mode = "offline"
            elif status_returned == "REVOKED":
                kiosk_status = "REVOKED"
                restore_mode = "offline"
        except urllib.error.HTTPError as he:
            if he.code in [401, 403]:
                logging.warning(f"Auto check-in unauthorized/forbidden ({he.code}): transitioning kiosk to DISABLED")
                kiosk_status = "DISABLED"
                restore_mode = "offline"
            else:
                logging.warning(f"Auto check-in failed with HTTP error {he.code}: {he}")
        except Exception as e:
            logging.warning(f"Auto check-in failed (retrying in 10s): {e}")
            
        time.sleep(10)


@app.on_event("startup")
def startup_event():
    global autocheck_in_thread_started
    if auth_token and orchestrator_ip:
        import threading
        threading.Thread(target=auto_register_with_orchestrator, daemon=True).start()
        autocheck_in_thread_started = True


@app.post("/api/kiosk/request-activation")
def request_activation():
    if not auth_token or not orchestrator_ip:
        raise HTTPException(status_code=400, detail="Orchestrator not configured or not paired")
        
    url = f"http://{orchestrator_ip}:{orchestrator_api_port}/api/kiosks/request-activation"
    payload = {"token": auth_token}
    try:
        post_data = json.dumps(payload).encode("utf-8")
        req_obj = urllib.request.Request(
            url, 
            data=post_data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_obj, timeout=10) as response:
            res_data = json.loads(response.read().decode())
            
        global kiosk_status
        kiosk_status = "PENDING"  # Update local state immediately
        return res_data
    except Exception as e:
        logging.error(f"Failed to submit activation request to orchestrator: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to request activation: {str(e)}")


# Fallback to serve the React frontend built for the offline client
if os.path.exists("frontend_build"):
    app.mount("/", StaticFiles(directory="frontend_build", html=True), name="frontend")
