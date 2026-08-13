"""Node health: SMART scores, thermal verdicts and the series behind them.

Read-only apart from the threshold overrides and the UI preference store.
Everything analytical is done in `core.*`; this module fetches rows and shapes
them, and takes care that a node with nothing to report says why rather than
returning an empty object the UI would have to guess about.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

import models
import schemas
from core import smart, monitoring_verdicts
from database import get_db, log_user_action
from routers.users import get_current_auth, require_admin, require_user
from tasks.monitoring import resolve_setting

router = APIRouter(prefix="/api/monitoring", tags=["Monitoring"])

#: Defaults for the monitoring graphs, used until a user changes them. Stored
#: per user rather than per browser so the same choices follow the person.
DEFAULT_UI_PREFERENCES = {
    "smart_graph_series": ["score", "temperature_c", "percent_used"],
    "thermal_graph_series": ["theta_c_per_w", "t_ambient_c"],
    "telemetry_graph_series": ["cpu_temp_c_mean", "power_w_mean"],
    "graph_days": 90,
}


def _latest_smart(db: Session, node_id: int) -> List[models.SmartSnapshot]:
    """The most recent snapshot per device, which is what the badge reflects."""
    rows = (
        db.query(models.SmartSnapshot)
        .filter(models.SmartSnapshot.node_id == node_id)
        .order_by(models.SmartSnapshot.captured_at.desc())
        .limit(50)
        .all()
    )
    latest: Dict[str, models.SmartSnapshot] = {}
    for row in rows:
        latest.setdefault(row.device, row)
    return list(latest.values())


def _smart_response(db: Session, node_id: int, snapshot: models.SmartSnapshot):
    # The wear projection needs the device's own history, not the fleet's.
    history = (
        db.query(models.SmartSnapshot.captured_at, models.SmartSnapshot.percent_used,
                 models.SmartSnapshot.written_bytes)
        .filter(models.SmartSnapshot.node_id == node_id,
                models.SmartSnapshot.device == snapshot.device)
        .order_by(models.SmartSnapshot.captured_at.asc())
        .all()
    )
    projection = smart.project_wear([(r.captured_at, r.percent_used) for r in history])
    write_rate = smart.bytes_per_day([(r.captured_at, r.written_bytes) for r in history])

    return schemas.SmartHealthResponse(
        captured_at=snapshot.captured_at,
        device=snapshot.device,
        protocol=snapshot.protocol,
        model=snapshot.model,
        serial=snapshot.serial,
        firmware=snapshot.firmware,
        health_passed=snapshot.health_passed,
        temperature_c=snapshot.temperature_c,
        power_on_hours=snapshot.power_on_hours,
        written_bytes=snapshot.written_bytes,
        percent_used=snapshot.percent_used,
        score=snapshot.score,
        grade=snapshot.grade,
        subscores=[schemas.SmartSubScore(**s) for s in (snapshot.subscores or [])],
        overrides=list(snapshot.overrides or []),
        advisories=list(snapshot.advisories or []),
        projected_date=projection.projected_date,
        days_remaining=projection.days_remaining,
        percent_used_per_day=projection.percent_used_per_day,
        bytes_per_day=write_rate,
        observation_days=projection.observation_days,
        observation_points=projection.observation_points,
        projection_unavailable_reason=projection.unavailable_reason,
    )


@router.get("/nodes/{node_id}", response_model=schemas.NodeHealthResponse)
def get_node_health(node_id: int, db: Session = Depends(get_db),
                    current_user=Depends(require_admin)):
    """Everything the node cards need: drive scores and the thermal verdict."""
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    settings = db.query(models.Settings).first()
    now = datetime.utcnow()

    return schemas.NodeHealthResponse(
        node_id=node.id,
        hostname=node.hostname,
        last_harvest_at=node.last_harvest_at,
        monitoring_enabled=bool(resolve_setting(node, settings, "monitoring_enabled", True)),
        capabilities=node.monitoring_capabilities,
        smart=[_smart_response(db, node.id, s) for s in _latest_smart(db, node.id)],
        thermal=schemas.ThermalHealthResponse(
            **vars(monitoring_verdicts.thermal_verdict(db, node, now))
        ),
    )


@router.get("/nodes/{node_id}/smart-history", response_model=List[schemas.SmartHistoryPoint])
def get_smart_history(
    node_id: int,
    days: int = Query(default=90, ge=1, le=3650),
    device: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Every SMART reading in the window, for the detail graph."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(models.SmartSnapshot)
        .filter(models.SmartSnapshot.node_id == node_id,
                models.SmartSnapshot.captured_at >= cutoff)
    )
    if device:
        query = query.filter(models.SmartSnapshot.device == device)
    return query.order_by(models.SmartSnapshot.captured_at.asc()).all()


@router.get("/nodes/{node_id}/smart-latest")
def get_latest_smart_report(
    node_id: int,
    device: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """The complete smartctl report behind the latest reading.

    Returned raw and unshaped: this is the "full statistics of the last query"
    view, and reformatting it would only hide fields somebody opened it to
    read. Older snapshots have their raw report cleared by retention, so this
    can legitimately answer that none is stored.
    """
    query = (
        db.query(models.SmartSnapshot)
        .filter(models.SmartSnapshot.node_id == node_id,
                models.SmartSnapshot.raw.isnot(None))
    )
    if device:
        query = query.filter(models.SmartSnapshot.device == device)
    snapshot = query.order_by(models.SmartSnapshot.captured_at.desc()).first()

    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No stored SMART report for this node. Reports older than the "
                   "retention window keep their scores but not their full text.",
        )
    return {
        "captured_at": snapshot.captured_at,
        "device": snapshot.device,
        "report": snapshot.raw,
    }


@router.get("/nodes/{node_id}/thermal-history", response_model=List[schemas.ThermalHistoryPoint])
def get_thermal_history(
    node_id: int,
    days: int = Query(default=90, ge=1, le=3650),
    include_rejected: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(models.ThermalFit)
        .filter(models.ThermalFit.node_id == node_id,
                models.ThermalFit.window_start >= cutoff)
    )
    if not include_rejected:
        query = query.filter(models.ThermalFit.rejection == "OK")
    return query.order_by(models.ThermalFit.window_start.asc()).all()


@router.get("/nodes/{node_id}/telemetry", response_model=List[schemas.TelemetryPoint])
def get_telemetry(
    node_id: int,
    days: int = Query(default=7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(models.TelemetryRollup)
        .filter(models.TelemetryRollup.node_id == node_id,
                models.TelemetryRollup.bucket_start >= cutoff)
        .order_by(models.TelemetryRollup.bucket_start.asc())
        .all()
    )


@router.get("/nodes/{node_id}/thresholds", response_model=schemas.MonitoringThresholds)
def get_node_thresholds(node_id: int, db: Session = Depends(get_db),
                        current_user=Depends(require_admin)):
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    settings = db.query(models.Settings).first()

    fields = ("monitoring_enabled", "monitoring_interval_days",
              "smart_temp_warn_c", "smart_temp_crit_c")
    fallbacks = {"monitoring_enabled": True, "monitoring_interval_days": 30,
                 "smart_temp_warn_c": smart.DEFAULT_TEMP_WARN_C,
                 "smart_temp_crit_c": smart.DEFAULT_TEMP_CRIT_C}

    return schemas.MonitoringThresholds(
        **{f: resolve_setting(node, settings, f, fallbacks[f]) for f in fields},
        overridden=[f for f in fields if getattr(node, f, None) is not None],
    )


@router.post("/nodes/{node_id}/thresholds", response_model=schemas.MonitoringThresholds)
def set_node_thresholds(
    node_id: int,
    payload: schemas.NodeMonitoringUpdate,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Set or clear this node's overrides. Null clears one back to inherited."""
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    warn = payload.smart_temp_warn_c
    crit = payload.smart_temp_crit_c
    if warn is not None and crit is not None and warn >= crit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The warning temperature must be below the critical one.",
        )

    # Every field is written, including the Nones: sending null is how the UI
    # clears an override back to inherited, so absent and null cannot be
    # collapsed into the same thing here.
    for field, value in payload.model_dump().items():
        setattr(node, field, value)
    db.commit()

    log_user_action(
        db, current_user.username, "Update Node Monitoring",
        f"Monitoring overrides for '{node.hostname}': {payload.model_dump()}", request,
    )
    return get_node_thresholds(node_id, db=db, current_user=current_user)


@router.post("/nodes/{node_id}/harvest")
def trigger_harvest(node_id: int, request: Request = None, db: Session = Depends(get_db),
                    current_user=Depends(require_admin)):
    """Harvest one node now, outside its schedule."""
    node = db.query(models.Node).filter(models.Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    from tasks.monitoring import harvest_node_task
    task = harvest_node_task.apply_async(args=[node_id], retry=False)

    log_user_action(db, current_user.username, "Manual Harvest",
                    f"Triggered a monitoring harvest for '{node.hostname}'", request)
    return {"message": f"Harvest of '{node.hostname}' started.", "task_id": task.id}


# --- per-user UI state --------------------------------------------------------

@router.get("/preferences", response_model=schemas.UiPreferencesResponse)
def get_preferences(current_user: models.User = Depends(require_user)):
    """Graph choices for the current user, falling back to the defaults.

    A user who has never chosen gets the defaults rather than an empty object,
    so the frontend never has to carry a second copy of them.
    """
    stored = current_user.ui_preferences or {}
    return schemas.UiPreferencesResponse(preferences={**DEFAULT_UI_PREFERENCES, **stored})


@router.post("/preferences", response_model=schemas.UiPreferencesResponse)
def set_preferences(
    payload: schemas.UiPreferencesResponse,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_user),
):
    """Merge into the current user's stored preferences.

    Merged rather than replaced so a client that knows about one graph does
    not wipe the settings of another it has never heard of.

    Depends on require_user, not get_current_auth: this used to re-query a
    User by `current_auth.id`, and kiosk ids come from a separate sequence
    that can collide with user ids, so a kiosk token could rewrite an
    unrelated user's preferences.
    """
    user = current_user
    merged = {**(user.ui_preferences or {}), **(payload.preferences or {})}
    user.ui_preferences = merged
    db.commit()
    return schemas.UiPreferencesResponse(preferences={**DEFAULT_UI_PREFERENCES, **merged})
