"""Discarding backup history records.

Separate from the read-side history endpoints in `nodes_crud` because deleting
is a different kind of operation with a different audience: only an admin may
do it, and every call is written to the audit log.

Scope is deliberately narrow. Only failed records can be dropped here. A
successful archive is real data somebody may need to restore from, and removing
it belongs to retention or to the existing per-node purge — not to a stray
click on a statistics page. Failed records, on the other hand, are noise:
controlled test runs and known outages that skew every reliability number on
the page until they are cleared.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import models
import schemas
from core import archive_cleanup, repo_paths
from database import get_db, log_user_action
from auth import require_admin
from routers.deps import node_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nodes/history", tags=["History"])

#: Refuse a fleet-wide purge larger than this without a node filter. A test
#: cleanup is tens of rows; thousands means the request was not what its author
#: thought it was.
_BULK_SAFETY_LIMIT = 5000


def _log_action(db: Session, username: str, action: str, details: str, request: Optional[Request]) -> None:
    """Record the deletion. A broken audit write must not undo a completed one."""
    try:
        log_user_action(db, username, action, details, request)
    except Exception:
        logger.exception("Could not write audit entry for %s", action)


@router.post("/purge-failed", response_model=schemas.PurgeFailedResponse)
def purge_failed(
    payload: schemas.PurgeFailedRequest,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Drop failed history records, optionally limited to one node or a date.

    A POST rather than a DELETE with a body: the filters are the point of the
    call, and bodies on DELETE are poorly supported by proxies.
    """
    query = db.query(models.BackupHistory).filter(models.BackupHistory.status != "SUCCESS")

    node = None
    if payload.node_id is not None:
        node = node_or_404(db, payload.node_id)
        query = query.filter(models.BackupHistory.node_id == payload.node_id)

    if payload.before is not None:
        query = query.filter(models.BackupHistory.timestamp < payload.before)

    records = query.all()
    if not records:
        return schemas.PurgeFailedResponse(deleted=0, checkpoints_removed=0)

    if len(records) > _BULK_SAFETY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{len(records)} records matched, which is more than the {_BULK_SAFETY_LIMIT} "
                f"this endpoint will remove at once. Narrow the request with a node or a date."
            ),
        )

    removed = _remove_leftovers(db, records)

    for record in records:
        db.delete(record)
    db.commit()

    scope = f"node '{node.hostname}'" if node else "all nodes"
    if payload.before:
        scope += f" before {payload.before.isoformat()}"
    _log_action(
        db,
        current_user.username,
        "Purge Failed Backups",
        f"Removed {len(records)} failed backup record(s) for {scope}; "
        f"{removed} leftover archive(s) deleted from the repository",
        request,
    )

    return schemas.PurgeFailedResponse(deleted=len(records), checkpoints_removed=removed)


@router.delete("/{history_id}", response_model=schemas.PurgeFailedResponse)
def delete_history_record(
    history_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Drop a single failed history record."""
    record = db.query(models.BackupHistory).filter(models.BackupHistory.id == history_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup history record not found.")

    if record.status == "SUCCESS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Successful archives cannot be deleted here — they hold restorable data. "
                "Use retention, or purge the node's archives."
            ),
        )

    archive_name = record.archive_name
    removed = _remove_leftovers(db, [record])

    db.delete(record)
    db.commit()

    _log_action(
        db,
        current_user.username,
        "Delete Failed Backup",
        f"Removed failed backup record '{archive_name}'"
        + (f"; {removed} leftover archive(s) deleted from the repository" if removed else ""),
        request,
    )

    return schemas.PurgeFailedResponse(deleted=1, checkpoints_removed=removed)


def _remove_leftovers(db, records: list) -> int:
    """Delete anything these failed runs left in their repositories.

    Usually nothing: a backup that fails before writing has no archive at all.
    Each repository is listed once and only actual matches are deleted, so the
    common case costs one read per repository and no lock. Any failure here is
    logged and swallowed — the record still goes, because leaving it in place
    would mean the operator cannot clear a failure they can see.

    Grouped by repository rather than run per record: a bulk purge spanning
    several nodes can span several shards, and listing one repository tells us
    nothing about archives in another.
    """
    by_repo: dict[str, list] = {}
    for record in records:
        node = db.query(models.Node).filter(models.Node.id == record.node_id).first()
        repo = repo_paths.repo_path_for_node(node) if node else repo_paths.shard_path(0)
        by_repo.setdefault(repo, []).append(record.archive_name)

    removed = 0
    for repo, archive_names in by_repo.items():
        try:
            present = archive_cleanup.list_repo_archives(repo)
        except Exception:
            logger.exception("Could not list archives in %s before deleting history", repo)
            continue

        if not present:
            continue

        doomed = []
        for name in archive_names:
            doomed.extend(archive_cleanup.matching_archives(present, name))

        if not doomed:
            continue

        try:
            removed += archive_cleanup.delete_archives(doomed, repo)
        except Exception:
            logger.exception("Could not delete leftover archives %s from %s", doomed, repo)

    return removed
