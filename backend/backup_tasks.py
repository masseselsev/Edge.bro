import os
import subprocess
import json
import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional
from celery.exceptions import MaxRetriesExceededError
from celery_app import celery_app

from models import Node, TaskLog, BackupHistory, Settings, BackupGroup
from ansible_utils import run_ansible_playbook
from core.borg_local import borg_kwargs
from core.db_session import session_scope
from core import ssh
from core import backup_stats, repo_paths, transfer_speed
from core.clock import utcnow
from core.repo_lock import (
    LOCK_WAIT_SECONDS,
    maintenance_in_progress,
    repository_maintenance,
    repository_writer,
    writer_in_progress,
)
from core import node_lock
from core.node_lock import NodeLockBusy
from core.task_log import log_to_task
from core.redis_client import make_client as make_redis_client

# Re-use logging configuration from tasks
logger = logging.getLogger(__name__)

#: How often a running transfer refreshes its writer registration. Well under
#: the shortest TTL `backup_lock_ttl_seconds` produces, so a backup that runs
#: longer than its estimate is still registered rather than aging out and
#: leaving its lock exposed to the next node's pre-flight.
WRITER_HEARTBEAT_SECONDS = 60


def compute_checkpoint_interval(rate_kib: Optional[int]) -> int:
    """
    Auto-calculate Borg checkpoint interval in seconds from upload rate limit.
    Targets: ~50 MB at slow (<= 500 KiB/s), ~200 MB at medium, 1800s at fast/unlimited.
    """
    if rate_kib is None or rate_kib == 0:
        return 1800  # Borg default (~500 MB at fast speeds)
    if rate_kib <= 500:
        return max(60, (50 * 1024) // rate_kib)
    if rate_kib <= 5000:
        return max(120, (200 * 1024) // rate_kib)
    return 1800


def resolve_behind_nat(node, group, settings) -> bool:
    """Resolves the effective NAT setting for one node.

    Most fleets are uniform, but a single orchestrator can serve sites that
    differ, so the flag is overridable at three levels. The most specific
    non-NULL value wins:

        node.orchestrator_behind_nat        (per site)
        -> group.orchestrator_behind_nat    (per schedule group)
        -> settings.orchestrator_behind_nat (global default)

    NULL means "inherit", which is deliberately distinct from an explicit
    False — a node may need to opt OUT of a group that is behind NAT.
    """
    node_val = getattr(node, "orchestrator_behind_nat", None)
    if node_val is not None:
        return bool(node_val)

    group_val = getattr(group, "orchestrator_behind_nat", None) if group else None
    if group_val is not None:
        return bool(group_val)

    return bool(getattr(settings, "orchestrator_behind_nat", False))


def resolve_cpu_quota(node, group, settings) -> tuple[Optional[int], str]:
    """Resolves the effective CPU quota (percent of one core) for one node.

    NULL means "inherit" and keeps falling through; 0 is a distinct,
    terminal value meaning "explicit no limit" — deliberately different
    from core.transfer_speed.resolve_rate_limit, where a node value of 0
    falls through to the group. Here 0 must stop the chain so an operator
    can free one node from a group-wide cap.

        node.cpu_quota            (per node, 0 = explicit unlimited)
        -> group.cpu_quota        (per schedule group)
        -> settings.default_cpu_quota (global default)
    """
    node_val = getattr(node, "cpu_quota", None)
    if node_val is not None:
        return (node_val if node_val > 0 else None), "node"

    group_val = getattr(group, "cpu_quota", None) if group else None
    if group_val is not None:
        return group_val, "group"

    return getattr(settings, "default_cpu_quota", None), "default"


def resolve_borg_target(
    orchestrator_behind_nat: bool,
    direct_ip: Optional[str],
    borg_ssh_port: int,
    repo_path: str,
) -> tuple:
    """
    Decides how the node should reach the orchestrator's borg-server.

    Normally the node connects directly to the orchestrator's real IP. When
    the orchestrator sits behind NAT and nodes cannot reach it directly, we
    instead open a reverse tunnel on the SAME ssh connection the orchestrator
    already makes to the node (for bootstrap/backup-trigger, which only needs
    outbound reachability from the orchestrator's side): `-R
    {port}:borg-server:22` makes 127.0.0.1:{port} on the NODE forward back to
    the orchestrator's borg-server container. The node's own borg client then
    talks to itself instead of the unreachable real IP.

    Returns (extra_ssh_args, borg_repo_url). extra_ssh_args is empty in the
    direct case, so callers that don't pass it through get identical output
    to before this existed.
    """
    if orchestrator_behind_nat:
        extra_ssh_args = ["-R", f"{borg_ssh_port}:borg-server:22"]
        borg_repo_url = f"ssh://borg@127.0.0.1:{borg_ssh_port}{repo_path}"
        return extra_ssh_args, borg_repo_url

    borg_repo_url = f"ssh://borg@{direct_ip}:{borg_ssh_port}{repo_path}"
    return [], borg_repo_url


def build_borg_create_inner_cmd(
    borg_repo_url: str,
    archive_name: str,
    exclude_str: str,
    compression: str,
    rate_limit_kib: int,
    checkpoint_secs: int,
    cpu_quota: Optional[int],
    borg_passphrase: str,
) -> str:
    """
    Builds the shell fragment that runs borg create on the node, optionally
    wrapped in systemd-run --scope for CPU limiting.
    SSH Compression=no because Borg already compresses data chunks.

    Returns a plain string rather than a full SSH command: it is the last
    statement spliced into the single locked script
    `core.node_lock.build_locked_remote_script` assembles, alongside the
    pre-flight cleanup and `borg init` — one SSH call holding one flock for
    all of it, since a lock held by one call cannot be seen by another. See
    that module's docstring for why.
    """
    borg_env = (
        f"BORG_RSH='{ssh.borg_rsh()}' BORG_PASSPHRASE='{borg_passphrase}' "
        f"BORG_RELOCATED_REPO_ACCESS_IS_OK=yes "
        f"BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes"
    )
    borg_compression = compression.replace(":", ",")
    rate_limit_str = ""
    if rate_limit_kib and rate_limit_kib > 0:
        rate_limit_str = f"--remote-ratelimit {rate_limit_kib} "

    borg_create = (
        f"borg create --json --stats --log-json --progress "
        f"--lock-wait {LOCK_WAIT_SECONDS} "
        f"--compression {borg_compression} "
        f"--checkpoint-interval {checkpoint_secs} "
        f"{rate_limit_str}"
        f"{borg_repo_url}::{archive_name} / {exclude_str}"
    )

    if cpu_quota and cpu_quota > 0:
        return (
            f"systemd-run --scope "
            f"-p CPUQuota={cpu_quota}% "
            f"-- bash -c \"{borg_env} {borg_create}\""
        )
    return f"{borg_env} {borg_create}"


def force_cleanup_stale_repo_locks(task_id: str, repo_path: str) -> None:
    """
    Fallback lock cleanup: if borg break-lock fails (e.g. due to permissions or stale socket issues),
    force-removes any stale lock.* files inside repo_path on the file system level.
    """
    try:
        if os.path.exists(repo_path):
            removed = []
            for root, _, files in os.walk(repo_path):
                for fname in files:
                    if fname.startswith("lock."):
                        full_path = os.path.join(root, fname)
                        try:
                            os.remove(full_path)
                            removed.append(os.path.relpath(full_path, repo_path))
                        except Exception as e:
                            log_to_task(task_id, f"[Lock cleanup] Failed to force-remove {full_path}: {e}")
            if removed:
                log_to_task(task_id, f"[Lock cleanup] Fallback: Force-removed stale lock files: {', '.join(set(removed))}")
    except Exception as e:
        log_to_task(task_id, f"[Lock cleanup] Fallback lock cleanup exception: {e}")


def cleanup_locks_and_resolve_ip(
    task_id: str,
    node_ip: str,
    node_ssh_port: int,
    repo_path: str,
    borg_passphrase: str,
    configured_ip: Optional[str],
    borg_ssh_port: int,
    orchestrator_behind_nat: bool = False,
) -> Optional[str]:
    """
    Resolves the correct orchestrator IP to use by verifying configured IP
    reachability or falling back to the incoming SSH connection IP, and
    cleans up a stale repository lock on the server side.

    This is read-only on the node itself: pkill-ing orphaned borg processes
    and clearing the node's cache-lock files used to happen here too, but that
    is destructive, and a second orchestrator sharing this node can have a
    live backup in flight at the exact moment this pre-flight runs. That
    cleanup now happens inside the same locked script as `borg init`/`create`
    — see `core.node_lock` and `_transfer_and_record` — so it only ever runs
    once this orchestrator has confirmed nothing else on the node is using it.

    When orchestrator_behind_nat is True, direct reachability is known to be
    impossible (that's the whole point of the flag), so the reachability probe
    is skipped — it would only waste a timeout and log a misleading "unreachable,
    falling back" message. The repo-side lock check still runs. The return
    value is None in that case; callers must use resolve_borg_target() for the
    repo URL instead.
    """

    # In NAT mode direct reachability is impossible by definition, so don't even
    # attempt the /dev/tcp probe — it can only waste a timeout.
    test_ip = "" if orchestrator_behind_nat else (configured_ip.strip() if configured_ip else "")
    remote_cmd = (
        f"echo \"$SSH_CONNECTION\"; "
        f"if [ -n \"{test_ip}\" ] && timeout 2 bash -c \"cat < /dev/null > /dev/tcp/{test_ip}/{borg_ssh_port}\" 2>/dev/null; then "
        f"  echo \"REACHABLE:yes\"; "
        f"else "
        f"  echo \"REACHABLE:no\"; "
        f"fi; "
        f"echo OK"
    )

    detected_ip = None
    is_reachable = False

    try:
        res = subprocess.run(
            ssh.command(node_ip, node_ssh_port, remote_cmd, keepalive=True),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
        )
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            if lines:
                ssh_conn = lines[0].strip()
                parts = ssh_conn.split()
                if len(parts) >= 1:
                    detected_ip = parts[0]
                
                for line in lines[1:]:
                    if line.startswith("REACHABLE:"):
                        is_reachable = line.split(":")[1].strip() == "yes"
                        break
        else:
            log_to_task(task_id, f"[Lock cleanup] WARNING: Pre-backup check failed: {res.stderr.strip()}")
    except Exception as e:
        log_to_task(task_id, f"[Lock cleanup] WARNING: Pre-backup check exception: {e}")

    # Break the repository lock only if nothing is legitimately holding it.
    #
    # This used to be unconditional, and the lock it took away was as likely to
    # belong to the running nightly prune as to a dead worker. Breaking a live
    # lock does not queue the backup behind the holder — it lets both write to
    # the same segments and manifest at once, which is repository corruption,
    # not contention. See core/repo_lock.py.
    #
    # Another *backup* is as real a holder as the prune, and is the common one:
    # every node in a shard shares that shard's repository. Guarding only on
    # maintenance also made `--lock-wait` inert, since a backup that tears the
    # lock away never reaches the wait it was given.
    if maintenance_in_progress(repo_path):
        log_to_task(
            task_id,
            "[Lock cleanup] Repository maintenance is in progress; leaving the "
            "repo lock alone. This backup will be retried on the next tick.",
        )
    elif writer_in_progress(repo_path):
        log_to_task(
            task_id,
            f"[Lock cleanup] Another backup is writing to {repo_path}; leaving "
            f"the repo lock alone. This backup will queue behind it for up to "
            f"{LOCK_WAIT_SECONDS}s.",
        )
    else:
        try:
            env = os.environ.copy()
            env["BORG_PASSPHRASE"] = borg_passphrase
            res = subprocess.run(
                ["borg", "break-lock", repo_path],
                env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30,
                **borg_kwargs(repo_path, env),
            )
            if res.returncode == 0:
                log_to_task(task_id, "[Lock cleanup] Repo lock check passed (no stale lock, or lock broken).")
            else:
                log_to_task(task_id, f"[Lock cleanup] WARNING: Repo break-lock failed: {res.stderr.strip()}")
                force_cleanup_stale_repo_locks(task_id, repo_path)
        except Exception as e:
            log_to_task(task_id, f"[Lock cleanup] WARNING: Server-side lock check exception: {e}")
            force_cleanup_stale_repo_locks(task_id, repo_path)

    if orchestrator_behind_nat:
        log_to_task(task_id, "Orchestrator is behind NAT — will reach it through a reverse tunnel instead of a direct IP.")
        return None

    resolved_ip = None
    if is_reachable and test_ip:
        resolved_ip = test_ip
        log_to_task(task_id, f"Using configured orchestrator IP: {resolved_ip}")
    elif detected_ip:
        resolved_ip = detected_ip
        if test_ip:
            log_to_task(task_id, f"Configured IP {test_ip} is unreachable from node. Falling back to SSH connection IP: {resolved_ip}")
        else:
            log_to_task(task_id, f"Using auto-detected orchestrator IP from SSH connection: {resolved_ip}")
    else:
        resolved_ip = test_ip or os.getenv("ORCHESTRATOR_IP") or "127.0.0.1"
        log_to_task(task_id, f"Fallbacks exhausted. Using default/configured orchestrator IP: {resolved_ip}")

    return resolved_ip



@celery_app.task(bind=True)
def run_prepare_task(self, node_id: int) -> Dict[str, Any]:
    """
    Celery task to run the Auto-Prepare disk labels playbook on the node.

    Args:
        node_id: ID of the Node database record.

    Returns:
        Status result dictionary.
    """
    return _run_prepare(node_id, self.request.id, log_to_task)


def _run_prepare(node_id, task_id, log_to_task) -> Dict[str, Any]:
    # Read, run, write — with no session held across the playbook. See
    # core.db_session for why that separation matters here.
    with session_scope() as db:
        node = db.query(Node).filter(Node.id == node_id).first()
        if not node:
            return {"status": "FAILED", "error": "Node not found"}
        node_ip, node_port, node_hostname = node.ip_address, node.ssh_port, node.hostname
        db.add(TaskLog(
            id=task_id, task_type="PREPARE", status="RUNNING", node_id=node_id, log_output=""
        ))

    log_to_task(task_id, f"Starting auto-prepare for {node_hostname} ({node_ip})")

    res = run_ansible_playbook(
        task_id=task_id,
        playbook_name="prepare.yml",
        host_ip=node_ip,
        ssh_port=node_port,
        extra_vars={},
        ssh_key_path="/root/.ssh/id_ed25519"
    )

    with session_scope() as db:
        node = db.query(Node).filter(Node.id == node_id).first()
        if not node:
            return res

        if res["status"] == "SUCCESS":
            parsed = res["parsed_data"]
            node.disk_type = parsed.get("disk_type", "UNKNOWN")
            node.network_iface = parsed.get("network_iface")
            node.efi_uuid = parsed.get("efi_uuid")
            if "partition_layout" in parsed:
                node.partition_layout = parsed["partition_layout"]
            if "os_version" in parsed:
                node.os_version = parsed["os_version"]
            if "hostname" in parsed:
                node.hostname = parsed["hostname"]
            node.cpu_info = parsed.get("cpu_info")
            node.memory_info = parsed.get("memory_info")
            node.edge_version = parsed.get("edge_version")
            node.status = "READY"
            summary = (
                f"Auto-prepare finished. Disk type: {node.disk_type}, "
                f"EFI UUID: {node.efi_uuid}, Interface: {node.network_iface}, "
                f"CPU: {node.cpu_info}, RAM: {node.memory_info}, "
                f"Edge Version: {node.edge_version}"
            )
        else:
            node.status = "NEEDS_FIX"
            summary = None

    if summary:
        log_to_task(task_id, summary, status="SUCCESS")
    else:
        log_to_task(task_id, "Auto-prepare task failed.", status="FAILED")

    return res


@celery_app.task(bind=True)
def run_backup_task(self, node_id: int, comment: Optional[str] = None) -> Dict[str, Any]:
    """
    Triggers remote backup execution on the node pushing to the central Borg server,
    then updates Database history.

    Args:
        node_id: ID of the Node database record.
        comment: Optional comment to save with the backup.

    Returns:
        Status dictionary.
    """
    task_id = self.request.id
    from tasks import fix_repo_permissions
    import redis
    import time

    redis_client = make_redis_client()

    plan = _plan_backup(node_id)
    if plan is None:
        return {"status": "FAILED", "error": "Node not found"}

    redis_client.setex(
        f"backup_running:{plan.node_id}", plan.lock_ttl, f"{int(time.time())}:{task_id}"
    )

    # Set once a NodeLockBusy retry has actually been scheduled, so `finally`
    # below knows not to release the node lock — Celery keeps this task's id
    # across a retry, and this key is what stops the scheduler dispatching a
    # second backup for the same node during the countdown.
    retrying = False
    try:
        # Check Sentinel HASP license for READY nodes
        if plan.status == "READY":
            refusal = _refuse_unlicensed_node(plan, task_id, redis_client, log_to_task)
            if refusal is not None:
                return refusal

        with session_scope() as db:
            # A retry after NodeLockBusy re-enters this function with the same
            # task_id, so the row (and its accumulated log) may already exist.
            task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
            if task is None:
                db.add(TaskLog(
                    id=task_id, task_type="BACKUP", status="RUNNING",
                    node_id=node_id, log_output="",
                ))
            else:
                task.status = "RUNNING"

        return _transfer_and_record(plan, task_id, comment, log_to_task, fix_repo_permissions)
    except NodeLockBusy as e:
        log_to_task(
            task_id,
            "[WAITING] Node is currently busy with another backup task "
            "(possibly from a different orchestrator). Will retry shortly.",
        )
        retrying = True
        try:
            self.retry(
                exc=e,
                countdown=node_lock.NODE_LOCK_RETRY_COUNTDOWN_SECONDS,
                max_retries=node_lock.NODE_LOCK_MAX_RETRIES,
            )
        except MaxRetriesExceededError:
            retrying = False
            log_to_task(
                task_id,
                f"Gave up waiting for the node to become free after "
                f"{node_lock.NODE_LOCK_MAX_RETRIES} attempts.",
                status="FAILED",
            )
            return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        log_to_task(task_id, f"Exception occurred during backup task: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        if not retrying:
            try:
                redis_client.delete(f"backup_running:{plan.node_id}")
            except Exception:
                pass


@dataclass
class BackupPlan:
    """Everything a backup needs from the database, resolved up front.

    A `borg create` runs for hours. Reading node, group and settings into
    plain values means the transfer holds no connection and no transaction
    while it runs — which is the whole point, but it also has to be complete:
    anything missing here cannot be fetched later without reopening the very
    session this exists to avoid. See core.db_session.
    """
    node_id: int
    hostname: str
    ip_address: str
    ssh_port: int
    status: str
    hasp_runtime_version: Optional[str]
    group_id: Optional[int]
    orchestrator_ip: Optional[str]
    borg_ssh_port: int
    behind_nat: bool
    global_exclusions: Any
    rate_limit_kib: Optional[int]
    rate_limit_source: str
    compression: str
    checkpoint_secs: int
    cpu_quota: Optional[int]
    cpu_quota_source: str
    lock_ttl: int
    repo_path: str


def _plan_backup(node_id: int) -> Optional[BackupPlan]:
    """Resolve node, group and global settings into one flat, detached record."""
    from core.schedule_estimate import backup_lock_ttl_seconds

    with session_scope() as db:
        node = db.query(Node).filter(Node.id == node_id).first()
        if not node:
            return None

        settings = db.query(Settings).first()
        if not settings:
            settings = Settings()
            db.add(settings)
            db.flush()

        group = (
            db.query(BackupGroup).filter(BackupGroup.id == node.group_id).first()
            if node.group_id else None
        )

        rate_limit_kib, rate_limit_source = transfer_speed.resolve_rate_limit(
            node.upload_rate_limit, group.upload_rate_limit if group else None
        )
        cpu_quota, cpu_quota_source = resolve_cpu_quota(node, group, settings)

        return BackupPlan(
            node_id=node.id,
            hostname=node.hostname,
            ip_address=node.ip_address,
            ssh_port=node.ssh_port,
            status=node.status,
            hasp_runtime_version=node.hasp_runtime_version,
            group_id=node.group_id,
            orchestrator_ip=settings.orchestrator_ip,
            borg_ssh_port=settings.borg_ssh_port,
            # Effective NAT mode for THIS node: node override -> group -> global.
            behind_nat=resolve_behind_nat(node, group, settings),
            global_exclusions=list(settings.global_exclusions or []),
            rate_limit_kib=rate_limit_kib,
            rate_limit_source=rate_limit_source,
            compression=(
                (group.compression if group and group.compression else None)
                or getattr(settings, 'default_compression', None)
                or 'zstd:3'
            ),
            checkpoint_secs=(
                group.checkpoint_interval
                if group and group.checkpoint_interval is not None
                else compute_checkpoint_interval(rate_limit_kib)
            ),
            cpu_quota=cpu_quota,
            cpu_quota_source=cpu_quota_source,
            # Sized from this node's history rather than a flat 4h: on slow
            # links a backup that outlives its lock gets killed by the next
            # scheduler tick. Sized from the limit that will actually apply,
            # otherwise a node capped slower than its group outlives its own
            # lock and gets killed.
            lock_ttl=backup_lock_ttl_seconds(db, node.id, rate_limit_kib or None),
            repo_path=repo_paths.repo_path_for_node(node),
        )


def _refuse_unlicensed_node(
    plan: BackupPlan, task_id: str, redis_client, log_to_task
) -> Optional[Dict[str, Any]]:
    """Abort the backup if the node's HASP license has lapsed.

    Returns a result dict when the backup must not proceed, None otherwise.
    A node whose license died is demoted out of READY: backing it up would
    capture a machine that cannot run, and the demotion is what surfaces the
    problem to an operator.
    """
    import time
    from core.hasp_helper import check_hasp_status_on_node

    # Wait if there's an active license lock (e.g. fingerprint download or
    # license update in progress)
    lock_key = f"license_lock:{plan.node_id}"
    for _ in range(5):
        if not redis_client.exists(lock_key):
            break
        time.sleep(1)

    hasp_status = check_hasp_status_on_node(plan)
    if hasp_status not in ("no_license", "clone_detected", "disabled", "expired"):
        return None

    from database import log_user_action
    with session_scope() as db:
        node = db.query(Node).filter(Node.id == plan.node_id).first()
        if node:
            node.status = "RESTORED"
        # A retry after NodeLockBusy re-enters with the same task_id, so the
        # row may already exist from an earlier attempt.
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if task is None:
            db.add(TaskLog(
                id=task_id, task_type="BACKUP", status="FAILED",
                node_id=plan.node_id, log_output="",
            ))
        else:
            task.status = "FAILED"
        db.flush()
        log_user_action(
            db, "System: License Monitor", "Node Status Demoted",
            f"Ready node '{plan.hostname}' detected with inactive/expired license "
            f"({hasp_status}) during backup. Status demoted to RESTORED.", None,
        )

    log_to_task(
        task_id,
        f"Backup aborted: Node HASP license status is inactive ({hasp_status}). "
        f"Licence update is required.",
        status="FAILED",
    )
    return {"status": "FAILED", "error": f"Inactive license status: {hasp_status}"}


def _transfer_and_record(
    plan: BackupPlan, task_id: str, comment: Optional[str], log_to_task, fix_repo_permissions
) -> Dict[str, Any]:
    """Run `borg create` against the node and record the outcome.

    Holds no database session for the duration — the only writes are the two
    short scopes at the end. Everything it needs is already in `plan`.
    """
    log_to_task(task_id, f"Initiating Borg backup for {plan.hostname}...")

    # --- Pre-backup check: resolve orchestrator IP and clean locks ---
    orchestrator_ip = cleanup_locks_and_resolve_ip(
        task_id=task_id,
        node_ip=plan.ip_address,
        node_ssh_port=plan.ssh_port,
        repo_path=plan.repo_path,
        borg_passphrase=os.getenv("BORG_PASSPHRASE", ""),
        configured_ip=plan.orchestrator_ip,
        borg_ssh_port=plan.borg_ssh_port,
        orchestrator_behind_nat=plan.behind_nat,
    )

    archive_name = f"{plan.hostname}-{utcnow().strftime('%Y%m%d%H%M%S')}"
    extra_ssh_args, borg_repo_url = resolve_borg_target(
        orchestrator_behind_nat=plan.behind_nat,
        direct_ip=orchestrator_ip,
        borg_ssh_port=plan.borg_ssh_port,
        repo_path=plan.repo_path,
    )

    # Announce this backup as a live writer of the repository before any
    # borg touches it, and keep the registration until the transfer is
    # done. Another node bound for the same shard reads this in its own
    # pre-flight and leaves our lock alone; without it, that pre-flight
    # breaks the lock out from under this transfer. TTL comes from this
    # node's own history, the same estimate the node-level backup lock
    # uses, and the heartbeat below carries a backup that outruns it.
    with repository_writer(
        f"backup:{plan.node_id}", plan.repo_path, ttl=plan.lock_ttl
    ) as writer_beat:
        fix_repo_permissions(plan.repo_path)

        exclude_args = []
        for ex in plan.global_exclusions:
            pattern = None
            if isinstance(ex, dict):
                pattern = ex.get("pattern")
            elif isinstance(ex, str):
                pattern = ex

            if pattern:
                pat_stripped = pattern.strip()
                if pat_stripped:
                    exclude_args.append(f"--exclude '{pat_stripped}'")
        exclude_str = " ".join(exclude_args)

        rate_limit_kib = plan.rate_limit_kib
        limit_mbps = transfer_speed.kib_s_to_mbps(rate_limit_kib) if rate_limit_kib else None
        if rate_limit_kib:
            rate_text = (
                f"{rate_limit_kib} KiB/s ({transfer_speed.format_mbps(limit_mbps)}), "
                f"set on the {plan.rate_limit_source}"
            )
        else:
            rate_text = "unlimited"

        cpu_quota_text = (
            f"{plan.cpu_quota}% (from the {plan.cpu_quota_source})"
            if plan.cpu_quota
            else f"unlimited (from the {plan.cpu_quota_source})"
        )

        log_to_task(task_id, (
            f"Resource limits — compression: {plan.compression}, "
            f"upload rate: {rate_text}, "
            f"checkpoint: {plan.checkpoint_secs}s, "
            f"cpu_quota: {cpu_quota_text}"
        ))

        # Pkill/cache-lock cleanup, `borg init` and `borg create` all run as one
        # shell script under one exclusive, non-blocking flock on the node's
        # filesystem — a lock file on the node is the only thing two
        # independent orchestrator installs sharing this node can coordinate
        # through. See core.node_lock for why this can't be three SSH calls.
        cleanup_cmd = (
            "pkill -x borg || true; "
            "find /root/.cache/borg -name 'lock*' -delete 2>/dev/null || true; "
        )

        # Compression on: `borg init` writes a tiny repo config, not chunks.
        # Redirected to stderr (not the JSON stdout `borg create` parses below)
        # with its exit code captured via a marker line, since init and create
        # now share one script and init's own status would otherwise be
        # overwritten by create's before Python ever sees it.
        init_cmd = (
            f"BORG_RSH='{ssh.borg_rsh(compression=True)}' "
            f"BORG_PASSPHRASE='{os.getenv('BORG_PASSPHRASE', '')}' "
            f"BORG_RELOCATED_REPO_ACCESS_IS_OK=yes "
            f"BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes "
            f"borg init --lock-wait {LOCK_WAIT_SECONDS} "
            f"--encryption=repokey {borg_repo_url} >&2; "
            f"echo \"{node_lock.INIT_RC_MARKER}:$?\" >&2;"
        )

        create_inner_cmd = build_borg_create_inner_cmd(
            borg_repo_url=borg_repo_url,
            archive_name=archive_name,
            exclude_str=exclude_str,
            compression=plan.compression,
            rate_limit_kib=rate_limit_kib,
            checkpoint_secs=plan.checkpoint_secs,
            cpu_quota=plan.cpu_quota,
            borg_passphrase=os.getenv('BORG_PASSPHRASE', ''),
        )

        remote_script = node_lock.build_locked_remote_script(cleanup_cmd, init_cmd, create_inner_cmd)
        ssh_cmd = ssh.command(
            plan.ip_address, plan.ssh_port, remote_script,
            connect_timeout=None,
            keepalive=True,
            extra_args=extra_ssh_args,
        )

        log_to_task(task_id, "Checking/Initializing Borg repository and starting transfer...")
        log_to_task(task_id, f"Running remote command on node: {' '.join(ssh_cmd[:6])} [COMMAND MASKED]")

        # stderr is consumed line by line: borg reports cumulative byte counters
        # there several times a second, which is the only way to see how fast
        # the transfer actually ran rather than just its average.
        process = subprocess.Popen(
            ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
        )

        tracker = transfer_speed.SpeedTracker()
        stdout_chunks: list = []
        stderr_lines: list = []
        started_at = time_module.monotonic()

        def _drain_stdout() -> None:
            # Read concurrently, otherwise a full stdout pipe deadlocks the
            # child while we are still blocked reading stderr.
            for chunk in process.stdout:
                stdout_chunks.append(chunk)

        stdout_reader = threading.Thread(target=_drain_stdout, daemon=True)
        stdout_reader.start()

        # Borg emits progress several times a second; refreshing the writer
        # registration on every one would be thousands of pointless Redis
        # writes, so it is refreshed on a wall-clock interval instead.
        last_beat = started_at

        for raw_line in process.stderr:
            stripped = raw_line.strip()
            if stripped == node_lock.LOCK_BUSY_MARKER:
                continue
            if stripped.startswith(f"{node_lock.INIT_RC_MARKER}:"):
                init_rc = stripped.split(":", 1)[1]
                if init_rc not in ("0", "2"):
                    log_to_task(task_id, f"WARNING: Repository initialization exited with status {init_rc}.")
                continue

            now = time_module.monotonic()
            if now - last_beat >= WRITER_HEARTBEAT_SECONDS:
                writer_beat()
                last_beat = now

            kind, payload = transfer_speed.parse_borg_log_line(raw_line)
            if kind is transfer_speed.LineKind.PROGRESS:
                tracker.sample(payload.get("time"), payload.get("deduplicated_size"))
            elif kind is transfer_speed.LineKind.MESSAGE:
                stderr_lines.append(transfer_speed.render_message(payload))
            elif kind is transfer_speed.LineKind.PLAIN:
                stderr_lines.append(payload["message"])

        process.wait()
        stdout_reader.join(timeout=30)
        stdout = "".join(stdout_chunks)
        stderr = "\n".join(stderr_lines)
        wall_seconds = time_module.monotonic() - started_at

        if process.returncode == node_lock.LOCK_BUSY_EXIT_CODE:
            # Nothing was touched on the node — the flock was never acquired,
            # so the pre-flight cleanup, init and create never ran. Bail out
            # before the writer registration below even considers this a
            # backup attempt; run_backup_task turns this into a Celery retry.
            raise NodeLockBusy(
                f"Node {plan.hostname} is busy: another orchestrator's backup "
                f"currently holds its node lock."
            )

    log_to_task(task_id, f"Remote execution stdout:\n{stdout}")
    if stderr:
        log_to_task(task_id, f"Remote execution stderr:\n{stderr}")

    if process.returncode not in (0, 1):
        # Classified here rather than when the Archives page asks, so the
        # reliability panel never has to read a fleet's worth of logs.
        combined_log = stdout + "\n" + stderr
        category = backup_stats.classify_failure(combined_log)
        log_to_task(task_id, f"Failure category: {category}")

        with session_scope() as db:
            db.add(BackupHistory(
                node_id=plan.node_id,
                archive_name=archive_name,
                original_size=0,
                deduplicated_size=0,
                status="FAILED",
                log_output=combined_log,
                comment=comment,
                duration_seconds=wall_seconds,
                error_category=category,
            ))
        log_to_task(task_id, "Backup execution failed.", status="FAILED")
        return {"status": "FAILED", "error": stderr}

    if process.returncode == 1:
        log_to_task(task_id, "WARNING: Backup completed with warnings (some files changed during backup or were skipped).")

    original_size = 0
    deduplicated_size = 0
    compressed_size = None
    borg_duration = None
    try:
        data = json.loads(stdout)
        archive_stats = data.get("archive", {}).get("stats", {})
        original_size = archive_stats.get("original_size", 0)
        deduplicated_size = archive_stats.get("deduplicated_size", 0)
        # Every chunk this archive references, compressed — what a restore or a
        # kiosk sync actually transfers, as opposed to `deduplicated_size`,
        # which is only what this run added to the repository. None rather than
        # 0 when borg does not say, so the UI can tell "not recorded" from
        # "empty archive" and fall back to its estimate.
        compressed_size = archive_stats.get("compressed_size")
        borg_duration = data.get("archive", {}).get("duration")
    except Exception:
        log_to_task(task_id, "Failed to parse JSON directly; estimating size metrics.")

    # Deduplicated bytes are what actually crossed the network; original
    # bytes only say how much the node read off its own disk.
    duration = borg_duration or wall_seconds
    avg_mbps = transfer_speed.average_mbps(deduplicated_size, duration)
    max_mbps = tracker.max_mbps
    read_mbps = transfer_speed.average_mbps(original_size, duration)

    log_to_task(task_id, (
        f"Transfer speed — average: {transfer_speed.format_mbps(avg_mbps)}, "
        f"peak: {transfer_speed.format_mbps(max_mbps)} "
        f"(sustained over {tracker.window_seconds:.0f}s), "
        f"read from disk: {transfer_speed.format_mbps(read_mbps)}"
    ))

    if rate_limit_kib:
        binding = transfer_speed.limit_is_binding(max_mbps, limit_mbps)
        if binding is True:
            verdict = "the limit is being reached, so it is what caps this backup"
        elif binding is False:
            verdict = "the limit was never reached, so something else is the bottleneck"
        else:
            verdict = "not enough samples to tell whether the limit was reached"
        log_to_task(task_id, (
            f"Upload limit {rate_limit_kib} KiB/s "
            f"({transfer_speed.format_mbps(limit_mbps)}) from the {plan.rate_limit_source} — {verdict}"
        ))
    elif max_mbps is not None:
        log_to_task(task_id, "No upload limit configured; the link itself set the pace.")

    with session_scope() as db:
        db.add(BackupHistory(
            node_id=plan.node_id,
            archive_name=archive_name,
            original_size=original_size,
            deduplicated_size=deduplicated_size,
            compressed_size=compressed_size,
            status="SUCCESS",
            log_output=stdout + "\n" + stderr,
            comment=comment,
            avg_speed_mbps=avg_mbps,
            max_speed_mbps=max_mbps,
            duration_seconds=duration,
        ))
        node = db.query(Node).filter(Node.id == plan.node_id).first()
        if node:
            node.last_backup = utcnow()

    log_to_task(task_id, "Backup completed successfully.", status="SUCCESS")

    # A backup is the one moment the fleet reliably runs its nodes hard,
    # which makes the telemetry either side of it the most informative
    # the node will produce — see docs on why excitation is what the
    # thermal fit needs. Dispatched rather than run inline so a slow
    # harvest cannot extend the backup, and non-fatal because a
    # completed backup must never be reported as failed over telemetry.
    try:
        from tasks.monitoring import harvest_node_task
        harvest_node_task.apply_async(args=[plan.node_id], retry=False)
    except Exception as e:
        logger.warning(f"Could not schedule post-backup harvest: {e}")

    return {"status": "SUCCESS", "archive": archive_name}


@celery_app.task
def global_daily_prune() -> Dict[str, Any]:
    """
    Celery scheduled cron task running at 3:00 AM daily.
    Executes borg prune on a per-node basis using resolved retention policies,
    then compacts the Borg repository.
    """
    from tasks import fix_repo_permissions
    return _run_global_daily_prune(fix_repo_permissions)


def _retention_by_hostname() -> dict:
    """Every node's retention rules, keyed by hostname, in one short session."""
    from core.retention import rules_from_policy

    with session_scope() as db:
        settings = db.query(Settings).first()
        if not settings:
            settings = Settings()

        groups = {g.id: g for g in db.query(BackupGroup).all()}

        resolved = {}
        for node in db.query(Node).all():
            group = groups.get(node.group_id) if node.group_id else None

            policy = None
            if group and group.override_retention and group.retention_policy:
                policy = group.retention_policy
            elif settings.retention_policy:
                policy = settings.retention_policy

            resolved[node.hostname] = rules_from_policy(policy, settings)
        return resolved


def _list_archives(repo_path: str, env: dict) -> Optional[list]:
    """Every archive in the repository, newest first. None if borg would not say.

    One `borg list` replaces the manifest read that each of the old per-node
    prunes was doing on its own.
    """
    from core.retention import Archive

    res = subprocess.run(
        ["borg", "list", "--lock-wait", str(LOCK_WAIT_SECONDS), "--json", repo_path],
        env=env, capture_output=True, text=True, **borg_kwargs(repo_path, env),
    )
    if res.returncode != 0:
        logger.error(f"Could not list the repository: {res.stderr}")
        return None

    archives = []
    for entry in json.loads(res.stdout).get("archives", []):
        name = entry.get("name")
        raw_time = entry.get("start") or entry.get("time")
        if not name or not raw_time:
            continue
        try:
            # Naive local time, which is the same clock borg's own prune uses
            # to compute period buckets. See core/retention.py.
            archives.append(Archive(name=name, ts=datetime.fromisoformat(raw_time)))
        except ValueError:
            logger.warning(f"Skipping archive with unparseable timestamp: {name} {raw_time!r}")
    archives.sort(key=lambda a: a.ts, reverse=True)
    return archives


def plan_deletions(archives: list, retention: dict, now=None) -> tuple:
    """Decide what to delete across the whole fleet in one pass.

    Returns (names_to_delete, per_node_report). Archives are matched to a node
    by the `{hostname}-` prefix the backup task gives them.

    Two archives are deliberately spared regardless of policy:

    * anything whose hostname does not match a current node — a renamed or
      deleted node's history is not this task's to throw away, and guessing
      wrong is unrecoverable;
    * every node's most recent archive, as a backstop. Borg's rules already
      keep it under any non-empty policy, so this only fires if the policy
      resolved to something unexpected.
    """
    from core.retention import select

    by_node = {}
    unclaimed = []
    for archive in archives:
        for hostname in retention:
            if archive.name.startswith(f"{hostname}-"):
                by_node.setdefault(hostname, []).append(archive)
                break
        else:
            unclaimed.append(archive.name)

    if unclaimed:
        logger.info(
            f"{len(unclaimed)} archive(s) belong to no current node and are left alone."
        )

    to_delete = []
    report = {}
    for hostname, node_archives in by_node.items():
        keep, delete, _ = select(node_archives, retention[hostname], now=now)

        newest = max(node_archives, key=lambda a: a.ts)
        delete = [a for a in delete if a.name != newest.name]

        to_delete.extend(a.name for a in delete)
        report[hostname] = {"kept": len(node_archives) - len(delete), "deleted": len(delete)}

    return to_delete, report


def _reconcile_history_with_repo(active_archives: set) -> int:
    """Drop history rows whose archive is no longer in the repository."""
    with session_scope() as db:
        stale = [
            row for row in db.query(BackupHistory).filter(BackupHistory.status == "SUCCESS").all()
            if row.archive_name not in active_archives
        ]
        for row in stale:
            logger.info(f"Removing stale database backup history record: {row.archive_name}")
            db.delete(row)
        return len(stale)


#: Batch size for `borg delete`. One invocation could take every name, but a
#: fleet-wide argv of 100k archive names hits the kernel's ARG_MAX, and a
#: failure part-way through a single huge call tells you nothing about what
#: survived. Batches keep the argv sane and make a partial failure legible.
DELETE_BATCH = int(os.getenv("BORG_DELETE_BATCH", "200"))

#: Set to skip the deletion itself and only log what would have gone. Worth one
#: night on a real fleet before trusting the retention port in core/retention.py.
PRUNE_DRY_RUN = os.getenv("BORG_PRUNE_DRY_RUN", "").lower() in ("1", "true", "yes")


def _run_global_daily_prune(fix_repo_permissions) -> Dict[str, Any]:
    """Prune every shard, then reconcile history against all of them at once.

    Each shard is an independent repository with its own lock, so they are
    pruned under separate maintenance flags — pruning one must not stand down
    backups bound for another, which is the whole reason for sharding.

    Reconciliation deliberately happens once, after every shard has been
    listed: it deletes history rows whose archive is absent, so handing it one
    shard's archives would delete the history of every node in the others.
    A shard that could not be listed suppresses it entirely rather than
    reconciling against a partial view.
    """
    results: Dict[str, Any] = {"deleted": 0, "nodes": {}, "shards": {}}
    surviving: set = set()
    listing_complete = True

    for repo_path in repo_paths.all_shard_paths():
        if not repo_paths.is_initialized(repo_path):
            # Shards past 0 do not exist until the first node assigned to one
            # runs `borg init` on its first backup. Nothing to prune is not an
            # error, and must not fail the rest of the nightly run.
            results["shards"][repo_path] = "SKIPPED: not initialized"
            continue

        shard_result, remaining = _prune_one_shard(repo_path, fix_repo_permissions)
        results["shards"][repo_path] = shard_result
        results["deleted"] += shard_result.get("deleted", 0)
        results["nodes"].update(shard_result.get("nodes", {}))

        if remaining is None:
            listing_complete = False
        else:
            surviving |= remaining

    if listing_complete and not PRUNE_DRY_RUN:
        try:
            logger.info("Synchronizing backup history database records with active archives...")
            stale = _reconcile_history_with_repo(surviving)
            logger.info(f"Database history sync completed. Removed {stale} stale records.")
        except Exception as e:
            logger.error(f"Exception during backup history DB sync: {str(e)}")
    elif not listing_complete:
        logger.warning(
            "Skipping history reconciliation: at least one shard could not be listed, "
            "and reconciling against a partial view would delete live history."
        )

    return results


def _prune_one_shard(repo_path: str, fix_repo_permissions) -> tuple:
    """Prune one repository in three borg invocations: list, delete, compact.

    It used to be one `borg prune --prefix <host>` per node. Each took the
    repository's exclusive lock and re-read a manifest holding every archive of
    every node, so the cost was quadratic in fleet size — hours at 2000 nodes,
    starting at 03:00 and still running when the backup windows opened, with no
    backup able to run for the whole of it because they need the same lock.

    Deciding in Python (core/retention.py, a port of borg's own algorithm)
    makes it one list, batched deletes, one compact.

    Returns (results, surviving_archive_names). The names are None if the
    repository could not be listed, which the caller must not mistake for "no
    archives survived".
    """
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    with repository_maintenance(owner="global_daily_prune", repo_path=repo_path) as heartbeat:
        if heartbeat is None:
            # Another prune already holds this repository. Two of these against
            # one repository is exactly what the flag exists to prevent, and
            # skipping costs nothing — the next nightly run picks it up.
            logger.warning(f"Maintenance already in progress on {repo_path}; skipping its prune.")
            return {"status": "SKIPPED", "reason": "maintenance already in progress"}, None

        results: Dict[str, Any] = {"deleted": 0, "nodes": {}, "compact": "PENDING"}

        archives = _list_archives(repo_path, env)
        if archives is None:
            return {"error": "Could not list the repository"}, None

        retention = _retention_by_hostname()
        to_delete, report = plan_deletions(archives, retention)
        results["nodes"] = report

        if PRUNE_DRY_RUN:
            logger.warning(
                f"BORG_PRUNE_DRY_RUN is set: {len(to_delete)} archive(s) in {repo_path} "
                f"would be deleted, nothing was. {to_delete}"
            )
            return (
                {"status": "DRY_RUN", "would_delete": len(to_delete), "nodes": report},
                {a.name for a in archives},
            )

        deleted = 0
        for index in range(0, len(to_delete), DELETE_BATCH):
            batch = to_delete[index:index + DELETE_BATCH]
            heartbeat()
            try:
                res = subprocess.run(
                    ["borg", "delete", "--lock-wait", str(LOCK_WAIT_SECONDS), repo_path, *batch],
                    env=env, capture_output=True, text=True,
                    **borg_kwargs(repo_path, env),
                )
                if res.returncode == 0:
                    deleted += len(batch)
                else:
                    logger.error(f"Borg delete failed for a batch of {len(batch)}: {res.stderr}")
                    results.setdefault("errors", []).append(res.stderr.strip())
            except Exception as e:
                logger.error(f"Exception deleting a batch of {len(batch)}: {e}")
                results.setdefault("errors", []).append(str(e))
        results["deleted"] = deleted
        logger.info(f"Pruned {deleted} archive(s) across {len(report)} node(s).")

        # Compaction reclaims the segments the deletes freed. Inside the
        # maintenance flag: it takes the same exclusive lock.
        heartbeat()
        try:
            logger.info("Starting Borg repository compaction after daily prunes...")
            res_compact = subprocess.run(
                ["borg", "compact", "--lock-wait", str(LOCK_WAIT_SECONDS), repo_path],
                env=env, capture_output=True, text=True, **borg_kwargs(repo_path, env),
            )
            if res_compact.returncode == 0:
                logger.info("Successfully compacted Borg repository.")
                results["compact"] = "SUCCESS"
            else:
                logger.error(f"Failed to compact Borg repository: {res_compact.stderr}")
                results["compact"] = f"FAILED: {res_compact.stderr}"
        except Exception as e:
            logger.error(f"Exception compacting Borg repository: {str(e)}")
            results["compact"] = f"ERROR: {str(e)}"

        # What actually survived. Re-listed rather than derived from
        # `to_delete`, so a delete that silently failed does not cost the
        # caller a history row for an archive still present.
        heartbeat()
        remaining = _list_archives(repo_path, env)
        surviving = None if remaining is None else {a.name for a in remaining}

    fix_repo_permissions(repo_path)
    return results, surviving
