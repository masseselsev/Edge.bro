import subprocess
import re
from fastapi import APIRouter, Depends
from routers.users import require_admin
from pydantic import BaseModel, Field
from typing import Optional, List

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
        dev_out = subprocess.check_output(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"], timeout=5).decode()
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
            ip_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", wired_conn["device"]],
                timeout=5
            ).decode()
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
            con_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                timeout=5
            ).decode()
            conn_name = None
            for line in con_out.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[1] == "802-3-ethernet" and parts[2] == wired_conn["device"]:
                    conn_name = parts[0]
                    break

            if conn_name:
                method_out = subprocess.check_output(
                    ["nmcli", "-t", "-f", "ipv4.method,ipv4.dns", "connection", "show", conn_name],
                    timeout=5
                ).decode()
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
            wifi_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,DEVICE", "device", "wifi", "list"],
                timeout=5
            ).decode()
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
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE", "device", "wifi", "list"],
            timeout=10
        ).decode()
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
    except Exception:
        # Fallback for dev environment
        return [
            WifiNetworkInfo(ssid="Office_5G", signal=95, security="WPA2", active=False),
            WifiNetworkInfo(ssid="Guest_Net", signal=45, security="Open", active=False)
        ]


@router.post("/wifi/connect", response_model=ActionResponse)
def connect_wifi(req: WifiConnectRequest):
    try:
        cmd = ["nmcli", "device", "wifi", "connect", req.ssid]
        if req.password:
            cmd += ["password", req.password]
        if req.hidden:
            cmd += ["hidden", "yes"]
        subprocess.check_call(cmd, timeout=30)
        return ActionResponse(status="SUCCESS", message=f"Connected to {req.ssid} successfully")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))


@router.post("/wired/configure", response_model=ActionResponse)
def configure_wired(req: WiredConfigRequest):
    try:
        # Get first active ethernet connection name
        con_out = subprocess.check_output(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            timeout=5
        ).decode()
        conn_name = None
        for line in con_out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "802-3-ethernet":
                conn_name = parts[0]
                break

        if not conn_name:
            # Check all ethernet connections (active or inactive)
            con_all_out = subprocess.check_output(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                timeout=5
            ).decode()
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
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.method", "manual", "ipv4.addresses", ip_cidr], timeout=5)
            if req.gateway:
                subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.gateway", req.gateway], timeout=5)
            else:
                subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.gateway", ""], timeout=5)
        else:
            # DHCP mode
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""], timeout=5)

        # 2. DNS Settings
        if req.dns_mode == "manual" and req.dns_servers:
            dns_str = " ".join(req.dns_servers)
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.dns", dns_str], timeout=5)
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.ignore-auto-dns", "yes"], timeout=5)
        else:
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.dns", ""], timeout=5)
            subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.ignore-auto-dns", "no"], timeout=5)

        # Re-activate connection to apply
        subprocess.check_call(["nmcli", "connection", "up", conn_name], timeout=15)
        return ActionResponse(status="SUCCESS", message="Wired connection settings applied successfully")
    except Exception as e:
        return ActionResponse(status="FAILED", error=str(e))
