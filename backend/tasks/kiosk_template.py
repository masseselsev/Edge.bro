"""Periodic check that the USB-Kiosk template still matches its sources.

The check belongs on a timer rather than at startup, where it used to live.
`payload_hash` covers `/opt/frontend_build`, a volume the frontend container
refills from its own image every time it starts, and compose orders the
frontend strictly after the backend reports healthy. The startup hook
therefore ran while the volume still held the *previous* release's dashboard
bundle and pronounced the sources unchanged, seconds before the new bundle
replaced it. Nothing looked again, so the template stayed a release behind
while the interface showed it as outdated.

Running it on the periodic queue also picks up the cases no single shot could:
a build that failed, a broker that was unreachable at the wrong second, or a
bundle replaced by hand.
"""
from typing import Any, Dict

from sqlalchemy.orm import Session

from celery_app import celery_app
import tasks


@celery_app.task(name="tasks.kiosk_template_check_task")
def kiosk_template_check_task() -> Dict[str, Any]:
    """Rebuild the USB-Kiosk template if its sources have moved on.

    Cheap when there is nothing to do - a hash of the payload directory and a
    string comparison - and it dispatches the build to the ISO queue rather
    than running it here, so a ten-minute xorriso run never occupies the
    periodic worker.
    """
    db: Session = tasks.SessionLocal()
    try:
        from iso_tasks import rebuild_kiosk_template_if_stale

        reason = rebuild_kiosk_template_if_stale(db)
        return {"status": "SUCCESS", "reason": reason}
    except Exception as e:
        tasks.logger.error(f"Error in kiosk_template_check_task: {str(e)}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        db.close()
