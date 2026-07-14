import os
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from routers.users import require_admin

router = APIRouter(prefix="/api/health", dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)

@router.get("")
def get_system_health(db: Session = Depends(get_db)):
    warnings = []
    try:
        root_dev = os.stat('/').st_dev
        
        # Check Borg repository volume
        borg_path = "/data/borg"
        if os.path.exists(borg_path) and os.stat(borg_path).st_dev == root_dev:
            warnings.append({
                "code": "BORG_ON_ROOT",
                "type": "WARNING",
                "message": "Borg backup repository storage (/data/borg) resides on the system root partition instead of an external drive/volume."
            })
            
        # Check Client ISO Cache volume
        iso_path = "/opt/data/iso_cache"
        if os.path.exists(iso_path) and os.stat(iso_path).st_dev == root_dev:
            warnings.append({
                "code": "ISO_CACHE_ON_ROOT",
                "type": "WARNING",
                "message": "Client ISO cache storage (/opt/data/iso_cache) resides on the system root partition instead of an external drive/volume."
            })
    except Exception as e:
        logger.error(f"System health check failed: {str(e)}")
        
    return {"warnings": warnings}
