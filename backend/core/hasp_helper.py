import subprocess
import re
import json
import logging
from typing import List, Dict, Any

from core import ssh
import models

logger = logging.getLogger("hasp_helper")

def parse_sentinel_json_blocks(raw_text: str) -> List[Dict[str, Any]]:
    objs = []
    for block in re.findall(r'\{[^{}]+\}', raw_text):
        try:
            block_clean = re.sub(r'\s+', ' ', block)
            objs.append(json.loads(block_clean))
        except Exception:
            continue
    return objs

def check_hasp_status_on_node(node) -> str:
    """
    Checks the Sentinel HASP license activation status on the given node.
    Returns one of: "active", "no_license", "clone_detected", "disabled", "expired", "unreachable", "inactive"

    Takes anything carrying `hasp_runtime_version`, `ssh_port` and
    `ip_address` rather than a Node specifically. It shells out over SSH, so
    its callers must not be holding a database session — backup_tasks passes a
    detached BackupPlan for exactly that reason.
    """
    if not node.hasp_runtime_version or node.hasp_runtime_version == "None":
        return "inactive"
        
    ssh_cmd = ssh.command(
        node.ip_address, node.ssh_port,
        "curl -s --connect-timeout 3 http://localhost:1947/_int_/tab_dev.html && "
        "echo '---FEATURES_SEPARATOR---' && "
        "curl -s --connect-timeout 3 http://localhost:1947/_int_/tab_feat.html",
    )
    
    try:
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=8)
        if res.returncode != 0 or not res.stdout.strip():
            return "unreachable"
            
        parts = res.stdout.split('---FEATURES_SEPARATOR---')
        dev_section = parts[0] if len(parts) > 0 else ""
        feat_section = parts[1] if len(parts) > 1 else ""
        
        devices = parse_sentinel_json_blocks(dev_section)
        features = parse_sentinel_json_blocks(feat_section)
        
        real_devices = [d for d in devices if d.get("haspid") and d.get("haspid") != "0" and d.get("typ") != "placeholder"]
        real_features = [f for f in features if f.get("fid") is not None]
        
        if not real_devices:
            return "no_license"
            
        is_cloned = any(d.get("cloned") is not None and d.get("cloned") != "0" for d in real_devices)
        is_disabled = any(d.get("key_disabled") is not None and d.get("key_disabled") != "0" for d in real_devices)
        
        if is_cloned:
            return "clone_detected"
        elif is_disabled:
            return "disabled"
        else:
            active_features = [f for f in real_features if f.get("fid") != "0"]
            if not active_features:
                return "no_license"
            elif all(f.get("unusable") is not None and f.get("unusable") != "0" for f in active_features):
                return "expired"
            else:
                return "active"
    except Exception as e:
        logger.error(f"Error checking HASP status on node {node.hostname}: {e}")
        return "unreachable"
