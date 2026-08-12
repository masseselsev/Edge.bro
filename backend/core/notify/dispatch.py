"""Fan-out from a sync result to subscribed users. One recipient's failure
never blocks the rest — logged and moved on, the same non-fatal-per-item
pattern used by deploy_monitoring() in tasks/bootstrap.py.
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy.orm import Session

import models
from core.alerts import SyncResult
from core.notify import telegram

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"WATCH": 0, "ALERT": 1}


def _subscribed_users(db: Session, min_alert_severity: str) -> List["models.User"]:
    result = []
    for user in db.query(models.User).all():
        prefs = user.notification_prefs or {}
        if not prefs.get("telegram_enabled"):
            continue
        threshold = prefs.get("min_severity", "WATCH")
        if _SEVERITY_RANK.get(min_alert_severity, 0) >= _SEVERITY_RANK.get(threshold, 0):
            result.append(user)
    return result


def _telegram_subscribers(db: Session) -> List["models.User"]:
    return [
        u for u in db.query(models.User).all()
        if (u.notification_prefs or {}).get("telegram_enabled")
    ]


def _send_to(users: List["models.User"], alert: "models.Alert", kind: str) -> None:
    for user in users:
        try:
            ok, detail = telegram.send(user, alert, kind)
            if not ok:
                logger.warning(
                    "Notification delivery failed: user=%s alert=%s kind=%s detail=%s",
                    user.id, alert.id, kind, detail,
                )
        except Exception:
            logger.exception(
                "Notification delivery raised: user=%s alert=%s kind=%s", user.id, alert.id, kind
            )


def notify(db: Session, result: SyncResult) -> None:
    for alert in result.opened:
        _send_to(_subscribed_users(db, alert.severity), alert, "opened")
    for alert in result.reopened:
        _send_to(_subscribed_users(db, alert.severity), alert, "reopened")
    for alert in result.resolved:
        # Resolutions bypass the severity gate: the point is telling whoever
        # was told about the problem that it is over, not re-filtering by a
        # severity the alert may no longer even carry meaningfully.
        _send_to(_telegram_subscribers(db), alert, "resolved")
