from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from celery_app import celery_app
from core.db_session import session_scope
from models import Node
import tasks


@dataclass
class _LicenseProbe:
    """One RESTORED node's identity, detached from the session.

    The probe itself is an SSH round trip with an 8s timeout, and the tick runs
    every minute — so the fields come out of the database first and the network
    work happens with no connection held. See core.db_session.
    """
    id: int
    hostname: str
    ssh_port: int
    ip_address: str
    hasp_runtime_version: Optional[str]


def _restored_nodes() -> List[_LicenseProbe]:
    with session_scope() as db:
        return [
            _LicenseProbe(
                id=node.id,
                hostname=node.hostname,
                ssh_port=node.ssh_port,
                ip_address=node.ip_address,
                hasp_runtime_version=node.hasp_runtime_version,
            )
            for node in db.query(Node).filter(Node.status == "RESTORED").all()
        ]


def _promote_to_ready(probe: _LicenseProbe) -> None:
    from database import log_user_action

    with session_scope() as db:
        node = db.query(Node).filter(Node.id == probe.id).first()
        if not node or node.status != "RESTORED":
            return
        node.status = "READY"
        db.flush()
        log_user_action(
            db, "System: License Monitor", "Node Status Promoted",
            f"Restored node '{probe.hostname}' detected with active license. "
            f"Status promoted to READY.", None,
        )


@celery_app.task(name="tasks.scheduler_tick")
def scheduler_tick() -> Dict[str, Any]:
    """
    Periodic task running every minute to evaluate node scheduling rules
    and trigger automated backups within defined group windows.
    """
    from core.scheduler import check_and_trigger_backups
    from core.hasp_helper import check_hasp_status_on_node

    try:
        # Check RESTORED nodes license status once a minute
        for probe in _restored_nodes():
            lock_key = f"license_lock:{probe.id}"
            if tasks.redis_client.exists(lock_key):
                continue
            tasks.redis_client.setex(lock_key, 15, "1")
            try:
                if check_hasp_status_on_node(probe) == "active":
                    _promote_to_ready(probe)
            finally:
                try:
                    tasks.redis_client.delete(lock_key)
                except Exception:
                    pass

        with session_scope() as db:
            check_and_trigger_backups(db)
        return {"status": "SUCCESS"}
    except Exception as e:
        tasks.logger.error(f"Error in scheduler_tick: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
