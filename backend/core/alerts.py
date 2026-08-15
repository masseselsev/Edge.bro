"""The generic alert state machine. No knowledge of SMART, thermal, or any
other producer — a future alert source only ever needs to produce
AlertCandidate objects and call sync() the same way these do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Set

from sqlalchemy.orm import Session

import models
from core.clock import utcnow

_SEVERITY_RANK = {"WATCH": 0, "ALERT": 1}


@dataclass
class AlertCandidate:
    module: str
    node_id: Optional[int]
    dedup_key: str
    severity: str
    title: str
    detail: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    opened: List["models.Alert"] = field(default_factory=list)
    reopened: List["models.Alert"] = field(default_factory=list)
    resolved: List["models.Alert"] = field(default_factory=list)


def sync(
    db: Session,
    candidates: List[AlertCandidate],
    modules: Optional[Set[str]] = None,
) -> SyncResult:
    """Reconcile the current candidate list against open alerts.

    `modules`, if given, is the set of module names that were actually
    evaluated this sweep. An open alert whose `module` is not in that set is
    left untouched even if it has no matching candidate — that module simply
    didn't run this cycle (e.g. it raised), so its silence says nothing about
    whether the underlying problem is still real. When `modules` is None
    (the default), every leftover open alert is resolved, matching the
    original all-or-nothing behavior; callers that only evaluate a subset of
    sources should always pass the set of modules that actually succeeded.

    One precondition on the caller, not enforced here:
    - `dedup_key` must be unique *within* one candidate list. Both shipped
      sources guarantee this by construction (smart.py groups by device in
      SQL; thermal.py emits at most one candidate per node). A source that
      violated it would hit the database's partial unique index on its
      second insert for the same key — a hard failure, not a silent one.
    """
    now = utcnow()
    result = SyncResult()

    open_rows = (
        db.query(models.Alert).filter(models.Alert.status != "RESOLVED").all()
    )
    open_by_key = {row.dedup_key: row for row in open_rows}

    for candidate in candidates:
        existing = open_by_key.pop(candidate.dedup_key, None)
        if existing is None:
            new_alert = models.Alert(
                module=candidate.module,
                node_id=candidate.node_id,
                dedup_key=candidate.dedup_key,
                severity=candidate.severity,
                status="OPEN",
                title=candidate.title,
                detail=candidate.detail,
                first_seen=now,
                last_seen=now,
            )
            db.add(new_alert)
            result.opened.append(new_alert)
            continue

        existing.last_seen = now
        existing.title = candidate.title
        existing.detail = candidate.detail

        escalated = _SEVERITY_RANK[candidate.severity] > _SEVERITY_RANK[existing.severity]
        if existing.status == "ACKNOWLEDGED" and escalated:
            existing.status = "OPEN"
            existing.severity = candidate.severity
            existing.acknowledged_at = None
            existing.acknowledged_by_id = None
            result.reopened.append(existing)
        elif escalated:
            existing.severity = candidate.severity

    # Whatever is left in open_by_key had no matching candidate this sweep.
    for leftover in open_by_key.values():
        if modules is not None and leftover.module not in modules:
            continue  # that module didn't run this sweep; leave its alerts alone
        leftover.status = "RESOLVED"
        leftover.resolved_at = now
        result.resolved.append(leftover)

    db.commit()
    return result
