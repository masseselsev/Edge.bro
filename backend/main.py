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
from routers import users as users_router

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

    # Clear any stale download lock file on startup. If a download was in progress, auto-resume it.
    try:
        lock_path = "/opt/data/iso_cache/download.lock"
        tmp_iso_path = "/opt/data/iso_cache/base.iso.tmp"
        base_iso_path = "/opt/data/iso_cache/base.iso"
        
        # Clear lock first to reset any stale status
        if os.path.exists(lock_path):
            os.remove(lock_path)
            print("Cleared stale download lock on startup.")
            
        # Check if we should auto-resume the download
        if not os.path.exists(base_iso_path) and os.path.exists(tmp_iso_path):
            print("Found partial base ISO download, scheduling automatic resume...")
            # Recreate download.lock so UI shows it as downloading
            with open(lock_path, "w") as f:
                f.write("LOCKED")
            
            from iso_tasks import download_base_iso_task
            download_base_iso_task.delay()
            print("Triggered base ISO download task on startup.")
    except Exception as e:
        print(f"Error managing base ISO download resume on startup: {str(e)}")

    db = next(get_db())
    upgrade_settings(db)
    seed_superadmin(db)
    db.close()


def seed_superadmin(db: Session):
    """
    Seeds the initial super administrator account if none exists,
    and repairs invalid empty seeded superadmin accounts.
    """
    import bcrypt
    
    # Retrieve configured credentials, falling back if env variable is missing or empty string
    username = os.getenv("SUPERADMIN_USERNAME") or "admin"
    password = os.getenv("ADMIN_PASSWORD") or "q1w2e3r4"
    
    superadmin = db.query(models.User).filter(models.User.is_superadmin == True).first()
    if not superadmin:
        pwd_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')
        db_user = models.User(
            username=username,
            hashed_password=hashed,
            name="Super Administrator",
            is_superadmin=True,
            comment="System-seeded superadmin"
        )
        db.add(db_user)
        db.commit()
        print(f"Superadmin user '{username}' seeded successfully.")
    elif not superadmin.username or superadmin.username.strip() == "":
        # Repair the invalid empty username
        pwd_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')
        superadmin.username = username
        superadmin.hashed_password = hashed
        db.commit()
        print(f"Repaired invalid empty superadmin username in database. Set to '{username}'.")


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
        if settings.default_cpu_quota == 10:
            settings.default_cpu_quota = 30
            db.commit()
            print("Upgraded default_cpu_quota setting from 10% to 30%.")
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
app.include_router(users_router.router)
