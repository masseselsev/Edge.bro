from typing import Dict, Any
from sqlalchemy.orm import Session
from models import Node
from celery_app import celery_app
import tasks

@celery_app.task(name="tasks.scheduler_tick")
def scheduler_tick() -> Dict[str, Any]:
    """
    Periodic task running every minute to evaluate node scheduling rules
    and trigger automated backups within defined group windows.
    """
    from core.scheduler import check_and_trigger_backups
    db: Session = tasks.SessionLocal()
    try:
        # Check RESTORED nodes license status once a minute
        from core.hasp_helper import check_hasp_status_on_node
        from database import log_user_action
        
        restored_nodes = db.query(Node).filter(Node.status == "RESTORED").all()
        for node in restored_nodes:
            lock_key = f"license_lock:{node.id}"
            if tasks.redis_client.exists(lock_key):
                continue
            tasks.redis_client.setex(lock_key, 15, "1")
            try:
                hasp_status = check_hasp_status_on_node(node)
                if hasp_status == "active":
                    node.status = "READY"
                    db.commit()
                    log_user_action(db, "System: License Monitor", "Node Status Promoted", f"Restored node '{node.hostname}' detected with active license. Status promoted to READY.", None)
            finally:
                try:
                    tasks.redis_client.delete(lock_key)
                except Exception:
                    pass

        check_and_trigger_backups(db)
        return {"status": "SUCCESS"}
    except Exception as e:
        tasks.logger.error(f"Error in scheduler_tick: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
