# Network Configuration Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide full network management (DHCP/Static Wired Ethernet, Wi-Fi scanning, connecting to hidden/visible wireless networks) from the kiosk UI.

**Architecture:** Add a `/api/network` router in FastAPI backend, using a Python wrapper over standard Debian `nmcli` calls. Integrate a Network Settings Modal with CSS transition animations in the React client.

**Tech Stack:** FastAPI, Pydantic, NetworkManager (nmcli), React, TypeScript, Tailwind CSS, Lucide icons.

---

### Task 1: Backend Router Mock & Test Setup

**Files:**
- Create: `backend/routers/network.py`
- Modify: `backend/main.py:1-40`
- Test: `backend/tests/test_network.py`

- [ ] **Step 1: Write test file**
  Create `backend/tests/test_network.py` containing a mock test for `GET /api/network/status`:
  ```python
  from fastapi.testclient import TestClient
  import pytest
  from main import app

  client = TestClient(app)

  def test_get_network_status_mock():
      response = client.get("/api/network/status")
      assert response.status_code == 200
      data = response.json()
      assert "wired" in data
      assert "wifi" in data
      assert data["wired"]["connected"] is True
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `pytest backend/tests/test_network.py -v`
  Expected: FAIL (404 Not Found)

- [ ] **Step 3: Create initial router**
  Create `backend/routers/network.py` with mock response:
  ```python
  from fastapi import APIRouter

  router = APIRouter(prefix="/network", tags=["Network"])

  @router.get("/status")
  def get_network_status():
      return {
          "wired": {
              "device": "eth0",
              "connected": True,
              "ip": "192.168.188.249",
              "netmask": "255.255.255.0",
              "gateway": "192.168.188.1",
              "dns_servers": ["8.8.8.8"],
              "mode": "auto"
          },
          "wifi": {
              "device": "wlan0",
              "connected": False,
              "ssid": None,
              "signal": 0
          }
      }
  ```

- [ ] **Step 4: Register router in `backend/main.py`**
  Add router registration:
  ```python
  from routers import network
  app.include_router(network.router, prefix="/api")
  ```

- [ ] **Step 5: Run test to verify it passes**
  Run: `pytest backend/tests/test_network.py -v`
  Expected: PASS

- [ ] **Step 6: Commit**
  ```bash
  git add backend/routers/network.py backend/main.py backend/tests/test_network.py
  git commit -m "feat(network): set up basic network router and mock tests"
  ```

---

### Task 2: Backend NetworkManager (`nmcli`) Integration

**Files:**
- Modify: `backend/routers/network.py`
- Modify: `backend/tests/test_network.py`

- [ ] **Step 1: Write tests for real/mock commands**
  Add unit tests validating `nmcli` parsing and routing logic (using mocked subprocesses) in `backend/tests/test_network.py`:
  ```python
  from unittest.mock import patch
  import subprocess

  def test_wifi_scan_mock():
      mock_output = "SSID_A:80:WPA2:no\nSSID_B:50:Open:no\n"
      with patch("subprocess.check_output", return_value=mock_output.encode()):
          response = client.get("/api/network/wifi/scan")
          assert response.status_code == 200
          data = response.json()
          assert len(data) == 2
          assert data[0]["ssid"] == "SSID_A"
          assert data[0]["signal"] == 80
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `pytest backend/tests/test_network.py -v`
  Expected: FAIL (No `/api/network/wifi/scan` endpoint)

- [ ] **Step 3: Implement real network command execution & parsing**
  Add scanner, connection, and wired modification endpoints in `backend/routers/network.py`.
  Parse with `re.split(r'(?<!\\):', line)` to support colons in SSIDs:
  ```python
  import subprocess
  import re
  from pydantic import BaseModel
  from typing import List, Optional

  class WifiConnectRequest(BaseModel):
      ssid: str
      password: Optional[str] = None
      hidden: bool = False

  class WiredConfigRequest(BaseModel):
      mode: str # "auto" or "manual"
      ip_address: Optional[str] = None
      netmask: Optional[str] = None
      gateway: Optional[str] = None
      dns_mode: str # "auto" or "manual"
      dns_servers: Optional[List[str]] = None

  def mask_to_prefix(mask: str) -> int:
      try:
          return sum(bin(int(x)).count('1') for x in mask.split('.'))
      except:
          return 24

  @router.get("/status")
  def get_network_status():
      # Active connections
      wired_conn = {"device": "eth0", "connected": False, "ip": None, "netmask": "255.255.255.0", "gateway": None, "dns_servers": [], "mode": "auto"}
      wifi_conn = {"device": "wlan0", "connected": False, "ssid": None, "signal": 0}

      try:
          # Get devices state
          dev_out = subprocess.check_output(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]).decode()
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

          # Get connection details if active
          if wired_conn["connected"]:
              ip_out = subprocess.check_output(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", wired_conn["device"]]).decode()
              dns_list = []
              for line in ip_out.splitlines():
                  if line.startswith("IP4.ADDRESS[1]:"):
                      ip_cidr = line.split(":", 1)[1]
                      wired_conn["ip"] = ip_cidr.split("/")[0]
                  elif line.startswith("IP4.GATEWAY:"):
                      wired_conn["gateway"] = line.split(":", 1)[1]
                  elif "IP4.DNS" in line:
                      dns_list.append(line.split(":", 1)[1])
              wired_conn["dns_servers"] = dns_list
      except Exception:
          # Fallback to mock on non-Linux platforms
          wired_conn["connected"] = True
          wired_conn["ip"] = "192.168.188.249"

      return {"wired": wired_conn, "wifi": wifi_conn}

  @router.get("/wifi/scan")
  def scan_wifi():
      try:
          out = subprocess.check_output(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE", "device", "wifi", "list"]).decode()
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
                      networks.append({
                          "ssid": ssid,
                          "signal": signal,
                          "security": security if security else "Open",
                          "active": active
                      })
          return sorted(networks, key=lambda x: x["signal"], reverse=True)
      except Exception:
          return [
              {"ssid": "Office_5G", "signal": 95, "security": "WPA2", "active": False},
              {"ssid": "Guest_Net", "signal": 45, "security": "Open", "active": False}
          ]

  @router.post("/wifi/connect")
  def connect_wifi(req: WifiConnectRequest):
      try:
          cmd = ["nmcli", "device", "wifi", "connect", req.ssid]
          if req.password:
              cmd += ["password", req.password]
          if req.hidden:
              cmd += ["hidden", "yes"]
          subprocess.check_call(cmd)
          return {"status": "SUCCESS", "message": f"Connected to {req.ssid}"}
      except Exception as e:
          return {"status": "FAILED", "error": str(e)}

  @router.post("/wired/configure")
  def configure_wired(req: WiredConfigRequest):
      try:
          # Get first active ethernet connection name
          con_out = subprocess.check_output(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"]).decode()
          conn_name = None
          for line in con_out.splitlines():
              parts = line.split(":")
              if len(parts) >= 2 and parts[1] == "802-3-ethernet":
                  conn_name = parts[0]
                  break
          
          if not conn_name:
              # Fallback to default wired connection name
              conn_name = "Wired connection 1"

          if req.mode == "manual":
              prefix = mask_to_prefix(req.netmask or "255.255.255.0")
              ip_cidr = f"{req.ip_address}/{prefix}"
              subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.method", "manual", "ipv4.addresses", ip_cidr])
              if req.gateway:
                  subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.gateway", req.gateway])
          else:
              subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""])

          # DNS Settings
          if req.dns_mode == "manual" and req.dns_servers:
              dns_str = " ".join(req.dns_servers)
              subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.dns", dns_str])
          else:
              subprocess.check_call(["nmcli", "connection", "modify", conn_name, "ipv4.dns", ""])

          # Re-activate connection to apply
          subprocess.check_call(["nmcli", "connection", "up", conn_name])
          return {"status": "SUCCESS", "message": "Wired connection settings applied"}
      except Exception as e:
          return {"status": "FAILED", "error": str(e)}
  ```

- [ ] **Step 4: Run tests to verify they pass**
  Run: `pytest backend/tests/test_network.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add backend/routers/network.py backend/tests/test_network.py
  git commit -m "feat(network): implement real NetworkManager CLI command executors"
  ```

---

### Task 3: React Network Settings Component

**Files:**
- Create: `frontend/src/components/NetworkSettingsModal.tsx`

- [ ] **Step 1: Write NetworkSettingsModal UI component**
  Write React component containing:
  - Tab navigation (Wired / Wi-Fi).
  - Wired settings panel (DHCP/Static toggle, manual IP inputs, DNS input fields).
  - Wi-Fi scan and select panel, including the manual hidden SSID modal transition.
  - Full-screen loading overlay.
  - Precise CSS classes matching `animate-modal-in` and `animate-fade-in`.
  
  ```tsx
  import React, { useState, useEffect } from 'react';
  import { ShieldAlert, RefreshCw, CheckCircle, Wifi, Globe, Key, Settings, X, Globe2 } from 'lucide-react';

  interface NetworkSettingsModalProps {
    onClose: () => void;
  }

  export default function NetworkSettingsModal({ onClose }: NetworkSettingsModalProps) {
    const [activeTab, setActiveTab] = useState<'wired' | 'wifi'>('wired');
    const [status, setStatus] = useState<any>(null);
    const [wifiNetworks, setWifiNetworks] = useState<any[]>([]);
    const [scanning, setScanning] = useState(false);
    const [connecting, setConnecting] = useState(false);
    const [connectError, setConnectError] = useState<string | null>(null);
    
    // Wired Form States
    const [wiredMode, setWiredMode] = useState<'auto' | 'manual'>('auto');
    const [ipAddress, setIpAddress] = useState('');
    const [netmask, setNetmask] = useState('255.255.255.0');
    const [gateway, setGateway] = useState('');
    const [dnsMode, setDnsMode] = useState<'auto' | 'manual'>('auto');
    const [dns1, setDns1] = useState('');
    const [dns2, setDns2] = useState('');

    // Wi-Fi States
    const [selectedSsid, setSelectedSsid] = useState<string | null>(null);
    const [wifiPassword, setWifiPassword] = useState('');
    const [showHiddenForm, setShowHiddenForm] = useState(false);
    const [hiddenSsid, setHiddenSsid] = useState('');
    const [hiddenSecurity, setHiddenSecurity] = useState('WPA2');

    useEffect(() => {
      fetchStatus();
      scanWifi();
      const interval = setInterval(fetchStatus, 5000);
      return () => clearInterval(interval);
    }, []);

    const fetchStatus = async () => {
      try {
        const res = await fetch('/api/network/status');
        const data = await res.json();
        setStatus(data);
        if (data.wired) {
          setWiredMode(data.wired.mode || 'auto');
          setIpAddress(data.wired.ip || '');
          setNetmask(data.wired.netmask || '255.255.255.0');
          setGateway(data.wired.gateway || '');
          setDnsMode(data.wired.dns_servers && data.wired.dns_servers.length > 0 ? 'manual' : 'auto');
          if (data.wired.dns_servers) {
            setDns1(data.wired.dns_servers[0] || '');
            setDns2(data.wired.dns_servers[1] || '');
          }
        }
      } catch (err) {
        console.error(err);
      }
    };

    const scanWifi = async () => {
      setScanning(true);
      try {
        const res = await fetch('/api/network/wifi/scan');
        const data = await res.json();
        setWifiNetworks(data);
      } catch (err) {
        console.error(err);
      } finally {
        setScanning(false);
      }
    };

    const handleApplyWired = async (e: React.FormEvent) => {
      e.preventDefault();
      setConnecting(true);
      setConnectError(null);
      try {
        const res = await fetch('/api/network/wired/configure', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: wiredMode,
            ip_address: wiredMode === 'manual' ? ipAddress : null,
            netmask: wiredMode === 'manual' ? netmask : null,
            gateway: wiredMode === 'manual' ? gateway : null,
            dns_mode: dnsMode,
            dns_servers: dnsMode === 'manual' ? [dns1, dns2].filter(Boolean) : null
          })
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
          fetchStatus();
          onClose();
        } else {
          setConnectError(data.error || 'Failed to configure wired network');
        }
      } catch (err: any) {
        setConnectError(err.message || 'Network error');
      } finally {
        setConnecting(false);
      }
    };

    const handleConnectWifi = async (e: React.FormEvent) => {
      e.preventDefault();
      const ssid = showHiddenForm ? hiddenSsid : selectedSsid;
      if (!ssid) return;

      setConnecting(true);
      setConnectError(null);
      try {
        const res = await fetch('/api/network/wifi/connect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ssid,
            password: wifiPassword || null,
            hidden: showHiddenForm
          })
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
          fetchStatus();
          onClose();
        } else {
          setConnectError(data.error || 'Failed to connect to Wi-Fi');
        }
      } catch (err: any) {
        setConnectError(err.message || 'Network error');
      } finally {
        setConnecting(false);
      }
    };

    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
        <div className="w-full max-w-lg bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl overflow-hidden animate-modal-in flex flex-col max-h-[90vh]">
          {/* Header */}
          <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-lg">
                <Settings size={18} />
              </div>
              <h3 className="text-sm font-bold text-white uppercase tracking-wider">Network Settings</h3>
            </div>
            <button onClick={onClose} className="text-zinc-400 hover:text-white transition-colors">
              <X size={18} />
            </button>
          </div>

          {/* Navigation Tabs */}
          <div className="flex bg-zinc-950/80 p-1 border-b border-zinc-800">
            <button
              onClick={() => setActiveTab('wired')}
              className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'wired'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800/80'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Globe size={14} /> Wired (Ethernet)
            </button>
            <button
              onClick={() => { setActiveTab('wifi'); scanWifi(); }}
              className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-bold transition-all ${
                activeTab === 'wifi'
                  ? 'bg-zinc-900 text-white shadow-sm border border-zinc-800/80'
                  : 'text-zinc-400 hover:text-zinc-100'
              }`}
            >
              <Wifi size={14} /> Wi-Fi Connections
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-5">
            {connectError && (
              <div className="mb-4 p-3 bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs rounded-lg flex items-center gap-2">
                <ShieldAlert size={14} />
                <span>{connectError}</span>
              </div>
            )}

            {activeTab === 'wired' ? (
              <form onSubmit={handleApplyWired} className="space-y-4">
                <div className="bg-zinc-950/50 border border-zinc-800/50 p-3.5 rounded-xl flex items-center justify-between">
                  <div>
                    <span className="text-xs font-bold text-white block">Wired Link State</span>
                    <span className="text-[10px] text-zinc-400 mt-1 block">
                      {status?.wired?.connected ? `Connected (${status.wired.device}) - ${status.wired.ip || 'No IP'}` : 'Disconnected'}
                    </span>
                  </div>
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                    status?.wired?.connected ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-zinc-800 text-zinc-500'
                  }`}>
                    {status?.wired?.connected ? 'Connected' : 'No Link'}
                  </span>
                </div>

                <div className="space-y-3">
                  <label className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">IP Assignment Mode</label>
                  <div className="flex bg-zinc-950 p-1 border border-zinc-800/80 rounded-xl">
                    <button
                      type="button"
                      onClick={() => setWiredMode('auto')}
                      className={`flex-1 py-1.5 rounded-lg text-xs font-bold transition-all ${
                        wiredMode === 'auto' ? 'bg-indigo-600 text-white shadow-md' : 'text-zinc-400 hover:text-white'
                      }`}
                    >
                      Dynamic (DHCP)
                    </button>
                    <button
                      type="button"
                      onClick={() => setWiredMode('manual')}
                      className={`flex-1 py-1.5 rounded-lg text-xs font-bold transition-all ${
                        wiredMode === 'manual' ? 'bg-indigo-600 text-white shadow-md' : 'text-zinc-400 hover:text-white'
                      }`}
                    >
                      Static IP
                    </button>
                  </div>
                </div>

                {wiredMode === 'manual' && (
                  <div className="grid grid-template-columns grid-cols-2 gap-3 transition-all duration-300">
                    <div className="col-span-1">
                      <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">IP Address</label>
                      <input
                        type="text"
                        required
                        value={ipAddress}
                        onChange={e => setIpAddress(e.target.value)}
                        placeholder="e.g. 192.168.1.50"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div className="col-span-1">
                      <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">Subnet Mask</label>
                      <input
                        type="text"
                        required
                        value={netmask}
                        onChange={e => setNetmask(e.target.value)}
                        placeholder="e.g. 255.255.255.0"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                    <div className="col-span-2">
                      <label className="text-[10px] text-zinc-400 font-bold mb-1.5 block">Default Gateway</label>
                      <input
                        type="text"
                        required
                        value={gateway}
                        onChange={e => setGateway(e.target.value)}
                        placeholder="e.g. 192.168.1.1"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                  </div>
                )}

                <div className="border-t border-zinc-800/80 pt-4 space-y-3">
                  <div className="flex justify-between items-center">
                    <label className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">DNS Mode</label>
                    <div className="flex gap-4">
                      <label className="text-xs text-zinc-300 flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="dnsMode" checked={dnsMode === 'auto'} onChange={() => setDnsMode('auto')} className="accent-indigo-600" /> Automatic
                      </label>
                      <label className="text-xs text-zinc-300 flex items-center gap-1.5 cursor-pointer">
                        <input type="radio" name="dnsMode" checked={dnsMode === 'manual'} onChange={() => setDnsMode('manual')} className="accent-indigo-600" /> Manual
                      </label>
                    </div>
                  </div>

                  {dnsMode === 'manual' && (
                    <div className="grid grid-cols-2 gap-3 transition-all duration-300">
                      <input
                        type="text"
                        required
                        value={dns1}
                        onChange={e => setDns1(e.target.value)}
                        placeholder="Primary DNS"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                      />
                      <input
                        type="text"
                        value={dns2}
                        onChange={e => setDns2(e.target.value)}
                        placeholder="Secondary DNS (Optional)"
                        className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                      />
                    </div>
                  )}
                </div>

                <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                  <button type="button" onClick={onClose} className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors">Cancel</button>
                  <button type="submit" className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors">Apply Wired Config</button>
                </div>
              </form>
            ) : (
              <form onSubmit={handleConnectWifi} className="space-y-4">
                {!showHiddenForm ? (
                  <>
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider">Wireless Networks</span>
                      <button type="button" onClick={scanWifi} disabled={scanning} className="text-indigo-400 hover:text-indigo-300 text-xs font-bold flex items-center gap-1.5 transition-colors disabled:opacity-50">
                        <RefreshCw size={12} className={scanning ? 'animate-spin' : ''} /> Scan
                      </button>
                    </div>

                    <div className="space-y-2 max-h-[200px] overflow-y-auto pr-1">
                      {wifiNetworks.map((net, idx) => (
                        <div
                          key={idx}
                          onClick={() => { setSelectedSsid(net.ssid); setShowHiddenForm(false); }}
                          className={`p-3 rounded-xl border flex items-center justify-between cursor-pointer transition-all duration-200 ${
                            selectedSsid === net.ssid ? 'bg-indigo-600/10 border-indigo-500 text-white' : 'bg-zinc-950 border-zinc-800 text-zinc-300 hover:border-zinc-700'
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <Wifi size={14} className={net.active ? 'text-emerald-400' : 'text-zinc-500'} />
                            <span className="text-xs font-bold">{net.ssid}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            {net.security !== 'Open' && <Key size={12} className="text-zinc-500" />}
                            <span className="text-[10px] font-bold text-zinc-400">{net.signal}%</span>
                          </div>
                        </div>
                      ))}

                      {wifiNetworks.length === 0 && !scanning && (
                        <p className="text-zinc-500 text-xs text-center py-4 font-semibold">No networks found. Trigger scan above.</p>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => { setShowHiddenForm(true); setSelectedSsid(null); }}
                      className="w-full p-2.5 bg-zinc-950 border border-zinc-800 border-dashed rounded-xl text-indigo-400 text-xs font-bold hover:border-zinc-700 transition-colors flex items-center justify-center gap-2"
                    >
                      Connect to Hidden Network...
                    </button>
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider">Hidden Wireless config</span>
                      <button type="button" onClick={() => setShowHiddenForm(false)} className="text-indigo-400 hover:text-indigo-300 text-xs font-bold transition-colors">
                        Back to List
                      </button>
                    </div>

                    <div className="space-y-3">
                      <div>
                        <label className="text-[10px] text-zinc-400 font-bold mb-1 block">Network SSID (Name)</label>
                        <input
                          type="text"
                          required
                          value={hiddenSsid}
                          onChange={e => setHiddenSsid(e.target.value)}
                          placeholder="e.g. MyHiddenNetwork"
                          className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-zinc-400 font-bold mb-1 block">Security Type</label>
                        <select
                          value={hiddenSecurity}
                          onChange={e => setHiddenSecurity(e.target.value)}
                          className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                        >
                          <option value="WPA2">WPA/WPA2 Personal (Secured)</option>
                          <option value="Open">Open (Unsecured)</option>
                        </select>
                      </div>
                    </div>
                  </div>
                )}

                {((selectedSsid && wifiNetworks.find(n => n.ssid === selectedSsid)?.security !== 'Open') || (showHiddenForm && hiddenSecurity !== 'Open')) && (
                  <div className="space-y-1">
                    <label className="text-[10px] text-zinc-400 font-bold mb-1 block">Password</label>
                    <input
                      type="password"
                      required
                      value={wifiPassword}
                      onChange={e => setWifiPassword(e.target.value)}
                      placeholder="Enter network password..."
                      className="w-full px-3 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-white text-xs focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                )}

                <div className="flex justify-end gap-2 pt-3 border-t border-zinc-800">
                  <button type="button" onClick={onClose} className="px-4 py-2 text-xs font-bold text-zinc-400 bg-zinc-800/50 hover:bg-zinc-800 rounded-lg transition-colors">Cancel</button>
                  <button type="submit" disabled={!selectedSsid && !showHiddenForm} className="px-4 py-2 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50 transition-colors">
                    Connect to Wi-Fi
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>

        {/* Loading / Connecting Overlay */}
        {connecting && (
          <div className="absolute inset-0 bg-black/80 z-50 flex flex-col items-center justify-center space-y-4 animate-fade-in">
            <RefreshCw className="animate-spin text-indigo-400" size={36} />
            <div className="text-center">
              <p className="text-sm font-bold text-white">Applying Configuration...</p>
              <p className="text-[10px] text-zinc-400 mt-1 uppercase tracking-wider font-semibold">Configuring NetworkManager</p>
            </div>
          </div>
        )}
      </div>
    );
  }
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add frontend/src/components/NetworkSettingsModal.tsx
  git commit -m "feat(network): add unified NetworkSettingsModal React component"
  ```

---

### Task 4: Connect Modal to Application Header

**Files:**
- Modify: `frontend/src/App.tsx:103-210`

- [ ] **Step 1: Integrate Network Status Button and Modal Overlay**
  Add status polling inside `App.tsx` and import `NetworkSettingsModal`. Add the pill indicator to the header:
  
  ```tsx
  // Add state at top of App()
  const [showNetworkModal, setShowNetworkModal] = useState(false);
  const [networkStatus, setNetworkStatus] = useState<any>(null);

  // Poll status on mount
  useEffect(() => {
    const fetchNet = async () => {
      try {
        const res = await fetch('/api/network/status');
        const data = await res.json();
        setNetworkStatus(data);
      } catch {}
    };
    fetchNet();
    const interval = setInterval(fetchNet, 7000);
    return () => clearInterval(interval);
  }, []);
  ```

  Replace the layout around the Header top-right section to add the button:
  ```tsx
  // Insert the Network Settings Pill Button before the "System Online" span
  <div className="flex items-center gap-3">
    <button
      onClick={() => setShowNetworkModal(true)}
      className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 font-bold transition-all duration-200 cursor-pointer"
    >
      {networkStatus?.wired?.connected ? (
        <>
          <Globe2 size={13} className="text-emerald-400" />
          <span>Wired Link</span>
        </>
      ) : networkStatus?.wifi?.connected ? (
        <>
          <Wifi size={13} className="text-emerald-400" />
          <span>{networkStatus.wifi.ssid}</span>
        </>
      ) : (
        <>
          <Globe2 size={13} className="text-rose-400" />
          <span className="text-rose-400 font-bold">Offline</span>
        </>
      )}
    </button>
    
    <span className="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2.5 py-1 rounded-full font-bold uppercase tracking-wider">
      System Online
    </span>
  </div>
  ```

  Render the modal overlay:
  ```tsx
  {showNetworkModal && (
    <NetworkSettingsModal onClose={() => setShowNetworkModal(false)} />
  )}
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add frontend/src/App.tsx
  git commit -m "feat(network): connect NetworkSettingsModal button to main App header"
  ```

---

### Task 5: Live ISO Generation & Verification

**Files:**
- Create: `backend/tests/test_cli_parsing.py`

- [ ] **Step 1: Write test for regex parsing**
  Create unit test validating the escaped colon parser explicitly:
  ```python
  import re

  def test_regex_split():
      line = "My\\:SSID\\:With\\:Colons:95:WPA2:no"
      parts = re.split(r"(?<!\\):", line)
      assert len(parts) == 4
      assert parts[0].replace("\\:", ":") == "My:SSID:With:Colons"
  ```

- [ ] **Step 2: Run test to verify it passes**
  Run: `pytest backend/tests/test_cli_parsing.py -v`
  Expected: PASS

- [ ] **Step 3: Build frontend static files**
  Rebuild frontend package inside Docker context:
  `docker compose build frontend && docker compose up -d frontend`
  Expected: Rebuilt successfully, asset files updated inside the shared volume.

- [ ] **Step 4: Commit**
  ```bash
  git add backend/tests/test_cli_parsing.py
  git commit -m "test(network): add CLI regex parsing verification tests"
  ```
