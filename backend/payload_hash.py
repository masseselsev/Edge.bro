import hashlib
import os
import logging

logger = logging.getLogger(__name__)

HASH_FILE = "/opt/data/iso_cache/payload_hash.txt"

# All source paths that contribute to the Compiled Offline Client ISO.
# Any change in these files should trigger a rebuild.
SOURCE_PATHS = [
    "/payload_client/backend",
    "/payload_client/systemd",
    "/payload_client/conf",
    "/payload_client/kiosk-launcher.sh",
    "/payload_client/kiosk-storage-setup.sh",
    "/payload_client/init-bottom-copy-payload.sh",
    "/payload_client/bin/borg",
    "/app/core/disk_ops.py",
    "/app/routers/network.py",
    "/app/version.py",
]


def _hash_file(h, path: str) -> None:
    """Hash file contents into h, silently skip unreadable files."""
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception as exc:
        logger.debug(f"payload_hash: skipping unreadable file {path}: {exc}")


def compute_payload_hash() -> str:
    """
    Walk all SOURCE_PATHS and compute a single SHA256 digest over their
    sorted file contents. Returns a 64-char hex string.
    """
    h = hashlib.sha256()
    for path in sorted(SOURCE_PATHS):
        if os.path.isfile(path):
            # Also include the relative path itself so renames are detected
            h.update(path.encode())
            _hash_file(h, path)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs.sort()  # deterministic order
                for fname in sorted(files):
                    full = os.path.join(root, fname)
                    h.update(full.encode())
                    _hash_file(h, full)
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
