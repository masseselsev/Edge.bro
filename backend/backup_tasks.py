import os
import subprocess
import json
import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional
from celery_app import celery_app

from models import Node, TaskLog, BackupHistory, Settings, BackupGroup
from ansible_utils import run_ansible_playbook
from core.borg_local import borg_kwargs
from core.db_session import session_scope
from core import ssh
from core import backup_stats, transfer_speed
from core.repo_lock import maintenance_in_progress, repository_maintenance
from core.task_log import log_to_task

# Re-use logging configuration from tasks
logger = logging.getLogger(__name__)

#: Borg holds the repository lock for the whole of `borg create`, not a brief
#: critical section, so two backups landing on one repository contend for the
#: entire duration of the first. Borg's own default wait is one second, which
#: turns that contention into an outright failure for the loser. Wait instead:
#: a backup that queues behind another finishes late, a backup that errors out
#: has to be retried from scratch. Bounded so a wedged lock cannot hold a
#: Celery worker forever.
LOCK_WAIT_SECONDS = int(os.getenv("BORG_LOCK_WAIT_SECONDS", "600"))


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


def resolve_borg_target(
    orchestrator_behind_nat: bool,
    direct_ip: Optional[str],
    borg_ssh_port: int,
    repo_path: str = "/data/borg/fleet",
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


def build_borg_create_cmd(
    node_ip: str,
    node_ssh_port: int,
    borg_repo_url: str,
    archive_name: str,
    exclude_str: str,
    compression: str,
    rate_limit_kib: int,
    checkpoint_secs: int,
    cpu_quota: Optional[int],
    borg_passphrase: str,
    extra_ssh_args: Optional[list] = None,
) -> list:
    """
    Builds the SSH command list to run borg create on the node,
    optionally wrapped in systemd-run --scope for CPU limiting.
    SSH Compression=no because Borg already compresses data chunks.
    """
    borg_env = (
        f"BORG_RSH='{ssh.borg_rsh()}' BORG_PASSPHRASE='{borg_passphrase}' "
        f"BORG_RELOCATED_REPO_ACCESS_IS_OK=yes"
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
        inner_cmd = (
            f"systemd-run --scope "
            f"-p CPUQuota={cpu_quota}% "
            f"-- bash -c \"{borg_env} {borg_create}\""
        )
    else:
        inner_cmd = f"bash -c \"{borg_env} {borg_create}\""

    # No connect timeout: a link slow enough to take a minute to hand-shake is
    # still a link this backup should run over.
    return ssh.command(
        node_ip, node_ssh_port, inner_cmd,
        connect_timeout=None,
        keepalive=True,
        extra_args=extra_ssh_args,
    )


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
    Cleans up stale Borg locks on the node and server, and resolves the correct
    orchestrator IP to use by verifying configured IP reachability or falling back
    to the incoming SSH connection IP.

    When orchestrator_behind_nat is True, direct reachability is known to be
    impossible (that's the whole point of the flag), so the reachability probe
    is skipped — it would only waste a timeout and log a misleading "unreachable,
    falling back" message. Lock cleanup still runs. The return value is None in
    that case; callers must use resolve_borg_target() for the repo URL instead.
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
        f"pkill -f '[b]org create' || true; "
        f"find /root/.cache/borg -name 'lock*' -delete 2>/dev/null; "
        f"find /root/.cache/borg -mindepth 1 -maxdepth 1 -type d -exec sh -c '[ ! -s \"$1/config\" ] && rm -rf \"$1\" && echo \"Removed corrupt borg cache: $1\"' _ {{}} \\; 2>/dev/null; "
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
            log_to_task(task_id, "[Lock cleanup] Killed orphaned borg processes on node (if any).")
            log_to_task(task_id, "[Lock cleanup] Cleared Borg cache locks on node.")
        else:
            log_to_task(task_id, f"[Lock cleanup] WARNING: Pre-backup check failed: {res.stderr.strip()}")
    except Exception as e:
        log_to_task(task_id, f"[Lock cleanup] WARNING: Pre-backup check exception: {e}")

    # Break the repository lock only if nothing is legitimately holding it.
    #
    # This used to be unconditional, and the lock it took away was as likely to
    # belong to the running nightly prune as to a dead worker. Breaking a live
    # lock does not queue the backup behind the prune — it lets both write to
    # the same segments and manifest at once, which is repository corruption,
    # not contention. See core/repo_lock.py.
    owner = maintenance_in_progress()
    if owner:
        log_to_task(
            task_id,
            "[Lock cleanup] Repository maintenance is in progress; leaving the "
            "repo lock alone. This backup will be retried on the next tick.",
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

    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

    plan = _plan_backup(node_id)
    if plan is None:
        return {"status": "FAILED", "error": "Node not found"}

    redis_client.setex(
        f"backup_running:{plan.node_id}", plan.lock_ttl, f"{int(time.time())}:{task_id}"
    )

    try:
        # Check Sentinel HASP license for READY nodes
        if plan.status == "READY":
            refusal = _refuse_unlicensed_node(plan, task_id, redis_client, log_to_task)
            if refusal is not None:
                return refusal

        with session_scope() as db:
            db.add(TaskLog(
                id=task_id, task_type="BACKUP", status="RUNNING",
                node_id=node_id, log_output="",
            ))

        return _transfer_and_record(plan, task_id, comment, log_to_task, fix_repo_permissions)
    except Exception as e:
        log_to_task(task_id, f"Exception occurred during backup task: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
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
    lock_ttl: int


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
            cpu_quota=(
                group.cpu_quota
                if group and group.cpu_quota is not None
                else getattr(settings, 'default_cpu_quota', None)
            ),
            # Sized from this node's history rather than a flat 4h: on slow
            # links a backup that outlives its lock gets killed by the next
            # scheduler tick. Sized from the limit that will actually apply,
            # otherwise a node capped slower than its group outlives its own
            # lock and gets killed.
            lock_ttl=backup_lock_ttl_seconds(db, node.id, rate_limit_kib or None),
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
        db.add(TaskLog(
            id=task_id, task_type="BACKUP", status="FAILED",
            node_id=plan.node_id, log_output="",
        ))
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
        repo_path="/data/borg/fleet",
        borg_passphrase=os.getenv("BORG_PASSPHRASE", ""),
        configured_ip=plan.orchestrator_ip,
        borg_ssh_port=plan.borg_ssh_port,
        orchestrator_behind_nat=plan.behind_nat,
    )

    archive_name = f"{plan.hostname}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    extra_ssh_args, borg_repo_url = resolve_borg_target(
        orchestrator_behind_nat=plan.behind_nat,
        direct_ip=orchestrator_ip,
        borg_ssh_port=plan.borg_ssh_port,
    )

    fix_repo_permissions("/data/borg/fleet")

    init_cmd = ssh.command(
        plan.ip_address, plan.ssh_port,
        # Compression on: `borg init` writes a tiny repo config, not chunks.
        f"BORG_RSH='{ssh.borg_rsh(compression=True)}' "
        f"BORG_PASSPHRASE='{os.getenv('BORG_PASSPHRASE')}' "
        f"BORG_RELOCATED_REPO_ACCESS_IS_OK=yes "
        f"borg init --lock-wait {LOCK_WAIT_SECONDS} "
        f"--encryption=repokey {borg_repo_url}",
        connect_timeout=None,
        keepalive=True,
        extra_args=extra_ssh_args,
    )
    log_to_task(task_id, "Checking/Initializing Borg repository...")
    try:
        res_init = subprocess.run(init_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_init.returncode not in (0, 2):
            log_to_task(task_id, f"WARNING: Repository initialization status: {res_init.stderr.strip()}")
    except Exception as e:
        log_to_task(task_id, f"Repository initialization check warning: {str(e)}")

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

    log_to_task(task_id, (
        f"Resource limits — compression: {plan.compression}, "
        f"upload rate: {rate_text}, "
        f"checkpoint: {plan.checkpoint_secs}s, "
        f"cpu_quota: {plan.cpu_quota}%"
    ))

    ssh_cmd = build_borg_create_cmd(
        node_ip=plan.ip_address,
        node_ssh_port=plan.ssh_port,
        borg_repo_url=borg_repo_url,
        archive_name=archive_name,
        exclude_str=exclude_str,
        compression=plan.compression,
        rate_limit_kib=rate_limit_kib,
        checkpoint_secs=plan.checkpoint_secs,
        cpu_quota=plan.cpu_quota,
        borg_passphrase=os.getenv('BORG_PASSPHRASE', ''),
        extra_ssh_args=extra_ssh_args,
    )

    log_to_task(task_id, f"Running remote command on node: {' '.join(ssh_cmd[:6])} [COMMAND MASKED]")

    # Locks have been cleaned up and IP resolved at the start of the task

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

    for raw_line in process.stderr:
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
    borg_duration = None
    try:
        data = json.loads(stdout)
        archive_stats = data.get("archive", {}).get("stats", {})
        original_size = archive_stats.get("original_size", 0)
        deduplicated_size = archive_stats.get("deduplicated_size", 0)
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
            status="SUCCESS",
            log_output=stdout + "\n" + stderr,
            comment=comment,
            avg_speed_mbps=avg_mbps,
            max_speed_mbps=max_mbps,
            duration_seconds=duration,
        ))
        node = db.query(Node).filter(Node.id == plan.node_id).first()
        if node:
            node.last_backup = datetime.utcnow()

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
        ["borg", "list", "--json", repo_path],
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
    """Prune the whole fleet in three borg invocations: list, delete, compact.

    It used to be one `borg prune --prefix <host>` per node. Each took the
    repository's exclusive lock and re-read a manifest holding every archive of
    every node, so the cost was quadratic in fleet size — hours at 2000 nodes,
    starting at 03:00 and still running when the backup windows opened, with no
    backup able to run for the whole of it because they need the same lock.

    Deciding in Python (core/retention.py, a port of borg's own algorithm)
    makes it one list, batched deletes, one compact.
    """
    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        return {"error": "Repository path not found"}

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    with repository_maintenance(owner="global_daily_prune") as heartbeat:
        if heartbeat is None:
            # Another prune already holds the repository. Two of these against
            # one repository is exactly what the flag exists to prevent, and
            # skipping costs nothing — the next nightly run picks it up.
            logger.warning("Repository maintenance already in progress; skipping this prune.")
            return {"status": "SKIPPED", "reason": "maintenance already in progress"}

        results: Dict[str, Any] = {"deleted": 0, "nodes": {}, "compact": "PENDING"}

        archives = _list_archives(repo_path, env)
        if archives is None:
            return {"error": "Could not list the repository"}

        retention = _retention_by_hostname()
        to_delete, report = plan_deletions(archives, retention)
        results["nodes"] = report

        if PRUNE_DRY_RUN:
            logger.warning(
                f"BORG_PRUNE_DRY_RUN is set: {len(to_delete)} archive(s) would be "
                f"deleted, nothing was. {to_delete}"
            )
            return {"status": "DRY_RUN", "would_delete": len(to_delete), "nodes": report}

        deleted = 0
        for index in range(0, len(to_delete), DELETE_BATCH):
            batch = to_delete[index:index + DELETE_BATCH]
            heartbeat()
            try:
                res = subprocess.run(
                    ["borg", "delete", repo_path, *batch],
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
                ["borg", "compact", repo_path],
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

        # Reconcile database history with what is actually left. Re-listed
        # rather than derived from `to_delete`, so a delete that silently
        # failed does not remove a history row for an archive still present.
        heartbeat()
        try:
            logger.info("Synchronizing backup history database records with active archives...")
            remaining = _list_archives(repo_path, env)
            if remaining is not None:
                stale = _reconcile_history_with_repo({a.name for a in remaining})
                logger.info(f"Database history sync completed. Removed {stale} stale records.")
        except Exception as e:
            logger.error(f"Exception during backup history DB sync: {str(e)}")

    fix_repo_permissions(repo_path)
    return results
