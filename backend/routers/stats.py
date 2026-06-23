from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models

from routers.users import require_admin

router = APIRouter(prefix="/api/stats", dependencies=[Depends(require_admin)])

@router.get("")
def get_global_stats(db: Session = Depends(get_db)):
    """
    Retrieves global metrics including storage dedup ratios.
    """
    histories = db.query(models.BackupHistory).filter(models.BackupHistory.status == "SUCCESS").all()
    total_original = sum(h.original_size for h in histories)
    total_deduplicated = sum(h.deduplicated_size for h in histories)
    
    ratio = 1.0
    if total_deduplicated > 0:
        ratio = round(total_original / total_deduplicated, 2)

    return {
        "total_nodes": db.query(models.Node).count(),
        "total_original_size_bytes": total_original,
        "total_deduplicated_size_bytes": total_deduplicated,
        "deduplication_ratio": ratio
    }
