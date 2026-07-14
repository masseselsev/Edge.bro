import os
import time
import subprocess
from typing import Optional
from fastapi import APIRouter
from routers.network import VpnStatusResponse, VpnConfigRequest, ActionResponse

router = APIRouter()

@router.get("/vpn/status", response_model=Optional[VpnStatusResponse])
def get_vpn_status():
    conf_path = "/etc/wireguard/wg0.conf"
    usb_path = "/media/usb-data/wg0.conf"
    
    # Try to find a config file
    active_path = None
    if os.path.exists(usb_path):
        active_path = usb_path
    elif os.path.exists(conf_path):
        active_path = conf_path
        
    if not active_path:
        return None
        
    # Parse local tunnel IP
    local_ip = None
    try:
        with open(active_path, "r") as f:
            for line in f:
                if "=" in line:
                    key, val = line.split("=", 1)
                    if key.strip().lower() == "address":
                        # Address can be comma-separated, take first
                        addr = val.split(",")[0].strip()
                        # Strip /24 prefix
                        if "/" in addr:
                            addr = addr.split("/")[0]
                        local_ip = addr
                        break
    except Exception:
        pass
        
    # Check if interface is connected/up
    try:
        # wg show wg0 dump output format:
        # line 1: private-key public-key listen-port fwmark
        # line 2+: public-key preshared-key endpoint allowed-ips latest-handshake transfer-rx transfer-tx persistent-keepalive
        res = subprocess.run(["wg", "show", "wg0", "dump"], capture_output=True, text=True)
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            endpoint = None
            allowed_ips = None
            received = 0
            sent = 0
            handshake = 0
            
            if len(lines) >= 2:
                peer_parts = lines[1].split("\t")
                if len(peer_parts) >= 8:
                    endpoint = peer_parts[2] if peer_parts[2] != "(none)" else None
                    allowed_ips = peer_parts[3] if peer_parts[3] != "(none)" else None
                    try:
                        handshake_time = int(peer_parts[4])
                        if handshake_time > 0:
                            handshake = int(time.time()) - handshake_time
                    except Exception:
                        pass
                    try:
                        received = int(peer_parts[5])
                        sent = int(peer_parts[6])
                    except Exception:
                        pass
                        
            return VpnStatusResponse(
                connected=True,
                ip=local_ip,
                endpoint=endpoint,
                allowed_ips=allowed_ips,
                received_bytes=received,
                sent_bytes=sent,
                last_handshake=handshake
            )
    except Exception:
        pass
        
    return VpnStatusResponse(connected=False, ip=local_ip)


@router.post("/vpn", response_model=ActionResponse)
def save_vpn_config(req: VpnConfigRequest):
    if "[interface]" not in req.config_text.lower():
        return ActionResponse(status="FAILED", error="Invalid WireGuard configuration: missing [Interface]")
        
    usb_dir = "/media/usb-data"
    usb_path = f"{usb_dir}/wg0.conf"
    conf_dir = "/etc/wireguard"
    conf_path = f"{conf_dir}/wg0.conf"
    
    try:
        # Ensure etc directory exists
        os.makedirs(conf_dir, exist_ok=True)
        with open(conf_path, "w") as f:
            f.write(req.config_text)
            
        # Copy to USB if plugged in
        if os.path.exists(usb_dir):
            os.makedirs(usb_dir, exist_ok=True)
            with open(usb_path, "w") as f:
                f.write(req.config_text)
                
        # Toggle up connection (down first to clean up)
        subprocess.run(["wg-quick", "down", "wg0"], capture_output=True)
        res = subprocess.run(["wg-quick", "up", "wg0"], capture_output=True, text=True)
        if res.returncode != 0:
            return ActionResponse(status="FAILED", error=f"Failed to start tunnel: {res.stderr}")
            
        return ActionResponse(status="SUCCESS")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


@router.post("/vpn/connect", response_model=ActionResponse)
def connect_vpn():
    usb_path = "/media/usb-data/wg0.conf"
    conf_dir = "/etc/wireguard"
    conf_path = f"{conf_dir}/wg0.conf"
    
    if not os.path.exists(conf_path):
        if os.path.exists(usb_path):
            try:
                os.makedirs(conf_dir, exist_ok=True)
                import shutil
                shutil.copy2(usb_path, conf_path)
            except Exception as e:
                return ActionResponse(status="FAILED", error=f"Failed to copy profile from USB: {str(e)}")
        else:
            return ActionResponse(status="FAILED", error="No WireGuard configuration file found")
            
    try:
        subprocess.run(["wg-quick", "down", "wg0"], capture_output=True)
        res = subprocess.run(["wg-quick", "up", "wg0"], capture_output=True, text=True)
        if res.returncode != 0:
            return ActionResponse(status="FAILED", error=res.stderr)
        return ActionResponse(status="SUCCESS")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


@router.post("/vpn/disconnect", response_model=ActionResponse)
def disconnect_vpn():
    try:
        res = subprocess.run(["wg-quick", "down", "wg0"], capture_output=True, text=True)
        if res.returncode != 0:
            pass
        return ActionResponse(status="SUCCESS")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


@router.delete("/vpn", response_model=ActionResponse)
def delete_vpn():
    usb_path = "/media/usb-data/wg0.conf"
    conf_path = "/etc/wireguard/wg0.conf"
    
    try:
        subprocess.run(["wg-quick", "down", "wg0"], capture_output=True)
        
        if os.path.exists(conf_path):
            os.remove(conf_path)
        if os.path.exists(usb_path):
            os.remove(usb_path)
            
        return ActionResponse(status="SUCCESS")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))
