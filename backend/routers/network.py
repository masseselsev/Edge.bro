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
    from database import get_db, SessionLocal
    from sqlalchemy.orm import Session
    import models
except ImportError:
    get_db = lambda: None
    SessionLocal = None
    Session = None
    models = None
from pydantic import BaseModel, Field
from typing import Optional, List

class BandwidthResponse(BaseModel):
    rx_speed: float = Field(..., description="Download speed in bytes/sec")
    tx_speed: float = Field(..., description="Upload speed in bytes/sec")
    rx_percent: float = Field(..., description="Download load in percent of limits")
    tx_percent: float = Field(..., description="Upload load in percent of limits")
    cpu_usage: float = Field(..., description="CPU utilization in percent (0-100)")
    ram_usage: float = Field(..., description="RAM utilization in percent (0-100)")


_redis_client = None
if redis:
    try:
        REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
        # Timeouts spelled out here rather than taken from core.redis_client,
        # which every other module uses. This file is copied verbatim into the
        # kiosk payload — see the note below about it carrying no auth — and
        # the payload ships only the modules listed in
        # `iso_tasks.INJECTED_CORE_MODULES`. Importing one that is not on that
        # list produces a kiosk whose network page is silently dead, which is
        # what `tests/test_iso_payload_injection.py` exists to prevent.
        #
        # The values matter for the same reason they do everywhere else:
        # redis-py's default is no timeout at all, so an unreachable Redis
        # would hang this endpoint rather than fall through to the in-process
        # cache below that was written for exactly that case.
        _redis_client = redis.Redis.from_url(
            REDIS_URL, socket_connect_timeout=3, socket_timeout=5
        )
    except Exception:
        pass

# Module-level fallback cache used when Redis is unavailable
_fallback_traffic_cache: dict = {}

# This module is shared verbatim with the kiosk payload client, which has no
# web session to authenticate against — network configuration there is local
# and physical. So the router declares no auth of its own; whoever mounts it
# decides. The orchestrator mounts it behind require_admin (see main.py); the
# kiosk mounts it bare. Previously this was inferred from an ImportError on
# routers.users, which meant any unrelated import failure in that module
# silently turned authorization off for every route here.
router = APIRouter(prefix="/network", tags=["Network"])

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


def get_cpu_times() -> tuple[float, float]:
    """Read CPU times from /proc/stat. Returns (total_time, idle_time)."""
    base_dir = "/proc"
    for p in ["/host/proc", "/proc"]:
        if os.path.exists(f"{p}/stat"):
            base_dir = p
            break
    try:
        with open(f"{base_dir}/stat", "r") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = line.split()
                    times = [float(x) for x in parts[1:9]]
                    total = sum(times)
                    idle = float(parts[4]) + float(parts[5])
                    return total, idle
    except Exception:
        pass
    return 0.0, 0.0


def get_ram_usage() -> float:
    """Read RAM usage from /proc/meminfo. Returns percentage (0-100)."""
    base_dir = "/proc"
    for p in ["/host/proc", "/proc"]:
        if os.path.exists(f"{p}/meminfo"):
            base_dir = p
            break
    try:
        mem_total = 0.0
        mem_avail = 0.0
        with open(f"{base_dir}/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = float(line.split()[1])
        if mem_total > 0:
            return 100.0 * (mem_total - mem_avail) / mem_total
    except Exception:
        pass
    return 0.0


BANDWIDTH_CACHE_KEY = "orch_net_traffic"
BANDWIDTH_CACHE_TTL = 60
BANDWIDTH_MIN_INTERVAL = 0.5  # seconds; shorter intervals would spike the rate


@router.get("/bandwidth", response_model=BandwidthResponse)
def get_bandwidth(db: Optional[Session] = Depends(get_db)) -> BandwidthResponse:
    """Return the orchestrator server's real-time CPU, RAM, and Network utilization.

    Uses a Redis snapshot cache to avoid blocking sleeps.  Falls back to an
    in-process dict when Redis is unavailable so the endpoint never crashes.
    """
    capacity_mbps = 1000
    temp_db = None
    try:
        if db is not None:
            settings = db.query(models.Settings).first()
            if settings and settings.server_net_capacity_mbps is not None:
                capacity_mbps = settings.server_net_capacity_mbps
        elif SessionLocal is not None and models is not None:
            temp_db = SessionLocal()
            settings = temp_db.query(models.Settings).first()
            if settings and settings.server_net_capacity_mbps is not None:
                capacity_mbps = settings.server_net_capacity_mbps
    except Exception:
        pass
    finally:
        if temp_db is not None:
            temp_db.close()

    limit_bytes = capacity_mbps * 125000  # 1 Mbps = 125,000 bytes/sec
    current_time, current_rx, current_tx = get_network_bytes()
    cpu_total, cpu_idle = get_cpu_times()
    ram_usage = get_ram_usage()

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
            "cpu_total": cpu_total,
            "cpu_idle": cpu_idle,
            "cpu_usage": 0.0,
        }
        _store_snapshot(snapshot, use_redis)
        return BandwidthResponse(
            rx_speed=0.0,
            tx_speed=0.0,
            rx_percent=0.0,
            tx_percent=0.0,
            cpu_usage=0.0,
            ram_usage=ram_usage,
        )

    delta_time = current_time - prev["timestamp"]

    # ── Too soon since last measurement: return cached speed ─────────────────
    if delta_time < BANDWIDTH_MIN_INTERVAL:
        rx_speed = float(prev.get("rx_speed", 0.0))
        tx_speed = float(prev.get("tx_speed", 0.0))
        rx_percent = min(100.0, 100.0 * rx_speed / limit_bytes) if limit_bytes > 0 else 0.0
        tx_percent = min(100.0, 100.0 * tx_speed / limit_bytes) if limit_bytes > 0 else 0.0
        return BandwidthResponse(
            rx_speed=rx_speed,
            tx_speed=tx_speed,
            rx_percent=rx_percent,
            tx_percent=tx_percent,
            cpu_usage=float(prev.get("cpu_usage", 0.0)),
            ram_usage=ram_usage,
        )

    # ── Compute derivative ────────────────────────────────────────────────────
    rx_speed = max(0.0, (current_rx - prev["rx_bytes"]) / delta_time)
    tx_speed = max(0.0, (current_tx - prev["tx_bytes"]) / delta_time)
    rx_percent = min(100.0, 100.0 * rx_speed / limit_bytes) if limit_bytes > 0 else 0.0
    tx_percent = min(100.0, 100.0 * tx_speed / limit_bytes) if limit_bytes > 0 else 0.0

    delta_cpu_total = cpu_total - prev.get("cpu_total", 0.0)
    delta_cpu_idle = cpu_idle - prev.get("cpu_idle", 0.0)
    if delta_cpu_total > 0:
        cpu_usage = max(0.0, min(100.0, 100.0 * (1.0 - (delta_cpu_idle / delta_cpu_total))))
    else:
        cpu_usage = prev.get("cpu_usage", 0.0)

    snapshot = {
        "timestamp": current_time,
        "rx_bytes": current_rx,
        "tx_bytes": current_tx,
        "rx_speed": rx_speed,
        "tx_speed": tx_speed,
        "cpu_total": cpu_total,
        "cpu_idle": cpu_idle,
        "cpu_usage": cpu_usage,
    }
    _store_snapshot(snapshot, use_redis)
    return BandwidthResponse(
        rx_speed=rx_speed,
        tx_speed=tx_speed,
        rx_percent=rx_percent,
        tx_percent=tx_percent,
        cpu_usage=cpu_usage,
        ram_usage=ram_usage,
    )



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
