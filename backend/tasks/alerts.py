"""Hourly sweep: evaluate every alert source, sync against open alerts,
dispatch notifications for whatever changed.
"""
from typing import Any, Dict

from sqlalchemy.orm import Session

from celery_app import celery_app
import tasks
from core.alerts import sync as sync_alerts
from core.alert_sources import SOURCES
from core.notify import dispatch

LOCK_KEY = "alert_eval_lock"
LOCK_TTL_SECONDS = 300


@celery_app.task(name="tasks.evaluate_alerts_task", ignore_result=True)
def evaluate_alerts_task() -> Dict[str, Any]:
    if tasks.redis_client.exists(LOCK_KEY):
        return {"status": "SKIPPED", "reason": "previous sweep still running"}
    tasks.redis_client.setex(LOCK_KEY, LOCK_TTL_SECONDS, "1")

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
        try:
            tasks.redis_client.delete(LOCK_KEY)
        except Exception:
            pass
