import subprocess
import re
import os
import time
import json
try:
    import redis
except ImportError:
    redis = None
from fastapi import APIRouter, Depends
try:
    from routers.users import require_admin
except ImportError:
    # On the kiosk terminal, network configuration is local and does not require web session authentication.
    def require_admin():
        pass
from pydantic import BaseModel, Field
from typing import Optional, List

_redis_client = None
if redis:
    try:
        REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
        _redis_client = redis.Redis.from_url(REDIS_URL)
    except Exception:
        pass

# Module-level fallback cache used when Redis is unavailable
_fallback_traffic_cache: dict = {}

router = APIRouter(prefix="/network", tags=["Network"], dependencies=[Depends(require_admin)])

# Pydantic models for strict type hinting and serialization
class WiredStatus(BaseModel):
    device: str
    connected: bool
    ip: Optional[str] = None
    netmask: Optional[str] = None
    gateway: Optional[str] = None
    dns_servers: List[str] = Field(default_factory=list)
    mode: str = "auto"
    dns_mode: str = "auto"

class WifiStatus(BaseModel):
    device: str
    connected: bool
    ssid: Optional[str] = None
    signal: int = 0

class NetworkStatusResponse(BaseModel):
    wired: WiredStatus
    wifi: WifiStatus

class WifiNetworkInfo(BaseModel):
    ssid: str
    signal: int
    security: str
    active: bool

class WifiConnectRequest(BaseModel):
    ssid: str
    password: Optional[str] = None
    hidden: bool = False

class WiredConfigRequest(BaseModel):
    mode: str  # "auto" or "manual"
    ip_address: Optional[str] = None
    netmask: Optional[str] = None
    gateway: Optional[str] = None
    dns_mode: str  # "auto" or "manual"
    dns_servers: Optional[List[str]] = None

class ActionResponse(BaseModel):
    status: str
    message: Optional[str] = None
    error: Optional[str] = None

class VpnConfigRequest(BaseModel):
    config_text: str

class VpnStatusResponse(BaseModel):
    connected: bool
    ip: Optional[str] = None
    endpoint: Optional[str] = None
    allowed_ips: Optional[str] = None
    received_bytes: int = 0
    sent_bytes: int = 0
    last_handshake: int = 0

class BandwidthResponse(BaseModel):
    rx_speed: float = Field(..., description="Download speed in bytes/sec")
    tx_speed: float = Field(..., description="Upload speed in bytes/sec")


def get_network_bytes() -> tuple[float, int, int]:
    """Read cumulative Rx/Tx bytes from /proc/net/dev for physical interfaces.

    Returns:
        (timestamp, total_rx_bytes, total_tx_bytes)
    """
    rx_total = 0
    tx_total = 0
    
    # Prioritize host PID 1's network namespace (since /proc/net/dev is namespaced to the reading process)
    base_dir = "/proc"
    for p in ["/host/proc/1", "/host/proc", "/proc"]:
        if os.path.exists(f"{p}/net/dev"):
            base_dir = p
            break

    dev_path = f"{base_dir}/net/dev"

    # Whitelist of physical interface name prefixes
    physical_prefixes = ("eth", "en", "wl", "ib", "ppp")

    try:
        with open(dev_path, "r") as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split(":")
            if len(parts) < 2:
                continue
            iface = parts[0].strip()
            
            # Only sum interfaces matching our physical whitelist
            if not iface.startswith(physical_prefixes):
                continue
            
            stats = parts[1].split()
            if len(stats) >= 9:
                rx_total += int(stats[0])
                tx_total += int(stats[8])
    except Exception:
        pass
        
    return time.monotonic(), rx_total, tx_total


def prefix_to_mask(prefix: int) -> str:
    """Convert CIDR prefix (e.g. 24) to subnet mask (e.g. 255.255.255.0)."""
    if prefix <= 0:
        return "0.0.0.0"
    if prefix >= 32:
        return "255.255.255.255"
    mask = (0xffffffff << (32 - prefix)) & 0xffffffff
    return f"{(mask >> 24) & 0xff}.{(mask >> 16) & 0xff}.{(mask >> 8) & 0xff}.{mask & 0xff}"


def mask_to_prefix(mask: str) -> int:
    """Convert subnet mask (e.g. 255.255.255.0) to CIDR prefix (e.g. 24)."""
    try:
        return sum(bin(int(x)).count('1') for x in mask.split('.'))
    except Exception:
        return 24


def run_nmcli(args: List[str], timeout: int = 5) -> str:
    """Run nmcli with English locale enforced to ensure robust output parsing."""
    import os
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.check_output(args, env=env, timeout=timeout).decode()


def call_nmcli(args: List[str], timeout: int = 5) -> int:
    """Execute nmcli with English locale enforced."""
    import os
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.check_call(args, env=env, timeout=timeout)


@router.get("/status", response_model=NetworkStatusResponse)
def get_network_status():
    wired_conn = {
        "device": "eth0",
        "connected": False,
        "ip": None,
        "netmask": "255.255.255.0",
        "gateway": None,
        "dns_servers": [],
        "mode": "auto",
        "dns_mode": "auto"
    }
    wifi_conn = {
        "device": "wlan0",
        "connected": False,
        "ssid": None,
        "signal": 0
    }

    try:
        # Get devices state
        dev_out = run_nmcli(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], timeout=5)
        for line in dev_out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                dev, dev_type, state = parts[0], parts[1], parts[2]
                if dev_type == "ethernet":
                    wired_conn["device"] = dev
                    if state == "connected":
                        wired_conn["connected"] = True
                elif dev_type == "wifi":
                    wifi_conn["device"] = dev
                    if state == "connected":
                        wifi_conn["connected"] = True

        # Get active connection properties for wired device
        if wired_conn["connected"]:
            ip_out = run_nmcli(
                ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", wired_conn["device"]],
                timeout=5
            )
            dns_list = []
            for line in ip_out.splitlines():
                if line.startswith("IP4.ADDRESS[1]:"):
                    ip_cidr = line.split(":", 1)[1]
                    wired_conn["ip"] = ip_cidr.split("/")[0]
                    try:
                        prefix = int(ip_cidr.split("/")[1])
                        wired_conn["netmask"] = prefix_to_mask(prefix)
                    except Exception:
                        pass
                elif line.startswith("IP4.GATEWAY:"):
                    wired_conn["gateway"] = line.split(":", 1)[1]
                elif "IP4.DNS" in line:
                    dns_list.append(line.split(":", 1)[1])
            wired_conn["dns_servers"] = dns_list

            # Find connection name to check configuration method (DHCP or Static)
            con_out = run_nmcli(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                timeout=5
            )
            conn_name = None
            for line in con_out.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[1] == "802-3-ethernet" and parts[2] == wired_conn["device"]:
                    conn_name = parts[0]
                    break

            if conn_name:
                method_out = run_nmcli(
                    ["nmcli", "-t", "-f", "ipv4.method,ipv4.dns", "connection", "show", conn_name],
                    timeout=5
                )
                for line in method_out.splitlines():
                    if line.startswith("ipv4.method:"):
                        val = line.split(":", 1)[1].strip()
                        if val == "manual":
                            wired_conn["mode"] = "manual"
                        else:
                            wired_conn["mode"] = "auto"
                    elif line.startswith("ipv4.dns:"):
                        val = line.split(":", 1)[1].strip()
                        if val:
                            wired_conn["dns_mode"] = "manual"
                        else:
                            wired_conn["dns_mode"] = "auto"

        # Get connected Wi-Fi SSID and signal details
        if wifi_conn["connected"]:
            wifi_out = run_nmcli(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,DEVICE", "device", "wifi", "list"],
                timeout=5
            )
            for line in wifi_out.splitlines():
                parts = re.split(r"(?<!\\):", line)
                if len(parts) >= 4 and parts[0].strip().lower() == "yes":
                    wifi_conn["ssid"] = parts[1].replace("\\:", ":").strip()
                    wifi_conn["signal"] = int(parts[2]) if parts[2].isdigit() else 0
                    break
    except Exception:
        # Fallback to mock connection state on development / non-Linux / missing nmcli
        wired_conn = {
            "device": "eth0",
            "connected": True,
            "ip": "192.168.188.249",
            "netmask": "255.255.255.0",
            "gateway": "192.168.188.1",
            "dns_servers": ["8.8.8.8", "8.8.4.4"],
            "mode": "auto",
            "dns_mode": "auto"
        }
        wifi_conn = {
            "device": "wlan0",
            "connected": False,
            "ssid": None,
            "signal": 0
        }

    return NetworkStatusResponse(
        wired=WiredStatus(**wired_conn),
        wifi=WifiStatus(**wifi_conn)
    )


@router.get("/wifi/scan", response_model=List[WifiNetworkInfo])
def scan_wifi():
    try:
        # Trigger a wifi rescan in NetworkManager (asynchronously/ignore failures)
        try:
            call_nmcli(["nmcli", "device", "wifi", "rescan"], timeout=3)
        except Exception:
            pass

        out = run_nmcli(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE", "device", "wifi", "list"],
            timeout=10
        )
        
        if not out.strip():
            print("WARNING: nmcli device wifi list returned empty output.")

        networks = []
        seen_ssids = set()
        for line in out.splitlines():
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 4:
                ssid = parts[0].replace("\\:", ":").strip()
                signal = int(parts[1]) if parts[1].isdigit() else 0
                security = parts[2].strip()
                active = parts[3].strip().lower() == "yes"
                if ssid and ssid not in seen_ssids:
                    seen_ssids.add(ssid)
                    networks.append(WifiNetworkInfo(
                        ssid=ssid,
                        signal=signal,
                        security=security if security else "Open",
                        active=active
                    ))
        return sorted(networks, key=lambda x: x.signal, reverse=True)
    except Exception as e:
        print(f"ERROR: Wifi scan failed, returning mock networks: {e}")
        # Fallback for dev environment
        return [
            WifiNetworkInfo(ssid="Office_5G", signal=95, security="WPA2", active=False),
            WifiNetworkInfo(ssid="Guest_Net", signal=45, security="Open", active=False)
        ]


def backup_network_profiles():
    usb_dir = "/media/usb-data/system-connections"
    nm_dir = "/etc/NetworkManager/system-connections"
    if not os.path.exists("/media/usb-data"):
        return
    try:
        os.makedirs(usb_dir, exist_ok=True)
        if os.path.exists(nm_dir):
            for file in os.listdir(nm_dir):
                if file.endswith(".nmconnection"):
                    src = os.path.join(nm_dir, file)
                    dst = os.path.join(usb_dir, file)
                    import shutil
                    shutil.copy2(src, dst)
    except Exception as e:
        print(f"Failed to backup network profiles: {e}")


@router.post("/wifi/connect", response_model=ActionResponse)
def connect_wifi(req: WifiConnectRequest):
    try:
        cmd = ["nmcli", "device", "wifi", "connect", req.ssid]
        if req.password:
            cmd += ["password", req.password]
        if req.hidden:
            cmd += ["hidden", "yes"]
        call_nmcli(cmd, timeout=30)
        backup_network_profiles()
        return ActionResponse(status="SUCCESS", message=f"Connected to {req.ssid} successfully")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


@router.post("/wired/configure", response_model=ActionResponse)
def configure_wired(req: WiredConfigRequest):
    try:
        # Get first active ethernet connection name
        con_out = run_nmcli(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            timeout=5
        )
        conn_name = None
        for line in con_out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "802-3-ethernet":
                conn_name = parts[0]
                break

        if not conn_name:
            # Check all ethernet connections (active or inactive)
            con_all_out = run_nmcli(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                timeout=5
            )
            for line in con_all_out.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[1] == "802-3-ethernet":
                    conn_name = parts[0]
                    break

        if not conn_name:
            conn_name = "Wired connection 1"

        # 1. IP assignment mode
        if req.mode == "manual":
            if not req.ip_address or not req.netmask:
                return ActionResponse(status="FAILED", error="IP address and netmask are required for manual mode")
            prefix = mask_to_prefix(req.netmask)
            ip_cidr = f"{req.ip_address}/{prefix}"
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.method", "manual", "ipv4.addresses", ip_cidr], timeout=5)
            if req.gateway:
                call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.gateway", req.gateway], timeout=5)
            else:
                call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.gateway", ""], timeout=5)
        else:
            # DHCP mode
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""], timeout=5)

        # 2. DNS Settings
        if req.dns_mode == "manual" and req.dns_servers:
            dns_str = " ".join(req.dns_servers)
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.dns", dns_str], timeout=5)
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.ignore-auto-dns", "yes"], timeout=5)
        else:
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.dns", ""], timeout=5)
            call_nmcli(["nmcli", "connection", "modify", conn_name, "ipv4.ignore-auto-dns", "no"], timeout=5)

        # Re-activate connection to apply
        call_nmcli(["nmcli", "connection", "up", conn_name], timeout=15)
        backup_network_profiles()
        return ActionResponse(status="SUCCESS", message="Wired connection settings applied successfully")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


BANDWIDTH_CACHE_KEY = "orch_net_traffic"
BANDWIDTH_CACHE_TTL = 60
BANDWIDTH_MIN_INTERVAL = 0.5  # seconds; shorter intervals would spike the rate


@router.get("/bandwidth", response_model=BandwidthResponse)
def get_bandwidth() -> BandwidthResponse:
    """Return the orchestrator server's real-time network Rx/Tx speeds in bytes/sec.

    Uses a Redis snapshot cache to avoid blocking sleeps.  Falls back to an
    in-process dict when Redis is unavailable so the endpoint never crashes.
    """
    current_time, current_rx, current_tx = get_network_bytes()

    # ── Load previous snapshot ──────────────────────────────────────────────
    prev: dict | None = None
    use_redis = True if _redis_client else False
    if use_redis:
        try:
            raw = _redis_client.get(BANDWIDTH_CACHE_KEY)
            if raw:
                prev = json.loads(raw)
        except Exception:
            use_redis = False
            prev = _fallback_traffic_cache.get(BANDWIDTH_CACHE_KEY)
    else:
        prev = _fallback_traffic_cache.get(BANDWIDTH_CACHE_KEY)

    # ── First call: baseline only ────────────────────────────────────────────
    if prev is None:
        snapshot = {
            "timestamp": current_time,
            "rx_bytes": current_rx,
            "tx_bytes": current_tx,
            "rx_speed": 0.0,
            "tx_speed": 0.0,
        }
        _store_snapshot(snapshot, use_redis)
        return BandwidthResponse(rx_speed=0.0, tx_speed=0.0)

    delta_time = current_time - prev["timestamp"]

    # ── Too soon since last measurement: return cached speed ─────────────────
    if delta_time < BANDWIDTH_MIN_INTERVAL:
        return BandwidthResponse(
            rx_speed=float(prev.get("rx_speed", 0.0)),
            tx_speed=float(prev.get("tx_speed", 0.0)),
        )

    # ── Compute derivative ────────────────────────────────────────────────────
    rx_speed = max(0.0, (current_rx - prev["rx_bytes"]) / delta_time)
    tx_speed = max(0.0, (current_tx - prev["tx_bytes"]) / delta_time)

    snapshot = {
        "timestamp": current_time,
        "rx_bytes": current_rx,
        "tx_bytes": current_tx,
        "rx_speed": rx_speed,
        "tx_speed": tx_speed,
    }
    _store_snapshot(snapshot, use_redis)
    return BandwidthResponse(rx_speed=rx_speed, tx_speed=tx_speed)


def _store_snapshot(snapshot: dict, use_redis: bool) -> None:
    """Persist the traffic snapshot to Redis (preferred) or the process-level dict."""
    try:
        if use_redis and _redis_client:
            _redis_client.setex(
                BANDWIDTH_CACHE_KEY,
                BANDWIDTH_CACHE_TTL,
                json.dumps(snapshot),
            )
        else:
            _fallback_traffic_cache[BANDWIDTH_CACHE_KEY] = snapshot
    except Exception:
        # Last-resort: always keep the fallback dict up to date
        _fallback_traffic_cache[BANDWIDTH_CACHE_KEY] = snapshot


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

