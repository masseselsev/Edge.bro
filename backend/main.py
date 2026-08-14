import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import get_db
import models
import paths
from version import VERSION
from tasks import ensure_orchestrator_ssh_key

# Import routers
from routers import settings as settings_router
from routers import nodes as nodes_router
from routers import archives as archives_router
from routers import history as history_router
from routers import tasks as tasks_router
from routers import restore as restore_router
from routers import stats as stats_router
from routers import iso as iso_router
from routers import network as network_router
from routers import groups as groups_router
from routers import kiosks as kiosks_router
import auth
from routers import users as users_router
from routers import health as health_router
from routers import ssh_keys as ssh_keys_router
from routers import monitoring as monitoring_router
from routers import notifications as notifications_router

app = FastAPI(title="Edge-B.R.O. API", version=VERSION)

# CORS. The UI is served from :7777 and this API answers on :8000, so they are
# different origins and the browser needs to be told this is allowed.
#
# It used to be told rather too much: `allow_origins=["*"]` together with
# `allow_credentials=True`. The wildcard is illegal with credentials per the
# fetch spec, so Starlette quietly echoes back whatever Origin arrived, and the
# result is that *any* website an operator visits could make credentialed
# requests to their orchestrator using their session cookie — trigger a
# restore, delete a node, read the fleet.
#
# The allowlist is a regex rather than a list because the orchestrator is
# reached by whichever of its addresses the operator happened to type, and a
# kiosk reaches its own backend on localhost. Both are private by construction;
# a public website is not, and that is the case this shuts out. Set
# CORS_ALLOWED_ORIGINS to a comma-separated list to pin it exactly.
_explicit_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

#: localhost, 10/8, 192.168/16 and 172.16-31/12, on any port, http or https.
PRIVATE_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127(\.\d{1,3}){3}|\[::1\]"
    r"|10(\.\d{1,3}){3}"
    r"|192\.168(\.\d{1,3}){2}"
    r"|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}"
    r")(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_explicit_origins,
    allow_origin_regex=None if _explicit_origins else PRIVATE_ORIGIN_REGEX,
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
        from core import repo_paths
        for shard in repo_paths.all_shard_paths():
            fix_repo_permissions(shard)
    except Exception as e:
        print(f"Error ensuring repository permissions on startup: {str(e)}")

    # Keep the fleet's SSH grants matching BORG_SHARD_COUNT.
    #
    # The forced command in every authorized_keys entry names the repositories
    # that key may reach, and it is derived from the count, so raising the count
    # is not complete until the grants are rewritten. That used to be a manual
    # step an operator had to know about, and forgetting it produced a
    # "restricted path" failure on the first node routed to a new shard.
    #
    # Order matters here and is the whole reason this is not a one-liner. The
    # rewrite makes the grants say exactly what the current count says, so
    # running it while the count is too low would *narrow* them and take away
    # access that currently works. The stranding check therefore comes first
    # and blocks the rewrite, rather than the other way round.
    try:
        from core import repo_paths
        from core.db_session import session_scope
        import models as _models

        with session_scope() as _db:
            stranded = repo_paths.stranded_shards(
                i for (i,) in _db.query(_models.Node.borg_shard_index).distinct()
            )

        if repo_paths.SHARD_COUNT > repo_paths.CONFIGURED_SHARD_COUNT:
            print(
                f"NOTE: BORG_SHARD_COUNT is set to {repo_paths.CONFIGURED_SHARD_COUNT}, but "
                f"{repo_paths.SHARD_COUNT} repositories already exist and hold archives. "
                f"Using {repo_paths.SHARD_COUNT}. The count can be raised but never lowered — "
                f"a node's shard is fixed when it is enrolled and its archives do not follow "
                f"it. Set BORG_SHARD_COUNT={repo_paths.SHARD_COUNT} to make this explicit."
            )

        if stranded:
            # The floor above covers shards that exist on disk. This is the case
            # it cannot see: a node routed to a shard it has not yet written to,
            # so there is no directory to infer it from.
            print(
                f"CONFIGURATION ERROR: nodes are assigned to shard(s) {stranded}, which is "
                f"beyond the {repo_paths.SHARD_COUNT} in use. Those nodes cannot back up or "
                f"restore. Set BORG_SHARD_COUNT to at least {max(stranded) + 1} and restart. "
                f"SSH grants have been left untouched — rewriting them now would remove "
                f"access that still works."
            )
        else:
            from scripts.reauthorize_shard_access import main as reauthorize
            reauthorize()
    except Exception as e:
        print(f"Error reconciling shard access on startup: {str(e)}")

    # A restart is exactly what orphans a kiosk repository download: its
    # cleanup lives in the streaming generator, which a killed process never
    # gets to finish. Each leftover is as large as the archive it holds.
    try:
        from routers.iso import sweep_stale_download_temps
        reclaimed = sweep_stale_download_temps()
        if reclaimed:
            print(f"Removed {reclaimed} abandoned repository download(s) on startup.")
    except Exception as e:
        print(f"Error sweeping abandoned repository downloads on startup: {str(e)}")

    # Clear any stale download lock file on startup. If a download was in progress, auto-resume it.
    try:
        lock_path = paths.DOWNLOAD_LOCK_PATH
        tmp_iso_path = paths.BASE_ISO_TMP_PATH
        base_iso_path = paths.BASE_ISO_PATH
        
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

    # Clear any stale tasks that were left in RUNNING state
    try:
        db = next(get_db())
        stale_tasks = db.query(models.TaskLog).filter(models.TaskLog.status == "RUNNING").all()
        for task in stale_tasks:
            task.status = "FAILED"
            task.log_output = (task.log_output or "") + "\n[SYSTEM] Task interrupted due to orchestrator service restart."
        db.commit()
        db.close()
        print(f"Cleared {len(stale_tasks)} stale running tasks on startup.")
    except Exception as e:
        print(f"Error clearing stale running tasks on startup: {str(e)}")

    # Auto-detect payload source file changes and trigger rebuild if needed
    try:
        from payload_hash import compute_payload_hash, read_stored_hash
        from iso_tasks import CACHE_DIR
        client_iso_path = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        if os.path.exists(client_iso_path):
            current_hash = compute_payload_hash()
            stored_hash = read_stored_hash()
            if current_hash != stored_hash:
                short_old = stored_hash[:8] if stored_hash else "none"
                short_new = current_hash[:8]
                print(f"Payload source hash changed ({short_old}... → {short_new}...). Triggering base ISO rebuild...")
                db_hash = next(get_db())
                from iso_tasks import trigger_base_iso_rebuild
                trigger_base_iso_rebuild(db_hash)
                db_hash.close()
            else:
                print(f"Payload source hash unchanged ({current_hash[:8]}...). No rebuild needed.")
        elif os.path.exists(paths.BASE_ISO_PATH):
            # The base ISO is cached but the template was never built — e.g. an
            # install that hit the base-download-never-triggers-a-build gap.
            # Nothing else will build it, so kick it off now.
            print("Base ISO cached but USB-Kiosk Client template not yet built — triggering build...")
            db_hash = next(get_db())
            from iso_tasks import trigger_base_iso_rebuild
            trigger_base_iso_rebuild(db_hash)
            db_hash.close()
        else:
            print("Compiled Offline Client ISO not yet built — skipping startup hash check.")
    except Exception as e:
        print(f"Error during payload hash check on startup: {str(e)}")

    db = next(get_db())
    upgrade_settings(db)
    seed_superadmin(db)
    db.close()



def seed_superadmin(db: Session):
    """
    Seeds the initial super administrator account if none exists,
    and repairs invalid empty seeded superadmin accounts.
    If RESET_SUPERADMIN_PASSWORD=true is set in env, updates superadmin password.
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
    else:
        updated = False
        pwd_bytes = password.encode('utf-8')
        
        if not superadmin.username or superadmin.username.strip() == "":
            superadmin.username = username
            updated = True

        if os.getenv("RESET_SUPERADMIN_PASSWORD", "").lower() in ("true", "1", "yes"):
            if not bcrypt.checkpw(pwd_bytes, superadmin.hashed_password.encode('utf-8')):
                salt = bcrypt.gensalt()
                superadmin.hashed_password = bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')
                superadmin.username = username
                updated = True
                print(f"Superadmin credentials for '{username}' reset via RESET_SUPERADMIN_PASSWORD environment variable.")

        if updated:
            db.commit()




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
        # Installs created before orchestrator_ip was seeded show an empty field
        # in the UI while tasks silently fall back to the env var. Backfill it.
        if not settings.orchestrator_ip:
            env_ip = os.getenv("ORCHESTRATOR_IP", "")
            if env_ip:
                settings.orchestrator_ip = env_ip
                db.commit()
                print(f"Seeded orchestrator_ip from ORCHESTRATOR_IP env: {env_ip}")
        if settings.default_cpu_quota == 10:
            settings.default_cpu_quota = 30
            db.commit()
            print("Upgraded default_cpu_quota setting from 10% to 30%.")
        old_defaults = [
            ['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*'],
            ['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found', '/var/log/edge/*', '/var/opt/edge/*'],
            ['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found', '/var/log/edge/*', '/var/opt/edge/*', '/var/spool/edge/*'],
            ['/dev/*', '/proc/*', '/sys/*', '/run/*', '/mnt/*', '/media/*', '/lost+found', '/var/log/edge/*', '/var/opt/edge/*', '/var/spool/edge/*', '/var/log/journal/*', '/var/log/**/*.gz', '/var/log/**/*.1']
        ]
        new_default = [
            {"pattern": "/dev/*", "comment": "System devices"},
            {"pattern": "/proc/*", "comment": "Virtual process filesystem"},
            {"pattern": "/sys/*", "comment": "Sysfs system info"},
            {"pattern": "/run/*", "comment": "Transient runtime files"},
            {"pattern": "/mnt/*", "comment": "Mounted filesystems"},
            {"pattern": "/media/*", "comment": "Removable media mounts"},
            {"pattern": "/lost+found", "comment": "Recovered filesystem fragments"},
            {"pattern": "/var/log/edge/*", "comment": "Edge app logs"},
            {"pattern": "/var/opt/edge/blobstore/*", "comment": "Local media files storage"},
            {"pattern": "/var/spool/edge/*", "comment": "Edge spool directory"},
            {"pattern": "/var/log/journal/*", "comment": "Systemd journal logs"},
            {"pattern": "/var/log/**/*.gz", "comment": "Compressed rotated logs"},
            {"pattern": "/var/log/**/*.1", "comment": "Rotated log backups"},
            {"pattern": "/var/hasplm/*", "comment": "Sentinel HASP licensing data"},
            {"pattern": "/etc/hasplm/*", "comment": "Sentinel HASP licensing config"}
        ]
        
        current_exclusions = settings.global_exclusions
        if not current_exclusions:
            settings.global_exclusions = new_default
            db.commit()
        else:
            if isinstance(current_exclusions, str):
                current_exclusions = [x.strip() for x in current_exclusions.split(",") if x.strip()]
            
            if isinstance(current_exclusions, list) and len(current_exclusions) > 0 and isinstance(current_exclusions[0], str):
                if current_exclusions in old_defaults:
                    settings.global_exclusions = new_default
                else:
                    settings.global_exclusions = [{"pattern": x, "comment": "Custom exclusion"} for x in current_exclusions]
                db.commit()


# Include routers
app.include_router(settings_router.router)
# Ahead of the nodes router so the literal /history paths are matched before
# the /{node_id} patterns that share their prefix.
app.include_router(history_router.router)
app.include_router(nodes_router.router)
# Registered after nodes: its paths sit under /api/nodes/history/ and are
# more specific, so ordering between the two is not load-bearing, but keeping
# them adjacent makes the shared prefix obvious.
app.include_router(archives_router.router)
app.include_router(tasks_router.router)
app.include_router(restore_router.router)
app.include_router(stats_router.router)
app.include_router(iso_router.router, prefix="/api/iso", tags=["Client ISO"])
# The network router carries no auth of its own because the kiosk payload
# client mounts the same module without a web session. On the orchestrator it
# reconfigures the host's WiFi, wired interfaces and VPN, so the guard is
# applied here, at the mount point.
app.include_router(
    network_router.router,
    prefix="/api",
    dependencies=[Depends(auth.require_admin)],
)
app.include_router(groups_router.router)
app.include_router(kiosks_router.router)
app.include_router(users_router.router)
app.include_router(health_router.router)
app.include_router(ssh_keys_router.router)
app.include_router(monitoring_router.router)
app.include_router(notifications_router.router)
