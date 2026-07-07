from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

from routers.users import require_admin

router = APIRouter(prefix="/api/stats", dependencies=[Depends(require_admin)])

@router.get("")
def get_global_stats(db: Session = Depends(get_db)):
    """
    Retrieves global metrics including storage dedup ratios for initial backups of each node.
    """
    histories = db.query(models.BackupHistory).filter(models.BackupHistory.status == "SUCCESS").all()
    
    # Filter to only keep the oldest (initial) successful backup for each unique node
    node_initial_backups = {}
    for h in histories:
        if h.node_id not in node_initial_backups:
            node_initial_backups[h.node_id] = h
        else:
            current_best = node_initial_backups[h.node_id]
            if h.timestamp < current_best.timestamp:
                node_initial_backups[h.node_id] = h
            elif h.timestamp == current_best.timestamp and h.id < current_best.id:
                node_initial_backups[h.node_id] = h

    initial_backups = list(node_initial_backups.values())
    total_original = sum(h.original_size for h in initial_backups)
    total_deduplicated = sum(h.deduplicated_size for h in initial_backups)
    
    ratio = 1.0
    if total_deduplicated > 0:
        ratio = round(total_original / total_deduplicated, 2)

    return {
        "total_nodes": db.query(models.Node).count(),
        "total_original_size_bytes": total_original,
        "total_deduplicated_size_bytes": total_deduplicated,
        "deduplication_ratio": ratio
    }
