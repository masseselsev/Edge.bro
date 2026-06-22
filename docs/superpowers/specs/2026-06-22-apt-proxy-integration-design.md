# APT Proxy Integration Design Spec

## Goal
Integrate a built-in APT caching proxy (`apt-cacher-ng`) into the orchestrator docker stack and configure edge node bootstrapping to use it as a Tier 3 fallback when native proxies and direct internet connection are both unavailable.

---

## 1. Architecture

We will add a new container named `apt-proxy` to the Docker Compose setup. It runs `apt-cacher-ng`, cache package files inside a Docker volume, and exposes port `3142` to the network.

### Docker Config
- **Dockerfile**: `docker/apt-proxy/Dockerfile`
  - Base image: `debian:bookworm-slim`
  - Packages: `apt-cacher-ng`
  - Command: Runs `apt-cacher-ng` in foreground (`ForeGround=1`)
- **Docker Compose Service**:
  - Service Name: `apt-proxy`
  - Exposed Port: `3142:3142`
  - Volume: `apt-cache:/var/cache/apt-cacher-ng` (shared named volume)

---

## 2. Ansible Playbook Logic (bootstrap.yml)

We will modify the raw task `Ensure python3/pip are installed` to support a 3-tier fallback logic:

1. **Tier 1 (Native Proxy)**:
   - Read from `apt-config dump`.
   - Test TCP connectivity to the native proxy. If reachable, proceed with APT updates.
2. **Tier 2 (Direct Internet Connection)**:
   - If native proxy is unreachable/unconfigured, test outbound TCP connectivity to standard APT repositories (`deb.debian.org` or `archive.ubuntu.com` on port 80).
   - If direct access works, rename native proxy configs to `*.disabled` and run APT.
3. **Tier 3 (Orchestrator APT Proxy)**:
   - If both native and direct fail, attempt connection to the orchestrator's proxy (port `3142`).
   - We will test:
     - The `orchestrator_ip` variable passed by the backend.
     - The incoming SSH connection source IP (parsed from `$SSH_CONNECTION`, `$SSH_CLIENT`, or output of `who -m`).
   - If reachable, write proxy configuration to `/etc/apt/apt.conf.d/99orchestrator-proxy` and disable native proxies.
   - Run APT update/install.

### Cleanup
- At the end of the playbook, delete `/etc/apt/apt.conf.d/99orchestrator-proxy` and restore any `.disabled` native proxy configurations.

---

## 3. Backend Implementation

- **orchestrator_ip Resolution**:
  - In `backend/tasks.py` (`bootstrap_node_task`), retrieve the `orchestrator_ip` by querying the DB settings, falling back to the `ORCHESTRATOR_IP` environment variable, or auto-detecting it from the routing table (using `ip route get <node_ip>`).
  - Pass this IP as an extra variable `orchestrator_ip` to the Ansible playbook.

---

## 4. Verification Plan

- **Unit Tests**:
  - Verify that `tasks.py` correctly passes `orchestrator_ip` to `bootstrap.yml`.
- **Manual Verification**:
  - Start the `apt-proxy` container.
  - Verify that we can retrieve packages through `http://localhost:3142`.
  - Simulate proxy unreachable / direct internet unreachable scenario (e.g. by blocking port 80/443 on a node but leaving 3142 open) and verify it falls back to orchestrator proxy during bootstrap.
