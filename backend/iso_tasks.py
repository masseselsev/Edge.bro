import os
import shutil
import subprocess
import json
import logging
import hashlib
import re
import secrets
from celery_app import celery_app
from core import ssh_keys
from core.db_session import session_scope
from typing import Any, Dict, List

import paths
from core import iso_build, task_log
from core.task_log import log_to_task
from core.clock import utcnow

logger = logging.getLogger(__name__)

#: Directory holding the current Debian **stable** live images.
#:
#: This used to point at the weekly `testing` build. Tracking testing gave the
#: kiosk the newest kernel, which is a real advantage for a rescue stick that
#: has to boot on hardware nobody chose in advance -- but it also decoupled the
#: kiosk from the packages we ship to it. The backend image downloads .debs
#: from its own suite and bakes them into the payload, so once testing moved to
#: a newer Python than stable, `borgbackup` refused to install on the kiosk:
#: it declares `python3 (<< 3.14)` and testing had 3.14. The offline SSH unit
#: installs those packages with `dpkg -i ... && systemctl start ssh`, so the
#: failure took ssh down with it.
#:
#: Pinning both sides to stable makes them the same distribution by
#: construction rather than by coincidence.
STABLE_LIVE_DIR = "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid"

#: `current-live` follows stable, but the filename carries the point release
#: (13.6.0 -> 13.7.0 ...), so it cannot simply be hardcoded. It is resolved
#: from the directory's SHA512SUMS at download time; this is the value used
#: when that lookup cannot be made -- offline installs, and the module-level
#: constant the API reports before any download has run.
FALLBACK_LIVE_ISO = "debian-live-13.6.0-amd64-xfce.iso"

DEFAULT_MIRROR_URLS = [f"{STABLE_LIVE_DIR}/{FALLBACK_LIVE_ISO}"]
BASE_ISO_URL = DEFAULT_MIRROR_URLS[0]
CACHE_DIR = paths.ISO_CACHE_DIR

#: Matches only the image itself. SHA512SUMS also lists `.iso.contents`,
#: `.iso.log` and `.iso.packages` for the same build, and a looser pattern
#: happily downloads a text file as the base image.
_LIVE_ISO_RE = re.compile(r"^(debian-live-[\d.]+-amd64-xfce\.iso)$")


def stable_live_iso_urls() -> List[str]:
    """Base-image URLs to try, current point release first.

    Reads the filename out of SHA512SUMS rather than scraping the directory
    index: it is the authoritative list of what is actually published there,
    it is small, and the download path fetches it anyway to verify the image.

    Never raises. A resolution failure falls back to the pinned filename,
    which is the previous behaviour and still points at stable -- the caller
    then reports the download failure, which is the honest error.
    """
    try:
        sums = subprocess.check_output(
            ["curl", "-4", "--connect-timeout", "10", "--retry", "1", "-sfL",
             f"{STABLE_LIVE_DIR}/SHA512SUMS"],
            timeout=60,
        ).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Could not read SHA512SUMS for the stable live image: {e}")
        return list(DEFAULT_MIRROR_URLS)

    names = []
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and _LIVE_ISO_RE.match(parts[1]):
            names.append(parts[1])

    if not names:
        logger.warning("SHA512SUMS listed no amd64 xfce live image; using the pinned filename.")
        return list(DEFAULT_MIRROR_URLS)

    # Newest first, so a directory mid-rotation between point releases picks
    # the one whose checksums were just published.
    names.sort(reverse=True)
    return [f"{STABLE_LIVE_DIR}/{n}" for n in names]

#: Sentinel passed instead of a real token when the base image is rebuilt for
#: its own sake — a settings change, a stale hash on startup — rather than
#: issued to anyone. See generate_client_iso_task.
TEMPLATE_BUILD = "TEMPLATE"
BASE_ISO_PATH = paths.BASE_ISO_PATH
BASE_ISO_PATH_TMP = paths.BASE_ISO_TMP_PATH

# The injection lists live with the build steps that use them; re-exported
# because tests/test_iso_payload_injection.py reads them from here.
INJECTED_CORE_MODULES = iso_build.INJECTED_CORE_MODULES
INJECTED_ROUTER_MODULES = iso_build.INJECTED_ROUTER_MODULES


@celery_app.task(bind=True)
def download_base_iso_task(self, url: str = None) -> Dict[str, Any]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    lock_path = os.path.join(CACHE_DIR, "download.lock")
    download_completed = False
    try:
        if os.path.exists(BASE_ISO_PATH):
            if os.path.getsize(BASE_ISO_PATH) > 1000 * 1024 * 1024:
                return {"status": "SUCCESS", "message": "Base ISO already cached."}
            else:
                os.remove(BASE_ISO_PATH)
        
        urls_to_try = [url] if url else stable_live_iso_urls()
        download_url = None
        content_length = None

        for attempt_url in urls_to_try:
            logger.info(f"Checking mirror: {attempt_url}")
            try:
                header_out = subprocess.check_output([
                    "curl", "-4", "--connect-timeout", "5", "--retry", "1", "-s", "-I", "-L", attempt_url
                ]).decode('utf-8', errors='ignore')
                
                temp_length = None
                for line in header_out.splitlines():
                    if line.lower().startswith("content-length:"):
                        temp_length = line.split(":", 1)[1].strip()
                
                if temp_length and temp_length.isdigit():
                    content_length = temp_length
                    download_url = attempt_url
                    logger.info(f"Mirror verified. Content-Length: {content_length}. Selected URL: {download_url}")
                    break
            except Exception as e:
                logger.warning(f"Mirror check failed for {attempt_url}: {e}")

        if not download_url:
            download_url = urls_to_try[0]
            logger.warning(f"All mirror checks failed. Falling back to primary URL: {download_url}")

        if content_length:
            try:
                with open(os.path.join(CACHE_DIR, "base.iso.size"), "w") as f:
                    f.write(content_length)
            except Exception as size_err:
                logger.warning(f"Could not write base.iso.size file: {size_err}")

        # Checked by location, not by list membership: the filename now varies
        # with the point release, so an equality test against the pinned
        # constant would skip checksum verification on every current image.
        is_official = download_url.startswith(STABLE_LIVE_DIR)
        logger.info(f"Downloading Base ISO from {download_url}...")

        # Check if another curl process is already downloading to the tmp path
        try:
            pgrep_out = subprocess.check_output(["pgrep", "-f", "curl.*base.iso.tmp"]).decode().strip()
            if pgrep_out:
                logger.warning(f"Another curl process (PIDs: {pgrep_out}) is already downloading. Aborting this task to prevent conflict.")
                return {"status": "SUCCESS", "message": "Base ISO download already in progress."}
        except subprocess.CalledProcessError:
            pass

        # Check if the temporary file is already completely downloaded
        if os.path.exists(BASE_ISO_PATH_TMP) and content_length:
            try:
                current_size = os.path.getsize(BASE_ISO_PATH_TMP)
                target_size = int(content_length)
                if current_size == target_size:
                    logger.info("Temporary file is already complete. Skipping curl download.")
                    download_completed = True
                elif current_size > target_size:
                    logger.warning("Local temporary file size is larger than remote content length. Deleting and restarting.")
                    os.remove(BASE_ISO_PATH_TMP)
            except ValueError:
                pass

        if not download_completed:
            # Use curl to download the file safely to a temporary path with fail-fast (-f) and resume (-C -)
            # Relaxed speed limits to prevent download failures on slow connections
            subprocess.check_call([
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-L", "-C", "-", "-o", BASE_ISO_PATH_TMP, download_url
            ])
            download_completed = True

        if is_official:
            logger.info("Downloading SHA512SUMS for validation...")
            sums_url = download_url.rsplit('/', 1)[0] + "/SHA512SUMS"
            sums_path = os.path.join(CACHE_DIR, "SHA512SUMS")
            subprocess.check_call([
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-sL", "-o", sums_path, sums_url
            ])
            
            iso_filename = os.path.basename(download_url)
            expected_hash = None
            with open(sums_path, 'r') as f:
                for line in f:
                    if iso_filename in line:
                        expected_hash = line.split()[0]
                        break
                        
            if not expected_hash:
                raise Exception("Could not find expected hash in SHA512SUMS")
                
            logger.info(f"Validating ISO checksum (expected: {expected_hash[:8]}...)..")
            hasher = hashlib.sha512()
            with open(BASE_ISO_PATH_TMP, 'rb') as f:
                for chunk in iter(lambda: f.read(4096 * 1024), b""):
                    hasher.update(chunk)
                    
            actual_hash = hasher.hexdigest()
            if actual_hash != expected_hash:
                raise Exception(f"Checksum mismatch! Expected {expected_hash}, got {actual_hash}")
        else:
            logger.info("Custom ISO URL provided. Skipping SHA512 validation.")
            
        os.rename(BASE_ISO_PATH_TMP, BASE_ISO_PATH)

        # The base image just changed, so the compiled USB-Kiosk Client template
        # is now stale — or, on a fresh install, was never built at all. Nothing
        # else triggers that first build, so it must happen here.
        try:
            with session_scope() as trigger_db:
                trigger_base_iso_rebuild(trigger_db)
        except Exception as trigger_err:
            logger.error(f"Failed to trigger client template rebuild after base ISO download: {trigger_err}")

        return {"status": "SUCCESS", "message": "Base ISO downloaded successfully."}
    except Exception as e:
        logger.error(f"Download or validation failed: {e}")
        # Only remove the temporary file if download completed but checksum validation failed
        if download_completed and os.path.exists(BASE_ISO_PATH_TMP):
            try:
                os.remove(BASE_ISO_PATH_TMP)
            except Exception as re:
                logger.error(f"Failed to remove corrupt temporary file: {re}")
        return {"status": "FAILED", "error": str(e)}
    finally:
        # Only clean up lock file if no other download process is currently active
        try:
            pgrep_out = subprocess.check_output(["pgrep", "-f", "curl.*base.iso.tmp"]).decode().strip()
            has_active_curl = bool(pgrep_out)
        except subprocess.CalledProcessError:
            has_active_curl = False

        if not has_active_curl and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except Exception as le:
                logger.error(f"Failed to remove download lock file: {le}")

def generate_kiosk_id() -> str:
    """Generates a memorable kiosk identifier in XX1234 pattern (2 letters + 4 digits)."""
    import random
    import string
    letters = "".join(random.choices(string.ascii_uppercase, k=2))
    digits = "".join(random.choices(string.digits, k=4))
    return f"{letters}{digits}"

def _authorize_orchestrator_key_for_kiosks() -> None:
    """Let the orchestrator's own key into its own authorized_keys.

    The kiosk pulls archives over SSH using the orchestrator's private key
    (shipped in the image), so the orchestrator has to accept that key against
    itself. Restricted to borg serve by BORG_SERVE_OPTIONS, and tagged so the
    SSH key audit can tell this self-grant apart from a stray key somebody
    added by hand.

    Failure is logged, not raised: the ISO is still worth producing, and the
    grant can be repaired without rebuilding several gigabytes.
    """
    # Deferred: tasks/__init__.py imports this module, so importing it back at
    # module scope closes a cycle. Unlike run_command_with_logging, these two
    # have not been moved out of the tasks package.
    from tasks import ensure_orchestrator_ssh_key, fix_ssh_permissions
    try:
        orch_pub_key = ensure_orchestrator_ssh_key()
        action = ssh_keys.authorize(
            ssh_keys.ORCHESTRATOR_AUTHORIZED_KEYS,
            orch_pub_key,
            options=ssh_keys.BORG_SERVE_OPTIONS,
            tag=ssh_keys.SELFGRANT_TAG,
        )
        fix_ssh_permissions()
        logger.info(
            "Orchestrator self-grant for kiosk access: %s (%s)",
            action.value, ssh_keys.fingerprint(orch_pub_key),
        )
    except Exception as ke:
        logger.error(f"Failed to setup SSH authorized_keys for kiosk: {ke}")


@celery_app.task(bind=True)
def generate_client_iso_task(self, target_ip: str, auth_token: str) -> Dict[str, Any]:
    from models import TaskLog
    import redis
    
    task_id = self.request.id

    try:
        REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = redis.Redis.from_url(REDIS_URL)
        r.delete("base_iso_dirty")
    except Exception as re:
        logger.error(f"Failed to clear base_iso_dirty in Celery task: {re}")

    # A scope rather than a session for the whole task: the build that follows
    # is many minutes of xorriso and cpio, and none of it needs a connection.
    with session_scope() as db:
        db.add(TaskLog(id=task_id, task_type="ISO_GEN", status="RUNNING", log_output=""))

    # Validate cached ISO size
    if os.path.exists(BASE_ISO_PATH):
        if os.path.getsize(BASE_ISO_PATH) < 1000 * 1024 * 1024:
            logger.warning("Cached Base ISO is too small (corrupted). Deleting it.")
            os.remove(BASE_ISO_PATH)

    output_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
    base_iso_to_use = BASE_ISO_PATH

    if not os.path.exists(BASE_ISO_PATH):
        if os.path.exists(output_iso) and os.path.getsize(output_iso) > 1000 * 1024 * 1024:
            logger.info("Base ISO not found, but client ISO exists. Using existing client ISO as base.")
            base_iso_to_use = output_iso
        else:
            log_to_task(task_id, "[PROGRESS] 5:Downloading Base ISO...")
            # Use the mirror sequence checking logic to select the working URL
            download_url = None
            candidates = stable_live_iso_urls()
            for attempt_url in candidates:
                logger.info(f"Checking mirror for client ISO generation: {attempt_url}")
                try:
                    subprocess.check_call([
                        "curl", "-4", "--connect-timeout", "5", "--retry", "1", "-s", "-I", "-L", attempt_url
                    ])
                    download_url = attempt_url
                    break
                except Exception as e:
                    logger.warning(f"Mirror check failed for {attempt_url}: {e}")

            if not download_url:
                download_url = candidates[0]
                logger.warning(f"All mirror checks failed. Falling back to primary URL: {download_url}")

            task_log.run_command_with_logging(task_id, [
                "curl", "-4", "--connect-timeout", "15", "--retry", "3", "--retry-delay", "2",
                "-f", "-L", "-o", BASE_ISO_PATH, download_url
            ])

    # Scratch space lives under CACHE_DIR rather than the container's own
    # /tmp: unpacking an ISO takes several times its compressed size, and
    # ISO_CACHE_HOST_PATH exists specifically so deployments with a small
    # root disk can point ISO storage at a larger drive or NFS mount. Using
    # /tmp here would fill the root disk regardless of that setting.
    work_dir = os.path.join(CACHE_DIR, "tmp", f"iso_gen_{task_id}")
    iso_unpacked = os.path.join(work_dir, "iso_unpacked")
    payload_dir = os.path.join(work_dir, "payload_initrd")
    
    try:
        # 1. Unpack the base ISO into a writable tree.
        log_to_task(task_id, "[PROGRESS] 10:Unpacking base ISO...")
        os.makedirs(work_dir, exist_ok=True)
        iso_build.unpack_iso(task_id, base_iso_to_use, iso_unpacked, progress_from=10, progress_to=20)

        # 2. Stage everything the offline client needs into a second initrd.
        log_to_task(task_id, "[PROGRESS] 30:Injecting payload and configurations...")
        opt_offline = iso_build.stage_payload_tree(payload_dir)
        task_log.run_command_with_logging(
            task_id, f"cp -v -r /payload_client/backend/* {os.path.join(opt_offline, 'backend')}/", shell=True,
        )
        iso_build.inject_shared_backend_modules(opt_offline)
        iso_build.inject_binaries_and_frontend(payload_dir, opt_offline)
        iso_build.inject_services(payload_dir)
        iso_build.inject_scripts(payload_dir)
        iso_build.inject_offline_packages(payload_dir)

        log_to_task(task_id, "[PROGRESS] 35:Injecting python environment dependencies...")
        iso_build.inject_site_packages(opt_offline)

        log_to_task(task_id, "[PROGRESS] 42:Generating kiosk configuration...")
        import models
        # A fresh scope: the one opened at the top of this task was closed long
        # before the build reached here.
        with session_scope() as db:
            settings = db.query(models.Settings).first()
            lang = settings.language if settings else "en"
            server_ips = list(settings.server_ips) if (settings and settings.server_ips) else []

        # A template build is a base image nobody was handed, so it must not
        # leave a usable credential behind. It bakes a random token that
        # matches nothing and does not touch auth_token.txt — a stick made
        # from it has to enrol like any other kiosk.
        is_template = auth_token == TEMPLATE_BUILD
        baked_token = secrets.token_hex(16) if is_template else auth_token

        iso_build.write_kiosk_config(opt_offline, {
            "orchestrator_ip": target_ip,
            "available_server_ips": server_ips,
            "auth_token": baked_token,
            "language": lang,
            "kiosk_id": generate_kiosk_id(),
        })

        # The token auth.py accepts from an offline restore client. Only a
        # real, issued ISO writes it; template rebuilds used to overwrite it
        # with their own placeholder, which both invalidated the operator's
        # actual stick and left that placeholder working as a password.
        if not is_template:
            with open(os.path.join(CACHE_DIR, "auth_token.txt"), "w") as f:
                f.write(auth_token.strip())

        _authorize_orchestrator_key_for_kiosks()
        iso_build.inject_orchestrator_ssh_key(opt_offline)

        # 3. Pack the staged tree as the second initrd.
        log_to_task(task_id, "[PROGRESS] 45:Packaging secondary initrd...")
        iso_build.pack_payload_initrd(
            task_id, payload_dir, os.path.join(iso_unpacked, "live", "payload.img"),
        )

        # 4. Teach both bootloaders to load it. Which one runs depends on the
        #    machine, so neither can be skipped.
        log_to_task(task_id, "[PROGRESS] 60:Updating bootloader configurations...")
        iso_build.patch_grub_config(iso_unpacked)
        iso_build.patch_syslinux_configs(iso_unpacked)

        # 5. The media self-check compares against these.
        log_to_task(task_id, "[PROGRESS] 75:Updating ISO checksums...")
        iso_build.update_md5sums(task_id, iso_unpacked)

        # 6. Repack.
        log_to_task(task_id, "[PROGRESS] 85:Repacking Live-USB ISO...")
        output_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        iso_build.repack_iso(
            task_id, iso_unpacked, output_iso,
            progress_from=85, progress_to=99, replace_existing=True,
        )

        log_to_task(task_id, "[PROGRESS] 100:Client ISO generated successfully!", status="SUCCESS")

        # Persist payload hash so future restarts can detect if sources changed
        try:
            from payload_hash import compute_payload_hash, write_stored_hash
            write_stored_hash(compute_payload_hash())
            logger.info("Payload source hash updated after successful build.")
        except Exception as hash_err:
            logger.warning(f"Failed to write payload hash after build: {hash_err}")

        # Auto-regenerate any existing approved kiosks to build on top of the new base ISO
        try:
            from models import Kiosk
            with session_scope() as db_reg:
                approved_kiosks = db_reg.query(Kiosk).filter(Kiosk.status == "APPROVED").all()
                for kiosk in approved_kiosks:
                    kiosk.rebuild_required = True
                # Read out before the scope closes: committing expires the
                # instances, and dispatching happens outside the session.
                to_rebuild = [(k.id, k.kiosk_id) for k in approved_kiosks]
            for kiosk_pk, kiosk_label in to_rebuild:
                logger.info(f"Auto-triggering rebuild for approved kiosk {kiosk_label} after base ISO update.")
                repack_kiosk_iso_task.delay(kiosk_pk)
        except Exception as e_kiosk:
            logger.error(f"Failed to auto-trigger kiosk ISO rebuild: {e_kiosk}")

        return {"status": "SUCCESS"}

    except Exception as e:
        log_to_task(task_id, f"Client ISO generation failed: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        try:
            REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
            r = redis.Redis.from_url(REDIS_URL)
            dirty = r.get("base_iso_dirty")
            if dirty:
                r.delete("base_iso_dirty")
                generate_client_iso_task.delay("127.0.0.1", TEMPLATE_BUILD)
        except Exception as re:
            logger.error(f"Failed to check/trigger dirty base ISO rebuild: {re}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@celery_app.task(bind=True)
def repack_kiosk_iso_task(self, kiosk_id: int) -> Dict[str, Any]:
    from models import TaskLog, Kiosk, Settings

    task_id = self.request.id

    work_dir = None
    try:
        # Everything the repack needs, read once. What follows is minutes of
        # xorriso, cpio and gzip; holding a connection across it is the bug
        # core.db_session exists to prevent.
        from routers.settings import get_local_ips
        with session_scope() as db:
            db.add(TaskLog(
                id=task_id, task_type=f"KIOSK_ISO_GEN_{kiosk_id}",
                status="RUNNING", log_output="",
            ))

            kiosk = db.query(Kiosk).filter(Kiosk.id == kiosk_id).first()
            if not kiosk:
                raise Exception(f"Kiosk record {kiosk_id} not found")

            settings = db.query(Settings).first()
            max_kiosk_isos = settings.max_kiosk_isos if settings else 5
            target_ip = kiosk.target_ip if kiosk.target_ip else (settings.orchestrator_ip if settings else "127.0.0.1")

            manual_ips = settings.server_ips if (settings and settings.server_ips) else []
            lang = settings.language if settings else "en"
            server_name = settings.server_name if (settings and settings.server_name) else "edge-bro"
            kiosk_auth_token = kiosk.auth_token
            kiosk_label = kiosk.kiosk_id

        # get_local_ips shells out to `ip`, so it stays outside the scope.
        available_ips = sorted(set(get_local_ips() + list(manual_ips)))

        template_iso = os.path.join(CACHE_DIR, "technician_client_v1.iso")
        if not os.path.exists(template_iso):
            raise Exception("Base template ISO not found. Compile generic Live-USB first.")

        history_dir = os.path.join(CACHE_DIR, "history")
        os.makedirs(history_dir, exist_ok=True)

        # Clean up any existing ISO files for this kiosk token first to ensure clean generation and save space
        for file in os.listdir(history_dir):
            if file.endswith(f"-{kiosk_auth_token}.iso") and "-kiosk-" in file:
                try:
                    os.remove(os.path.join(history_dir, file))
                except Exception:
                    pass
                    
        from datetime import datetime
        created_date = datetime.now().strftime("%Y%m%d")
        output_kiosk_iso = os.path.join(history_dir, f"{server_name}-kiosk-{created_date}-{kiosk_auth_token}.iso")

        # Same reasoning as generate_client_iso_task: keep multi-GB scratch
        # space off the root disk and on the configured ISO storage.
        work_dir = os.path.join(CACHE_DIR, "tmp", f"repack_{kiosk_id}_{task_id}")
        iso_unpacked = os.path.join(work_dir, "iso_unpacked")
        payload_unpacked = os.path.join(work_dir, "payload_unpacked")
        os.makedirs(work_dir, exist_ok=True)

        # 1. Unpack the generic template — the per-kiosk ISO is the shared
        #    image with one config file swapped, not a rebuild from scratch.
        #    A full build takes tens of minutes; this takes a few.
        log_to_task(task_id, "[PROGRESS] 10:Extracting generic ISO template...")
        iso_build.unpack_iso(task_id, template_iso, iso_unpacked, progress_from=10, progress_to=25)

        # 2. Unpack the payload initrd we appended when the template was built.
        log_to_task(task_id, "[PROGRESS] 30:Extracting secondary initrd payload...")
        os.makedirs(payload_unpacked, exist_ok=True)
        payload_img_path = os.path.join(iso_unpacked, "live", "payload.img")
        if not os.path.exists(payload_img_path):
            raise Exception("payload.img not found in template ISO")

        subprocess.run(
            f"gzip -dc {payload_img_path} | cpio -idmv",
            shell=True,
            cwd=payload_unpacked,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # 3. Swap in this kiosk's identity. Everything else in the payload is
        #    identical across kiosks, which is what makes the shortcut sound.
        log_to_task(task_id, "[PROGRESS] 50:Updating configuration token...")
        config_data = iso_build.read_kiosk_config(payload_unpacked)
        config_data.update({
            "auth_token": kiosk_auth_token,
            "kiosk_id": kiosk_label,
            "available_server_ips": available_ips,
            "orchestrator_ip": target_ip,
            "language": lang,
        })
        with open(iso_build.kiosk_config_path(payload_unpacked), "w") as f:
            json.dump(config_data, f, indent=4)

        # 4. Repack the initrd in place. The bootloaders already reference it.
        log_to_task(task_id, "[PROGRESS] 70:Repacking secondary initrd...")
        subprocess.run(
            f"find . -print0 | cpio -o -H newc --null | gzip > {payload_img_path}",
            shell=True,
            cwd=payload_unpacked,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # 5. Compile.
        log_to_task(task_id, "[PROGRESS] 80:Compiling custom kiosk ISO...")
        iso_build.repack_iso(task_id, iso_unpacked, output_kiosk_iso)

        # 6. Each of these is several gigabytes; keep only the newest few.
        log_to_task(task_id, "[PROGRESS] 95:Pruning old repository ISOs...")
        iso_build.prune_kiosk_iso_history(history_dir, max_kiosk_isos)

        from datetime import datetime
        with session_scope() as db:
            kiosk = db.query(Kiosk).filter(Kiosk.id == kiosk_id).first()
            if kiosk:
                kiosk.iso_built_at = utcnow()
                kiosk.rebuild_required = False

        log_to_task(task_id, "[PROGRESS] 100:Kiosk custom ISO generated successfully!", status="SUCCESS")
        return {"status": "SUCCESS", "iso_path": output_kiosk_iso}

    except Exception as e:
        logger.error(f"Kiosk ISO repackaging failed: {e}")
        log_to_task(task_id, f"Kiosk ISO repackaging failed: {str(e)}", status="FAILED")
        return {"status": "FAILED", "error": str(e)}
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def trigger_base_iso_rebuild(db):
    import redis
    import os
    from datetime import datetime
    import models
    
    active_task = db.query(models.TaskLog).filter(
        models.TaskLog.task_type == "ISO_GEN",
        models.TaskLog.status == "RUNNING"
    ).first()
    
    if active_task:
        age = utcnow() - active_task.created_at
        if age.total_seconds() > 45 * 60:
            active_task.status = "FAILED"
            active_task.log_output += "\n[SYSTEM] Task assumed dead after 45 minutes timeout."
            db.commit()
        else:
            REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
            r = redis.Redis.from_url(REDIS_URL)
            r.set("base_iso_dirty", "1")
            return
            
    generate_client_iso_task.delay("127.0.0.1", TEMPLATE_BUILD)

