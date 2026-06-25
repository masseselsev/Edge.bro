# Orchestrator Bandwidth Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a real-time network traffic/bandwidth display (download/upload speed) for the orchestrator server, centering it neatly in the global header.

**Architecture:** Fetch traffic metrics from `/proc/net/dev` on active interfaces, cache snapshots in Redis to calculate non-blocking derivative speeds (`delta_bytes / delta_time`), and poll this endpoint every 3 seconds from the React frontend, displaying Rx and Tx speeds with custom Lucide icons.

**Tech Stack:** FastAPI, Pydantic, Redis, React, TypeScript, Tailwind CSS, Lucide Icons.

## Global Constraints
- Strictly use type hinting on the backend with Pydantic request/response models.
- Maximum file size: Do not exceed 500 lines. Keep code split and modular.
- Do not hardcode database/Redis passwords or secrets.
- Use CSS transitions for any layout elements or state changes to support the premium look-and-feel.
- Enable full multi-language support (i18n) for all elements using the custom translations module.

---

### Task 1: Backend Endpoint and Logic

**Files:**
- Modify: `backend/routers/network.py`
- Modify: `backend/tests/test_network.py`

**Interfaces:**
- Consumes: `/proc/net/dev`, `redis_client`
- Produces: `GET /api/network/bandwidth` returning `BandwidthResponse` schema with `rx_speed` and `tx_speed` in Bytes/sec.

- [ ] **Step 1: Write the failing tests**
  Add unit tests in `backend/tests/test_network.py` that mock `/proc/net/dev` and test speed calculation, including Redis caching, concurrency rate-limiting (under 0.5s), and process-level in-memory fallback.
  
  ```python
  def test_get_bandwidth_success():
      # Add tests verifying first request returns 0, and second request calculates correct delta
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `docker compose -p feat-auth exec backend pytest tests/test_network.py -k test_get_bandwidth`
  Expected: FAIL with Import or Attribute Error.

- [ ] **Step 3: Write minimal implementation**
  In `backend/routers/network.py`:
  - Import `redis` and import `redis_client` or initialize it if not imported.
  - Implement `BandwidthResponse` schema.
  - Implement `get_network_bytes()` parsing logic.
  - Implement the `/bandwidth` endpoint utilizing Redis cache and process-level fallback (module-global dict `_fallback_traffic_cache = {}`).

- [ ] **Step 4: Run test to verify it passes**
  Run: `docker compose -p feat-auth exec backend pytest tests/test_network.py -k test_get_bandwidth`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run: `git add backend/routers/network.py backend/tests/test_network.py && git commit -m "feat(network): implement bandwidth speed endpoint and tests"`

---

### Task 2: Translation Entries

**Files:**
- Modify: `frontend/src/i18n/translations.ts`

**Interfaces:**
- Produces: Translation keys `bandwidthDownload` and `bandwidthUpload` in translations map.

- [ ] **Step 1: Add translation keys**
  Add the following key-value pairs to the `translations` object for `en`, `ru`, and `uk` in `frontend/src/i18n/translations.ts`:
  - `en`: `bandwidthDownload: 'Download'`, `bandwidthUpload: 'Upload'`
  - `ru`: `bandwidthDownload: 'Загрузка'`, `bandwidthUpload: 'Отдача'`
  - `uk`: `bandwidthDownload: 'Завантаження'`, `bandwidthUpload: 'Віддача'`

- [ ] **Step 2: Commit**
  Run: `git add frontend/src/i18n/translations.ts && git commit -m "feat(i18n): add bandwidth translation strings"`

---

### Task 3: Frontend Polling and Layout Integration

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `GET /api/network/bandwidth`
- Produces: Bandwidth widget centered in the global header.

- [ ] **Step 1: Import icons and configure hooks**
  In `frontend/src/App.tsx`:
  - Import `ArrowDown` and `ArrowUp` from `lucide-react`.
  - Add state `bandwidth` (`{ rx_speed: number; tx_speed: number } | null`).
  - Add an effect (`useEffect`) to poll `/api/network/bandwidth` every 3 seconds only if `isAuthenticated && !isKiosk`.
  - Add `formatSpeed(bytesPerSec: number): string` formatting helper.

- [ ] **Step 2: Render Bandwidth Display in Header**
  Adjust the top row layout of `<header>` in `App.tsx`:
  - Change `flex-col sm:flex-row items-center justify-between gap-4` to `flex-col md:flex-row items-center justify-between gap-4`.
  - Wrap Logo/Title in a left-aligned container (`flex-1 w-full md:w-auto flex justify-center md:justify-start`).
  - Insert the `bandwidth` display widget in the center (`flex-shrink-0 flex justify-center`).
  - Wrap Actions/Selectors in a right-aligned container (`flex-1 w-full md:w-auto flex justify-center md:justify-end`).
  - Style the bandwidth display with standard zinc-based borders, background, monospace typography, and subtle micro-animations (e.g. `animate-pulse` on active traffic icons).

- [ ] **Step 3: Run typescript check and build**
  Run: `cd frontend && npm run build` (or similar command in the workspace) to verify there are no TypeScript errors.

- [ ] **Step 4: Commit**
  Run: `git add frontend/src/App.tsx && git commit -m "feat(frontend): center bandwidth widget in global header"`
