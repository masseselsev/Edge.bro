# APT Proxy Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate a built-in `apt-cacher-ng` proxy container and configure node bootstrap to fall back to it if native node proxies and direct internet are both unavailable.

**Architecture:** We add a new `apt-proxy` container running `apt-cacher-ng` to the Docker Compose stack with cached package persistence. The `bootstrap.yml` playbook uses a 3-tier fallback logic: check native proxies first, then direct internet connection, then the orchestrator's APT proxy. The node dynamically resolves the orchestrator IP from both the backend-passed parameter and the active SSH connection metadata.

**Tech Stack:** Docker Compose, apt-cacher-ng, Ansible, Python 3.13, FastAPI.

## Global Constraints
- Backend: Python 3.11, FastAPI, SQLAlchemy, Alembic, Celery, Ansible Runner (Note: Current project runs python:3.13-slim in Docker, Python rules apply)
- Maximum File Size: No single file must exceed 500 lines.
- Non-Persistent Terminal for Stateless Commands: Always run simple, stateless commands (e.g., git status, git diff, git add, running tests) with RunPersistent: false.

---

### Task 1: Docker Integration for apt-proxy

**Files:**
- Create: `docker/apt-proxy/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: None
- Produces: `apt-proxy` container running on port `3142` with persistent cache storage `apt-cache`

- [ ] **Step 1: Create Dockerfile for apt-proxy**
  Create `docker/apt-proxy/Dockerfile` with the following content:
  ```dockerfile
  FROM debian:bookworm-slim
  RUN apt-get update && apt-get install -y \
      apt-cacher-ng \
      && rm -rf /var/lib/apt/lists/*
  
  EXPOSE 3142
  CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1"]
  ```

- [ ] **Step 2: Add apt-proxy service to docker-compose.yml**
  Modify `docker-compose.yml` to insert the new service and volume:
  ```yaml
    apt-proxy:
      build:
        context: .
        dockerfile: docker/apt-proxy/Dockerfile
      restart: always
      ports:
        - "3142:3142"
      volumes:
        - apt-cache:/var/cache/apt-cacher-ng
  ```
  And under `volumes:` at the bottom of the file:
  ```yaml
    apt-cache:
  ```

- [ ] **Step 3: Build and start the container**
  Run: `docker compose build apt-proxy && docker compose up -d apt-proxy`
  Expected: Service starts successfully and status is running.

- [ ] **Step 4: Verify proxy functionality**
  Run: `curl -I http://localhost:3142`
  Expected: HTTP 200 OK or 403 Forbidden page served by apt-cacher-ng (confirming the daemon is listening).

- [ ] **Step 5: Commit changes**
  Run: `git add docker/apt-proxy/Dockerfile docker-compose.yml && git commit -m "feat(docker): add apt-proxy container with persistent volume"`

---

### Task 2: Ansible Playbook Fallback Logic

**Files:**
- Modify: `backend/playbooks/bootstrap.yml`

**Interfaces:**
- Consumes: `orchestrator_ip` variable from playbook extra_vars.
- Produces: 3-tier fallback behavior for package installation, temporary `/etc/apt/apt.conf.d/99orchestrator-proxy` file creation, and dynamic cleanup.

- [ ] **Step 1: Modify proxy verification and package installation in bootstrap.yml**
  Replace lines 50-77 in `backend/playbooks/bootstrap.yml` with the following implementation:
  ```yaml
      - name: Ensure python3/pip are installed (with dynamic proxy test and temporary fallback)
        raw: |
          # Tier 1: Check native proxy config
          PROXY_RAW=$(apt-config dump | grep -i 'Acquire::http::Proxy ' | head -n1 | sed -E 's/.*http:\/\/([^"/]+).*/\1/')
          if [ -n "$PROXY_RAW" ]; then
            PROXY_URL=$(echo "$PROXY_RAW" | sed -E 's/.*@//')
            PROXY_HOST=$(echo "$PROXY_URL" | cut -d: -f1)
            PROXY_PORT=$(echo "$PROXY_URL" | cut -d: -f2)
            if [ "$PROXY_PORT" = "$PROXY_HOST" ] || [ -z "$PROXY_PORT" ]; then
              PROXY_PORT=80
            fi
            if timeout 2 bash -c "cat < /dev/null > /dev/tcp/$PROXY_HOST/$PROXY_PORT" 2>/dev/null; then
              echo "Proxy $PROXY_HOST:$PROXY_PORT is reachable. Using proxy."
              apt-get update && apt-get install -y python3 python3-pip
              exit 0
            fi
          fi
  
          # Tier 2: Check direct internet access
          if timeout 2 bash -c "cat < /dev/null > /dev/tcp/deb.debian.org/80" 2>/dev/null || \
             timeout 2 bash -c "cat < /dev/null > /dev/tcp/archive.ubuntu.com/80" 2>/dev/null; then
            echo "Direct internet connection is available. Disabling native proxy and running directly."
            for path in /etc/apt/apt.conf /etc/apt/apt.conf.d; do
              if [ -e "$path" ]; then
                find "$path" -type f -exec grep -q "$PROXY_HOST" {} \; -print 2>/dev/null | while read -r f; do
                  mv "$f" "${f}.disabled"
                done
              fi
            done
            apt-get update && apt-get install -y python3 python3-pip
            exit 0
          fi
  
          # Tier 3: Check orchestrator proxy
          # Detect incoming SSH connection IP
          DETECTED_SSH_IP=$(echo "$SSH_CONNECTION" | awk '{print $1}')
          if [ -z "$DETECTED_SSH_IP" ]; then
            DETECTED_SSH_IP=$(echo "$SSH_CLIENT" | awk '{print $1}')
          fi
          if [ -z "$DETECTED_SSH_IP" ]; then
            DETECTED_SSH_IP=$(who -m 2>/dev/null | awk '{print $NF}' | tr -d '()')
          fi
  
          IP_LIST=""
          if [ -n "{{ orchestrator_ip }}" ]; then
            IP_LIST="{{ orchestrator_ip }}"
          fi
          if [ -n "$DETECTED_SSH_IP" ] && [ "$DETECTED_SSH_IP" != "{{ orchestrator_ip }}" ]; then
            IP_LIST="$IP_LIST $DETECTED_SSH_IP"
          fi
  
          PROXY_SUCCESS=false
          for test_ip in $IP_LIST; do
            if timeout 2 bash -c "cat < /dev/null > /dev/tcp/$test_ip/3142" 2>/dev/null; then
              echo "Direct connection and native proxy failed. Using orchestrator APT proxy at $test_ip:3142."
              # Disable native proxy configurations temporarily
              if [ -n "$PROXY_RAW" ]; then
                for path in /etc/apt/apt.conf /etc/apt/apt.conf.d; do
                  if [ -e "$path" ]; then
                    find "$path" -type f -exec grep -q "$PROXY_HOST" {} \; -print 2>/dev/null | while read -r f; do
                      mv "$f" "${f}.disabled"
                    done
                  fi
                done
              fi
              echo "Acquire::http::Proxy \"http://$test_ip:3142\";" > /etc/apt/apt.conf.d/99orchestrator-proxy
              apt-get update && apt-get install -y python3 python3-pip
              PROXY_SUCCESS=true
              break
            fi
          done
  
          if [ "$PROXY_SUCCESS" = "true" ]; then
            exit 0
          fi
  
          echo "All access methods failed (native proxy, direct internet, and orchestrator proxy)."
          exit 3
        changed_when: false
  ```

- [ ] **Step 2: Modify restore/cleanup task in bootstrap.yml**
  Replace lines 212-223 in `backend/playbooks/bootstrap.yml` with:
  ```yaml
      - name: Restore proxy configurations and clean up orchestrator proxy
        shell: |
          rm -f /etc/apt/apt.conf.d/99orchestrator-proxy
          for path in /etc/apt/apt.conf /etc/apt/apt.conf.d; do
            if [ -e "$path" ]; then
              find "$path" -type f -name "*.disabled" 2>/dev/null | while read -r f; do
                mv "$f" "${f%.disabled}"
              done
            fi
          done
        changed_when: false
        failed_when: false
  ```

- [ ] **Step 3: Commit changes**
  Run: `git add backend/playbooks/bootstrap.yml && git commit -m "feat(ansible): add 3-tier fallback logic and SSH IP auto-detection in bootstrap"`

---

### Task 3: Backend Integration & Testing

**Files:**
- Modify: `backend/tasks.py`
- Test: `backend/tests/test_network.py`

**Interfaces:**
- Consumes: `Settings` database entry, environment variables, network routing.
- Produces: Injects `orchestrator_ip` in the `extra_vars` mapping for `run_ansible_playbook`.

- [ ] **Step 1: Write failing test in test_network.py**
  Add a new test `test_bootstrap_node_task_passes_orchestrator_ip` in `backend/tests/test_network.py`:
  ```python
  def test_bootstrap_node_task_passes_orchestrator_ip(db_session, monkeypatch):
      from tasks import bootstrap_node_task
      from models import Node, Settings
      import ansible_utils
  
      # Ensure settings exist
      settings = db_session.query(Settings).first()
      if not settings:
          settings = Settings(orchestrator_ip="192.168.100.100")
          db_session.add(settings)
          db_session.commit()
      else:
          settings.orchestrator_ip = "192.168.100.100"
          db_session.commit()
  
      node = Node(hostname="test-node", ip_address="192.168.100.1")
      db_session.add(node)
      db_session.commit()
  
      passed_vars = {}
  
      def mock_run_playbook(task_id, playbook_name, host_ip, ssh_port, extra_vars, ssh_password=None):
          nonlocal passed_vars
          passed_vars = extra_vars
          return {"status": "SUCCESS", "parsed_data": {}}
  
      monkeypatch.setattr(ansible_utils, "run_ansible_playbook", mock_run_playbook)
  
      bootstrap_node_task.delay(node_id=node.id, bootstrap_user="root", ssh_password="pwd")
      
      assert passed_vars.get("orchestrator_ip") == "192.168.100.100"
  ```

- [ ] **Step 2: Verify test fails**
  Run: `docker compose exec backend pytest tests/test_network.py -k test_bootstrap_node_task_passes_orchestrator_ip`
  Expected: FAIL (KeyError or AssertionError because `orchestrator_ip` is not passed).

- [ ] **Step 3: Implement orchestrator_ip detection in tasks.py**
  In `backend/tasks.py`, inside `bootstrap_node_task` function, retrieve and pass `orchestrator_ip`:
  ```python
      settings = db.query(Settings).first()
      orchestrator_ip = settings.orchestrator_ip if settings else None
      if not orchestrator_ip:
          orchestrator_ip = os.getenv("ORCHESTRATOR_IP")
      if not orchestrator_ip:
          try:
              route_cmd = f"ip route get {node.ip_address}"
              route_out = subprocess.check_output(route_cmd, shell=True, text=True)
              orchestrator_ip = route_out.split("src")[1].split()[0]
          except Exception:
              orchestrator_ip = "127.0.0.1"
  ```
  And pass it inside `extra_vars`:
  ```python
      res = run_ansible_playbook(
          task_id=task_id,
          playbook_name="bootstrap.yml",
          host_ip=node.ip_address,
          ssh_port=node.ssh_port,
          extra_vars={
              "bootstrap_user": bootstrap_user,
              "orchestrator_ssh_pub_key": orchestrator_pub_key,
              "orchestrator_ip": orchestrator_ip
          },
          ssh_password=ssh_password
      )
  ```

- [ ] **Step 4: Verify test passes**
  Run: `docker compose exec backend pytest tests/test_network.py -k test_bootstrap_node_task_passes_orchestrator_ip`
  Expected: PASS.

- [ ] **Step 5: Run all backend tests**
  Run: `docker compose exec backend pytest`
  Expected: 34 passed (all tests pass).

- [ ] **Step 6: Commit changes**
  Run: `git add backend/tasks.py backend/tests/test_network.py && git commit -m "feat(backend): retrieve and pass orchestrator_ip to bootstrap playbook"`
