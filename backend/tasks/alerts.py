"""Hourly sweep: evaluate every alert source, sync against open alerts,
dispatch notifications for whatever changed.
"""
import os
import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

import tasks
from celery_app import celery_app
from core.alert_sources import SOURCES
from core.alerts import sync as sync_alerts
from core.notify import dispatch

LOCK_KEY = "alert_eval_lock"

#: Ceiling on how long one sweep may hold the lock.
#:
#: This must exceed the worst-case sweep duration, not the typical one. It was
#: 300s, which a large fleet outgrew — and an expired lock does not skip the
#: next run, it lets a second sweep start alongside the first, each holding a
#: connection and a fleet's worth of rows. An hour is longer than any healthy
#: sweep and still guarantees the lock cannot outlive a dead worker for more
#: than one beat interval.
LOCK_TTL_SECONDS = int(os.getenv("ALERT_LOCK_TTL_SECONDS", "3600"))


@celery_app.task(name="tasks.evaluate_alerts_task", ignore_result=True)
def evaluate_alerts_task() -> Dict[str, Any]:
    # SET NX EX is atomic: check-then-set was a race, and two beats firing
    # close together could both observe "no lock" and both proceed.
    token = uuid.uuid4().hex
    try:
        acquired = tasks.redis_client.set(
            LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS
        )
    except Exception:
        tasks.logger.exception("Could not reach Redis to take the alert lock")
        return {"status": "FAILED", "reason": "lock unavailable"}

    if not acquired:
        return {"status": "SKIPPED", "reason": "previous sweep still running"}

    db: Session = tasks.SessionLocal()
    try:
        candidates = []
        succeeded_modules = set()
        for module_name, evaluate_fn in SOURCES.items():
            try:
                candidates.extend(evaluate_fn(db))
                succeeded_modules.add(module_name)
            except Exception:
                tasks.logger.exception(
                    "Alert source '%s' raised during evaluation; skipping it this sweep",
                    module_name,
                )

        result = sync_alerts(db, candidates, modules=succeeded_modules)
        dispatch.notify(db, result)
        return {
            "status": "SUCCESS",
            "opened": len(result.opened),
            "reopened": len(result.reopened),
            "resolved": len(result.resolved),
        }
    except Exception:
        tasks.logger.exception("evaluate_alerts_task failed")
        return {"status": "FAILED"}
    finally:
        db.close()
        _release_lock(token)


def _release_lock(token: str) -> None:
    """Delete the lock only if we still hold it.

    A plain DELETE releases whatever lock is present, including one a *later*
    sweep took after ours expired — which turns one overrun into an unbounded
    chain of overlapping sweeps. Compare-and-delete has to be atomic, hence
    the Lua script.
    """
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
        tasks.redis_client.eval(script, 1, LOCK_KEY, token)
    except Exception:
        tasks.logger.warning("Could not release the alert lock cleanly", exc_info=True)
