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
    """Read cumulative Rx/Tx bytes from /proc/net/dev for physical interfaces."""
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


# Include DHPC (local network/wifi) and WG (Wireguard) sub-routers
from routers.network_dhcp import router as dhcp_router, get_network_status, scan_wifi, connect_wifi, configure_wired
from routers.network_wg import router as wg_router, get_vpn_status, save_vpn_config, connect_vpn, disconnect_vpn, delete_vpn

router.include_router(dhcp_router)
router.include_router(wg_router)
