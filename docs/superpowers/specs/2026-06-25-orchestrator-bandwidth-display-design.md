# Orchestrator Bandwidth Display Design Spec

We want to implement a real-time network traffic/bandwidth display (download/upload speed) for the orchestrator server, placing it in the center of the global header.

## Goal

Provide administrators with visual insight into the current server network load. This is especially useful to confirm that backups, restores, or ISO downloads are transferring data actively.

## Technical Plan

### 1. Backend Bandwidth API
* **Endpoint**: `GET /api/network/bandwidth`
* **Access Control**: Admin users only (`require_admin`).
* **Source of Metrics**: `/proc/net/dev`.
  * Parse lines to get total Receive (Rx) and Transmit (Tx) bytes.
  * Ignore `lo` and virtual/container network prefixes (`docker`, `br-`, `veth`).
* **Rate Calculation Strategy**:
  * Store the last snapshot `{"timestamp": float, "rx_bytes": int, "tx_bytes": int, "rx_speed": float, "tx_speed": float}` in Redis under key `"orch_net_traffic"`.
  * On request:
    * Compute `delta_time = current_time - prev_timestamp`.
    * If `delta_time < 0.5` seconds, serve the previously calculated speeds (prevents rate spikes from concurrent UI requests).
    * If `delta_time >= 0.5` seconds, compute:
      * `rx_speed = max(0.0, (current_rx - prev_rx) / delta_time)` (Bytes/sec)
      * `tx_speed = max(0.0, (current_tx - prev_tx) / delta_time)` (Bytes/sec)
      * Save the new baseline and speed values back to Redis.
    * Fall back to an in-memory dictionary cache in the Python process context if Redis is down.

### 2. Frontend Integration
* **Polling Loop**: Triggered in a `useEffect` hook in `App.tsx` every 3 seconds, ONLY if `isAuthenticated && !isKiosk` is true.
* **Layout Centering**:
  * Adjust the header's top row to use a three-column distribution layout on medium screens and up (`md:flex-row`).
  * Brand Identity (Left: `flex-1 flex justify-start`).
  * Bandwidth Widget (Center: `flex-shrink-0 flex justify-center`).
  * Actions & Selectors (Right: `flex-1 flex justify-end`).
  * On mobile/small screens, elements stack and center vertically automatically.
* **Widget Presentation**:
  * Background matching existing aesthetics (`bg-zinc-950/40 border border-zinc-800/60 rounded-xl px-3 py-1.5`).
  * Font: Monospace (`font-mono text-[11px]`).
  * Indicators:
    * Download (RX): Green icon (`ArrowDown`), animated pulse when speed > 1 KB/s.
    * Upload (TX): Blue/indigo icon (`ArrowUp`), animated pulse when speed > 1 KB/s.
  * Formatter: Formats values into `B/s`, `KB/s`, `MB/s`, or `GB/s`.
* **Internationalization**: Add translation keys `bandwidthDownload` and `bandwidthUpload` to `translations.ts` for English, Russian, and Ukrainian.

## Verification Plan

### Automated Verification
* Unit test in `backend/tests` mocking `/proc/net/dev` and verifying speed calculation.

### Manual Verification
* Run the local backend & frontend app.
* Log in as an administrator.
* View the new bandwidth widget in the header.
* Generate network activity (e.g. download base ISO or run a backup test) and verify that the speeds update dynamically.
