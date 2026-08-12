"""Alert list/acknowledge and per-user Telegram delivery preferences."""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

import models
import schemas
from core.notify import telegram
from database import get_db, log_user_action
from routers.users import get_current_auth, require_admin

router = APIRouter(prefix="/api", tags=["Notifications"])


def _alert_response(db: Session, alert: models.Alert) -> schemas.AlertResponse:
    node = (
        db.query(models.Node).filter(models.Node.id == alert.node_id).first()
        if alert.node_id else None
    )
    ack_user = (
        db.query(models.User).filter(models.User.id == alert.acknowledged_by_id).first()
        if alert.acknowledged_by_id else None
    )
    return schemas.AlertResponse(
        id=alert.id, module=alert.module, node_id=alert.node_id,
        node_hostname=node.hostname if node else None,
        dedup_key=alert.dedup_key, severity=alert.severity, status=alert.status,
        title=alert.title, detail=alert.detail,
        first_seen=alert.first_seen, last_seen=alert.last_seen,
        resolved_at=alert.resolved_at, acknowledged_at=alert.acknowledged_at,
        acknowledged_by=ack_user.username if ack_user else None,
    )


@router.get("/alerts", response_model=List[schemas.AlertResponse])
def list_alerts(
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = None,
    node_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_auth=Depends(require_admin),
):
    q = db.query(models.Alert)
    if status_filter:
        q = q.filter(models.Alert.status == status_filter)
    if severity:
        q = q.filter(models.Alert.severity == severity)
    if node_id:
        q = q.filter(models.Alert.node_id == node_id)
    rows = q.order_by(models.Alert.last_seen.desc()).all()
    return [_alert_response(db, row) for row in rows]


@router.post("/alerts/{alert_id}/acknowledge", response_model=schemas.AlertResponse)
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_auth=Depends(require_admin),
):
    alert = db.query(models.Alert).filter(models.Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    if alert.status == "RESOLVED":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Alert is already resolved.")

    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by_id = getattr(current_auth, "id", None)
    db.commit()
    log_user_action(
        db, getattr(current_auth, "username", "unknown"), "Alert Acknowledged",
        f"Acknowledged alert #{alert.id}: {alert.title}", None,
    )
    return _alert_response(db, alert)


@router.get("/notifications/preferences", response_model=schemas.NotificationPreferences)
def get_notification_preferences(current_auth=Depends(get_current_auth)):
    prefs = getattr(current_auth, "notification_prefs", None) or {}
    defaults = schemas.NotificationPreferences().model_dump()
    return schemas.NotificationPreferences(**{**defaults, **prefs})


@router.post("/notifications/preferences", response_model=schemas.NotificationPreferences)
def set_notification_preferences(
    payload: schemas.NotificationPreferences,
    db: Session = Depends(get_db),
    current_auth=Depends(get_current_auth),
):
    user = db.query(models.User).filter(models.User.id == getattr(current_auth, "id", None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Preferences belong to a user account.")
    user.notification_prefs = payload.model_dump()
    db.commit()
    return payload


@router.post("/notifications/test", response_model=schemas.NotificationTestResult)
def send_test_notification(
    db: Session = Depends(get_db),
    current_auth=Depends(get_current_auth),
):
    user = db.query(models.User).filter(models.User.id == getattr(current_auth, "id", None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a user account.")

    probe_alert = models.Alert(
        module="test", severity="WATCH", status="OPEN",
        title="Test notification from edge-bro",
    )
    ok, detail = telegram.send(user, probe_alert, "opened")
    return schemas.NotificationTestResult(success=ok, detail=detail)


@router.get("/notifications/status", response_model=schemas.NotificationStatus)
def get_notification_status():
    return schemas.NotificationStatus(telegram_configured=bool(os.getenv("TELEGRAM_BOT_TOKEN")))
