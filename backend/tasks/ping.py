import asyncio
from datetime import datetime
from typing import Dict, Any
from sqlalchemy.orm import Session
from models import Node
from celery_app import celery_app
import tasks

async def async_ping_ip(ip: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "2", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False

async def ping_all_async(ips: list[str]) -> list[bool]:
    tasks_list = [tasks.async_ping_ip(ip) for ip in ips]
    return await asyncio.gather(*tasks_list)

@celery_app.task(name="tasks.ping_all_nodes_task")
def ping_all_nodes_task() -> Dict[str, Any]:
    """
    Periodic task running every 30 seconds to ping all nodes
    and update their availability status.
    """
    db: Session = tasks.SessionLocal()
    try:
        nodes = db.query(Node).all()
        if not nodes:
            return {"status": "SUCCESS"}
            
        ips = [n.ip_address for n in nodes]
        results = asyncio.run(ping_all_async(ips))
            
        for node, is_online in zip(nodes, results):
            node.last_ping_status = is_online
            if is_online:
                node.last_available_at = datetime.utcnow()
        db.commit()
        return {"status": "SUCCESS"}
    except Exception as e:
        tasks.logger.error(f"Error in ping_all_nodes_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
