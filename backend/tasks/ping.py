"""Fleet reachability sweep.

Runs every 30 seconds against every node, so everything here is written for
the 2000-node case rather than the ten-node one: the number of concurrent
subprocesses is capped, only the columns that actually changed are written
back, and the whole thing is loaded as tuples rather than ORM objects.
"""
import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

from sqlalchemy import case
from sqlalchemy.orm import Session

import tasks
from celery_app import celery_app
from models import Node

#: How many `ping` processes may exist at once.
#:
#: Without a cap this sweep forks one process per node simultaneously — 2000
#: of them every 30 seconds, each living up to the 2s timeout. That exhausts
#: PIDs and file descriptors long before it finishes. 64 keeps a 2000-node
#: sweep to roughly 32 batches; at 2s worst case per batch that is well inside
#: the 30s budget, and in practice almost every ping returns in milliseconds.
PING_CONCURRENCY = int(os.getenv("PING_CONCURRENCY", "64"))

#: Per-ping wall clock. Matches the `-W` flag handed to ping itself; the
#: asyncio timeout is a backstop for a process that ignores it.
PING_TIMEOUT_SECONDS = 2


async def async_ping_ip(ip: str) -> bool:
    """One ICMP probe. Never raises — an unreachable node is not an error."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # The backstop is deliberately longer than ping's own -W: we want ping
        # to time out and exit on its own wherever possible, because killing
        # it here leaves a zombie until the loop reaps it.
        await asyncio.wait_for(proc.wait(), timeout=PING_TIMEOUT_SECONDS + 3)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return False
    except Exception:
        return False


async def ping_all_async(ips: List[str]) -> List[bool]:
    """Probe every address, at most PING_CONCURRENCY at a time."""
    semaphore = asyncio.Semaphore(PING_CONCURRENCY)

    async def bounded(ip: str) -> bool:
        async with semaphore:
            # Indirected through the `tasks` package so existing tests that
            # patch tasks.async_ping_ip keep working.
            return await tasks.async_ping_ip(ip)

    return await asyncio.gather(*(bounded(ip) for ip in ips))


@celery_app.task(name="tasks.ping_all_nodes_task")
def ping_all_nodes_task() -> Dict[str, Any]:
    """Refresh every node's reachability flag.

    Only nodes whose status actually changed are written. The previous version
    reassigned `last_ping_status` on every node every 30 seconds, which made
    the ORM flush 2000 UPDATEs per sweep — 4000 row versions a minute of pure
    churn for a fleet that is mostly up and mostly unchanged.
    """
    db: Session = tasks.SessionLocal()
    try:
        # Tuples, not ORM objects: this is a bulk read and nothing here needs
        # a mapped instance or its identity-map overhead.
        rows: List[Tuple[int, str, bool]] = db.query(
            Node.id, Node.ip_address, Node.last_ping_status
        ).all()
        if not rows:
            return {"status": "SUCCESS", "checked": 0, "changed": 0}

        results = asyncio.run(ping_all_async([r[1] for r in rows]))
        now = datetime.utcnow()

        changed_online: List[int] = []
        changed_offline: List[int] = []
        still_online: List[int] = []

        # `was_online` is None for a node that has never been probed, which is
        # distinct from False and must still produce a write — otherwise a node
        # that is offline the first time it is ever seen keeps a NULL status
        # forever, and the UI cannot tell "down" from "never checked".
        for (node_id, _ip, was_online), is_online in zip(rows, results):
            if is_online:
                if was_online is True:
                    still_online.append(node_id)
                else:
                    changed_online.append(node_id)
            elif was_online is not False:
                changed_offline.append(node_id)

        # Nodes that came up: flip the flag and stamp availability.
        if changed_online:
            db.query(Node).filter(Node.id.in_(changed_online)).update(
                {Node.last_ping_status: True, Node.last_available_at: now},
                synchronize_session=False,
            )
        # Nodes that went down: flip the flag, leave last_available_at as the
        # last time they were actually reachable.
        if changed_offline:
            db.query(Node).filter(Node.id.in_(changed_offline)).update(
                {Node.last_ping_status: False},
                synchronize_session=False,
            )
        # Nodes that were up and stayed up still need their heartbeat moved
        # forward, or "last available" would freeze at the moment they first
        # came online. This is the one unavoidable bulk write.
        if still_online:
            db.query(Node).filter(Node.id.in_(still_online)).update(
                {Node.last_available_at: now},
                synchronize_session=False,
            )

        db.commit()
        return {
            "status": "SUCCESS",
            "checked": len(rows),
            "changed": len(changed_online) + len(changed_offline),
        }
    except Exception as e:
        db.rollback()
        tasks.logger.error(f"Error in ping_all_nodes_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
