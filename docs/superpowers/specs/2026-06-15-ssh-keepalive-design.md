# Design Spec: SSH Keepalive for Backup Streams

This specification details the addition of SSH Keepalive settings for the backup task streams to handle slow, high-latency, or dropping connection environments (e.g. mobile networks).

## Context & Problem Statement
On unstable edge node connections, idle TCP sockets can be silently terminated by intermediate stateful NAT gateways or firewalls (especially on 3G/4G/5G connections) when no data flows for several minutes (e.g. during local compression/deduplication phases).
Additionally, if a connection drops entirely, the SSH client and python orchestrator process can hang indefinitely in ESTABLISHED status (up to the default Linux TCP timeout of 2 hours), locking up Celery worker queues.

## Proposed Changes

### 1. Configuration Parameters
Read parameters from environment variables (with defaults):
- `SSH_KEEPALIVE_INTERVAL`: Default `30` (seconds between keepalive messages).
- `SSH_KEEPALIVE_COUNT`: Default `3` (maximum consecutive missed keepalives before connection drop is declared).

### 2. Backend Command Construction
- **File**: `backend/backup_tasks.py`
- Modify `build_borg_create_cmd` and `run_backup_task` to inject:
  - `-o ServerAliveInterval=30`
  - `-o ServerAliveCountMax=3`
  into the outgoing SSH commands (Orchestrator -> Node) and `BORG_RSH` variables (Node -> Borg Server).

### 3. Unit Testing
- **File**: `backend/tests/test_db.py`
- Update unit tests (`test_checkpoint_calculation_and_command_builder`) to assert the generation of keepalive options in the built CLI commands.

## Verification Plan
1. **Automated Tests**: Run the pytest suite to ensure that generated SSH command strings contain the new keepalive arguments.
2. **Runtime Verification**: Trigger a backup process and verify via logs that the command executes with correct flags.
