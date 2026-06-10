# Network Configuration Integration Spec

Provide network management (Wired Ethernet and Wi-Fi) directly from the full-screen Kiosk interface via NetworkManager (`nmcli`).

## Visual & Aesthetic Guidelines
- The Network Settings Modal must use premium, smooth CSS transition animations:
  - Background overlay: `animate-fade-in` (fade-in opacity transition).
  - Modal container: `animate-modal-in` (scale-up transform transition).
  - Tab shifting, hover transitions, and inputs: standard Tailwind `transition-all duration-200` transitions.

---

## 1. API Specifications

The local FastAPI client backend will run as `root` and handle connections using `nmcli`. The primary orchestrator backend will mirror these endpoints with mock handlers to prevent errors when viewing settings from the server dashboard.

### `GET /api/network/status`
Returns the status of both wired and wireless devices.

**Response (JSON)**:
```json
{
  "wired": {
    "device": "eth0",
    "connected": true,
    "ip": "192.168.188.249",
    "netmask": "255.255.255.0",
    "gateway": "192.168.188.1",
    "dns_servers": ["8.8.8.8", "1.1.1.1"],
    "mode": "auto"
  },
  "wifi": {
    "device": "wlan0",
    "connected": false,
    "ssid": null,
    "signal": 0
  }
}
```

### `GET /api/network/wifi/scan`
Triggers a wireless scan and lists unique SSIDs with signal strength.

**Response (JSON)**:
```json
[
  { "ssid": "Office_5G", "signal": 95, "security": "WPA2", "active": false },
  { "ssid": "Guest_Free", "signal": 40, "security": "Open", "active": false }
]
```

### `POST /api/network/wifi/connect`
Connects the client to the specified Wi-Fi network. Supports hidden SSIDs.

**Request (JSON)**:
```json
{
  "ssid": "MySSID",
  "password": "Password",
  "hidden": true
}
```

**Response (JSON)**:
```json
{ "status": "SUCCESS", "message": "Connected to MySSID" }
```

### `POST /api/network/wired/configure`
Applies static/dynamic network settings to the wired interface.

**Request (JSON)**:
```json
{
  "mode": "auto", // "auto" (DHCP) or "manual" (Static)
  "ip_address": "192.168.1.50",
  "netmask": "255.255.255.0",
  "gateway": "192.168.1.1",
  "dns_mode": "auto", // "auto" or "manual"
  "dns_servers": ["8.8.8.8", "1.1.1.1"]
}
```

**Response (JSON)**:
```json
{ "status": "SUCCESS", "message": "Wired network settings applied" }
```

---

## 2. Shell Command Implementation Details

### Wi-Fi Scanning
```bash
nmcli -t -f SSID,SIGNAL,SECURITY,ACTIVE device wifi list
```
*Parses each line using regex `re.split(r'(?<!\\):', line)` to correctly isolate fields without splitting escaped colons.*

### Wi-Fi Connecting
```bash
nmcli device wifi connect "<SSID>" password "<PASSWORD>" hidden "<yes|no>"
```

### Wired DHCP Mode Config
```bash
nmcli connection modify "<Conn_Name>" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns ""
nmcli connection up "<Conn_Name>"
```

### Wired Static IP Config
```bash
nmcli connection modify "<Conn_Name>" ipv4.method manual ipv4.addresses "<IP>/<CIDR>" ipv4.gateway "<GW>" ipv4.dns "<DNS1> <DNS2>"
nmcli connection up "<Conn_Name>"
```
*(CIDR prefix is converted dynamically in Python from the Subnet Mask using mathematical bit-count conversion)*

---

## 3. UI Component Structure

### Network Settings Button (Header)
A network indicator pill placed in the main header of `App.tsx`:
- Icon changes based on current connection:
  - 🌐 Ethernet icon when Wired is connected.
  - 📶 Wi-Fi strength icon when Wi-Fi is connected.
  - ⚠️ Warning icon when disconnected.
- Displays label: `Wired: Connected`, `WiFi: <SSID>`, or `Offline (No Connection)`.
- Clicking it opens the configuration modal.

### Network Settings Modal (`components/NetworkSettingsModal.tsx`)
A tabbed layout containing:
- **Wired Tab**: Toggle for Dynamic/Static, input fields for IP, Mask, Gateway, DNS.
- **Wi-Fi Tab**: Scan button, list of scanned networks, "Add Hidden Network" button, password input field.
- **Full-Screen Loading Overlay**: Activates during connection attempts. Shows spin loader and connection steps.
