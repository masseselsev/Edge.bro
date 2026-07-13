import hashlib
import os
import logging

logger = logging.getLogger(__name__)

HASH_FILE = "/opt/data/iso_cache/payload_hash.txt"

# Source paths that go INTO the Compiled Offline Client ISO.
# IMPORTANT: Only /payload_client/ paths here — /app/ paths differ between
# the backend and worker containers (hot-reload mount vs baked image), which
# would cause spurious hash mismatches on every restart.
# disk_ops.py, network.py, version.py are stable: their changes always come
# bundled with a new borg binary or payload_client update anyway.
SOURCE_PATHS = [
    "/payload_client/backend",
    "/payload_client/systemd",
    "/payload_client/conf",
    "/payload_client/kiosk-launcher.sh",
    "/payload_client/kiosk-storage-setup.sh",
    "/payload_client/init-bottom-copy-payload.sh",
    "/opt/frontend_build",
]

# Large binaries: hash by size + first/last 64KB only (fast, still reliable).
BINARY_PATHS = [
    "/payload_client/bin/borg",
]

# NOTE: /app/ files (disk_ops.py, network.py, version.py) are intentionally
# NOT hashed here. The backend and worker containers may mount different
# versions of /app/ (backend uses live host mount for hot-reload, worker uses
# baked image), causing persistent hash mismatches on every restart.
# Those files only change with a deliberate `docker compose build` + redeploy,
# at which point a manual ISO rebuild can be triggered if needed.

# Skip these when walking directories (generated files that change on import)
SKIP_DIRS = {"__pycache__"}
SKIP_EXTS = {".pyc", ".pyo"}


def _hash_file(h, path: str) -> None:
    """Hash full file contents into h, skip unreadable files silently."""
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception as exc:
        logger.debug(f"payload_hash: skipping {path}: {exc}")


def _hash_binary_fast(h, path: str) -> None:
    """Hash a large binary by size + first 64KB + last 64KB (fast fingerprint)."""
    try:
        size = os.path.getsize(path)
        h.update(path.encode())
        h.update(size.to_bytes(8, "little"))
        with open(path, "rb") as f:
            h.update(f.read(65536))
            if size > 65536:
                f.seek(-min(65536, size), 2)
                h.update(f.read(65536))
    except Exception as exc:
        logger.debug(f"payload_hash: skipping binary {path}: {exc}")


def compute_payload_hash() -> str:
    """
    Compute SHA256 over /payload_client/ source files. Deterministic and fast:
    - Directories: walk sorted, skip __pycache__ and .pyc files
    - Large binaries (/payload_client/bin/borg): size + first/last 64KB only
    - /app/ paths intentionally excluded (differ between containers)
    Returns a 64-char hex string.
    """
    h = hashlib.sha256()

    for path in sorted(SOURCE_PATHS):
        if os.path.isfile(path):
            h.update(path.encode())
            _hash_file(h, path)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                # Skip __pycache__ and similar generated directories
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for fname in sorted(files):
                    if os.path.splitext(fname)[1] in SKIP_EXTS:
                        continue
                    full = os.path.join(root, fname)
                    h.update(full.encode())
                    _hash_file(h, full)

    for path in sorted(BINARY_PATHS):
        if os.path.isfile(path):
            _hash_binary_fast(h, path)


    return h.hexdigest()


def read_stored_hash():
    """Read the hash written after the last successful build, or None."""
    try:
        with open(HASH_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning(f"payload_hash: failed to read stored hash: {exc}")
        return None


def write_stored_hash(hash_val: str) -> None:
    """Persist hash after a successful build."""
    try:
        os.makedirs(os.path.dirname(HASH_FILE), exist_ok=True)
        with open(HASH_FILE, "w") as f:
            f.write(hash_val)
    except Exception as exc:
        logger.error(f"payload_hash: failed to write hash: {exc}")
