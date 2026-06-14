import os
import subprocess
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from celery import shared_task

from database import SessionLocal
from models import Node, TaskLog, BackupHistory, Settings
from ansible_utils import run_ansible_playbook

# Re-use logging configuration from tasks
logger = logging.getLogger(__name__)


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
) -> list:
    """
    Builds the SSH command list to run borg create on the node,
    optionally wrapped in systemd-run --scope for CPU limiting.
    SSH Compression=no because Borg already compresses data chunks.
    """
    interval = int(os.getenv("SSH_KEEPALIVE_INTERVAL", "30"))
    count = int(os.getenv("SSH_KEEPALIVE_COUNT", "3"))

    borg_rsh = (
        "ssh -i /home/borg/.ssh/id_ed25519 "
        "-o StrictHostKeyChecking=no -o Compression=no "
        f"-o ServerAliveInterval={interval} -o ServerAliveCountMax={count}"
    )
    borg_env = f"BORG_RSH='{borg_rsh}' BORG_PASSPHRASE='{borg_passphrase}'"
    borg_create = (
        f"borg create --json --stats "
        f"--compression {compression} "
        f"--checkpoint-interval {checkpoint_secs} "
        f"--remote-ratelimit {rate_limit_kib} "
        f"{borg_repo_url}::{archive_name} / {exclude_str}"
    )

    if cpu_quota and cpu_quota > 0:
        inner_cmd = (
            f"systemd-run --scope "
            f"-p CPUQuota={cpu_quota}% "
            f"-p IOSchedulingClass=idle "
            f"-- bash -c \"{borg_env} {borg_create}\""
        )
    else:
        inner_cmd = f"bash -c \"{borg_env} {borg_create}\""

    return [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ServerAliveInterval={interval}",
        "-o", f"ServerAliveCountMax={count}",
        "-p", str(node_ssh_port),
        "-i", "/root/.ssh/id_ed25519",
        f"root@{node_ip}",
        inner_cmd,
    ]




@shared_task(bind=True)
def run_prepare_task(self, node_id: int) -> Dict[str, Any]:
    """
    Celery task to run the Auto-Prepare disk labels playbook on the node.

    Args:
        node_id: ID of the Node database record.

    Returns:
        Status result dictionary.
    """
    task_id = self.request.id
    from tasks import log_to_task
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    task_log = TaskLog(id=task_id, task_type="PREPARE", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    log_to_task(task_id, f"Starting auto-prepare for {node.hostname} ({node.ip_address})")

    res = run_ansible_playbook(
        task_id=task_id,
        playbook_name="prepare.yml",
        host_ip=node.ip_address,
        ssh_port=node.ssh_port,
        extra_vars={},
        ssh_key_path="/root/.ssh/id_ed25519"
    )

    if res["status"] == "SUCCESS":
        node.disk_type = res["parsed_data"].get("disk_type", "UNKNOWN")
        node.network_iface = res["parsed_data"].get("network_iface")
        node.efi_uuid = res["parsed_data"].get("efi_uuid")
        if "partition_layout" in res["parsed_data"]:
            node.partition_layout = res["parsed_data"]["partition_layout"]
        if "os_version" in res["parsed_data"]:
            node.os_version = res["parsed_data"]["os_version"]
        if "hostname" in res["parsed_data"]:
            node.hostname = res["parsed_data"]["hostname"]
        node.cpu_info = res["parsed_data"].get("cpu_info")
        node.memory_info = res["parsed_data"].get("memory_info")
        node.edge_version = res["parsed_data"].get("edge_version")
        node.status = "READY"
        db.commit()
        log_to_task(task_id, f"Auto-prepare finished. Disk type: {node.disk_type}, EFI UUID: {node.efi_uuid}, Interface: {node.network_iface}, CPU: {node.cpu_info}, RAM: {node.memory_info}, Edge Version: {node.edge_version}", status="SUCCESS")
    else:
        node.status = "NEEDS_FIX"
        db.commit()
        log_to_task(task_id, "Auto-prepare task failed.", status="FAILED")

    db.close()
    return res


@shared_task(bind=True)
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
    from tasks import log_to_task, fix_repo_permissions
    db: Session = SessionLocal()
    node = db.query(Node).filter(Node.id == node_id).first()
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings()
        db.add(settings)
        db.commit()

    if not node:
        db.close()
        return {"status": "FAILED", "error": "Node not found"}

    import redis
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    redis_client.setex(f"backup_running:{node.id}", 14400, "1")

    task_log = TaskLog(id=task_id, task_type="BACKUP", status="RUNNING", log_output="")
    db.add(task_log)
    db.commit()

    log_to_task(task_id, f"Initiating Borg backup for {node.hostname}...")

    orchestrator_ip = settings.orchestrator_ip or os.getenv("ORCHESTRATOR_IP")
    if not orchestrator_ip:
        try:
            route_cmd = f"ip route get {node.ip_address}"
            route_out = subprocess.check_output(route_cmd, shell=True, text=True)
            orchestrator_ip = route_out.split("src")[1].split()[0]
        except Exception:
            orchestrator_ip = "127.0.0.1"

    archive_name = f"{node.hostname}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    borg_repo_url = f"ssh://borg@{orchestrator_ip}:{settings.borg_ssh_port}/data/borg/fleet"

    fix_repo_permissions("/data/borg/fleet")

    interval = int(os.getenv("SSH_KEEPALIVE_INTERVAL", "30"))
    count = int(os.getenv("SSH_KEEPALIVE_COUNT", "3"))

    init_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ServerAliveInterval={interval}",
        "-o", f"ServerAliveCountMax={count}",
        "-p", str(node.ssh_port),
        "-i", "/root/.ssh/id_ed25519",
        f"root@{node.ip_address}",
        f"BORG_RSH='ssh -i /home/borg/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o ServerAliveInterval={interval} -o ServerAliveCountMax={count}' BORG_PASSPHRASE='{os.getenv('BORG_PASSPHRASE')}' borg init --encryption=repokey {borg_repo_url}"
    ]
    log_to_task(task_id, "Checking/Initializing Borg repository...")
    try:
        res_init = subprocess.run(init_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res_init.returncode not in (0, 2):
            log_to_task(task_id, f"WARNING: Repository initialization status: {res_init.stderr.strip()}")
    except Exception as e:
        log_to_task(task_id, f"Repository initialization check warning: {str(e)}")

    exclude_args = []
    if settings.global_exclusions:
        for x in settings.global_exclusions.split(","):
            ex = x.strip()
            if ex:
                exclude_args.append(f"--exclude '{ex}'")
    exclude_str = " ".join(exclude_args)

    # --- Resolve resource settings (group -> global -> hardcoded fallback) ---
    group = None
    if node.group_id:
        from models import BackupGroup
        group = db.query(BackupGroup).filter(BackupGroup.id == node.group_id).first()

    compression = (
        (group.compression if group and group.compression else None)
        or getattr(settings, 'default_compression', None)
        or 'zstd:3'
    )
    rate_limit_kib = (
        group.upload_rate_limit
        if group and group.upload_rate_limit is not None
        else 0
    )
    checkpoint_secs = (
        group.checkpoint_interval
        if group and group.checkpoint_interval is not None
        else compute_checkpoint_interval(rate_limit_kib)
    )
    cpu_quota = (
        group.cpu_quota
        if group and group.cpu_quota is not None
        else getattr(settings, 'default_cpu_quota', None)
    )

    log_to_task(task_id, (
        f"Resource limits — compression: {compression}, "
        f"rate: {rate_limit_kib} KiB/s, "
        f"checkpoint: {checkpoint_secs}s, "
        f"cpu_quota: {cpu_quota}%"
    ))

    ssh_cmd = build_borg_create_cmd(
        node_ip=node.ip_address,
        node_ssh_port=node.ssh_port,
        borg_repo_url=borg_repo_url,
        archive_name=archive_name,
        exclude_str=exclude_str,
        compression=compression,
        rate_limit_kib=rate_limit_kib,
        checkpoint_secs=checkpoint_secs,
        cpu_quota=cpu_quota,
        borg_passphrase=os.getenv('BORG_PASSPHRASE', ''),
    )

    log_to_task(task_id, f"Running remote command on node: {' '.join(ssh_cmd[:6])} [COMMAND MASKED]")


    try:
        process = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()

        log_to_task(task_id, f"Remote execution stdout:\n{stdout}")
        if stderr:
            log_to_task(task_id, f"Remote execution stderr:\n{stderr}")

        if process.returncode in (0, 1):
            if process.returncode == 1:
                log_to_task(task_id, "WARNING: Backup completed with warnings (some files changed during backup or were skipped).")
            original_size = 0
            deduplicated_size = 0
            try:
                data = json.loads(stdout)
                archive_stats = data.get("archive", {}).get("stats", {})
                original_size = archive_stats.get("original_size", 0)
                deduplicated_size = archive_stats.get("deduplicated_size", 0)
            except Exception:
                log_to_task(task_id, "Failed to parse JSON directly; estimating size metrics.")

            history = BackupHistory(
                node_id=node.id,
                archive_name=archive_name,
                original_size=original_size,
                deduplicated_size=deduplicated_size,
                status="SUCCESS",
                log_output=stdout + "\n" + stderr,
                comment=comment
            )
            db.add(history)
            node.last_backup = datetime.utcnow()
            db.commit()

            log_to_task(task_id, "Backup completed successfully.", status="SUCCESS")
            return {"status": "SUCCESS", "archive": archive_name}
        else:
            history = BackupHistory(
                node_id=node.id,
                archive_name=archive_name,
                original_size=0,
                deduplicated_size=0,
                status="FAILED",
                log_output=stdout + "\n" + stderr,
                comment=comment
            )
            db.add(history)
            db.commit()
            log_to_task(task_id, "Backup execution failed.", status="FAILED")
            return {"status": "FAILED", "error": stderr}
    except Exception as e:
        log_to_task(task_id, f"Exception occurred during backup task: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        try:
            redis_client.delete(f"backup_running:{node.id}")
        except Exception:
            pass
        db.close()


@shared_task
def global_daily_prune() -> Dict[str, Any]:
    """
    Celery scheduled cron task running at 3:00 AM daily.
    Executes borg prune on a per-node basis using resolved retention policies,
    then compacts the Borg repository.
    """
    from models import BackupGroup
    from tasks import fix_repo_permissions
    db: Session = SessionLocal()
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings()

    nodes = db.query(Node).all()
    results = {"prunes": {}, "compact": "PENDING"}

    repo_path = "/data/borg/fleet"
    if not os.path.exists(repo_path):
        db.close()
        return {"error": "Repository path not found"}

    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")

    # Pre-fetch groups
    groups = {g.id: g for g in db.query(BackupGroup).all()}

    for node in nodes:
        # Resolve policy
        policy = None
        group = groups.get(node.group_id) if node.group_id else None
        
        if group and group.override_retention and group.retention_policy:
            policy = group.retention_policy
        elif settings.retention_policy:
            policy = settings.retention_policy

        # Build command parameters
        prune_cmd = ["borg", "prune", "--prefix", f"{node.hostname}-"]

        if policy:
            p_type = policy.get("type", "interval")
            if p_type == "interval":
                prune_cmd.extend([
                    "--keep-daily", str(policy.get("keep_daily", 7)),
                    "--keep-weekly", str(policy.get("keep_weekly", 4)),
                    "--keep-monthly", str(policy.get("keep_monthly", 6))
                ])
            elif p_type == "count":
                prune_cmd.extend(["--keep-last", str(policy.get("keep_last", 5))])
            elif p_type == "timeframe":
                val = policy.get("within_value", 3)
                unit = policy.get("within_unit", "m")
                prune_cmd.extend([
                    "--keep-last", "1",
                    "--keep-within", f"{val}{unit}"
                ])
        else:
            # Fallback to legacy settings flat columns
            prune_cmd.extend([
                "--keep-daily", str(settings.keep_daily),
                "--keep-weekly", str(settings.keep_weekly),
                "--keep-monthly", str(settings.keep_monthly)
            ])

        prune_cmd.append(repo_path)

        try:
            logger.info(f"Executing Borg prune for node {node.hostname}...")
            res_prune = subprocess.run(prune_cmd, env=env, capture_output=True, text=True)
            if res_prune.returncode == 0:
                results["prunes"][node.hostname] = "SUCCESS"
            else:
                logger.error(f"Borg prune failed for node {node.hostname}: {res_prune.stderr}")
                results["prunes"][node.hostname] = f"FAILED: {res_prune.stderr}"
        except Exception as e:
            logger.error(f"Exception pruning node {node.hostname}: {str(e)}")
            results["prunes"][node.hostname] = f"ERROR: {str(e)}"

    # Compaction
    try:
        logger.info("Starting Borg repository compaction after daily prunes...")
        compact_cmd = ["borg", "compact", repo_path]
        res_compact = subprocess.run(compact_cmd, env=env, capture_output=True, text=True)
        if res_compact.returncode == 0:
            logger.info("Successfully compacted Borg repository.")
            results["compact"] = "SUCCESS"
        else:
            logger.error(f"Failed to compact Borg repository: {res_compact.stderr}")
            results["compact"] = f"FAILED: {res_compact.stderr}"
    except Exception as e:
        logger.error(f"Exception compacting Borg repository: {str(e)}")
        results["compact"] = f"ERROR: {str(e)}"

    fix_repo_permissions(repo_path)
    db.close()
    return results
