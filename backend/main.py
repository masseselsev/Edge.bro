import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import get_db
import models
from version import VERSION
from tasks import ensure_orchestrator_ssh_key

# Import routers
from routers import settings as settings_router
from routers import nodes as nodes_router
from routers import tasks as tasks_router
from routers import restore as restore_router
from routers import stats as stats_router
from routers import iso as iso_router
from routers import network as network_router
from routers import groups as groups_router
from routers import kiosks as kiosks_router

app = FastAPI(title="Edge B.R.O. API", version=VERSION)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_db_init():
    """
    Ensure settings are initialized in the database on startup and orchestrator SSH keys are ready.
    """
    try:
        from database import setup_db_logging
        setup_db_logging()
    except Exception as e:
        print(f"Error setting up database logging on startup: {str(e)}")

    try:
        ensure_orchestrator_ssh_key()
    except Exception as e:
        print(f"Error ensuring SSH keypair on startup: {str(e)}")

    # Ensure permissions of the shared borg storage are correct from day one
    try:
        from tasks import fix_repo_permissions
        fix_repo_permissions("/data/borg/fleet")
    except Exception as e:
        print(f"Error ensuring repository permissions on startup: {str(e)}")

    # Clear any stale download lock file and temp files from a previous crash/reboot
    try:
        lock_path = "/opt/data/iso_cache/download.lock"
        if os.path.exists(lock_path):
            os.remove(lock_path)
            print("Cleared stale download lock on startup.")
        tmp_iso_path = "/opt/data/iso_cache/base.iso.tmp"
        if os.path.exists(tmp_iso_path):
            os.remove(tmp_iso_path)
            print("Cleared stale temporary base ISO file on startup.")
    except Exception as e:
        print(f"Error clearing stale download lock/files on startup: {str(e)}")

    db = next(get_db())
    upgrade_settings(db)
    db.close()


def upgrade_settings(db: Session):
    """
    Upgrade old default exclusions to the new default if unchanged by user.
    """
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
    else:
        old_defaults = [
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*',
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1'
        ]
        new_default = (
            '/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,'
            '/var/log/edge/*,/var/opt/edge/blobstore/*,/var/spool/edge/*,/var/log/journal/*,'
            '/var/log/**/*.gz,/var/log/**/*.1'
        )
        if settings.global_exclusions in old_defaults:
            settings.global_exclusions = new_default
            db.commit()


# Include routers
app.include_router(settings_router.router)
app.include_router(nodes_router.router)
app.include_router(tasks_router.router)
app.include_router(restore_router.router)
app.include_router(stats_router.router)
app.include_router(iso_router.router, prefix="/api/iso", tags=["Client ISO"])
app.include_router(network_router.router, prefix="/api")
app.include_router(groups_router.router)
app.include_router(kiosks_router.router)
