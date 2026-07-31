"""Single source of truth for on-disk locations shared across containers.

The ISO cache path below is the location *inside* the container. Which host
drive or Docker volume sits behind it is decided by ISO_CACHE_HOST_PATH in
.env — see docker-compose.yml.
"""

import os

ISO_CACHE_DIR = "/opt/data/iso_cache"

BASE_ISO_PATH = os.path.join(ISO_CACHE_DIR, "base.iso")
BASE_ISO_TMP_PATH = BASE_ISO_PATH + ".tmp"
BASE_ISO_SIZE_PATH = os.path.join(ISO_CACHE_DIR, "base.iso.size")
DOWNLOAD_LOCK_PATH = os.path.join(ISO_CACHE_DIR, "download.lock")
PAYLOAD_HASH_PATH = os.path.join(ISO_CACHE_DIR, "payload_hash.txt")
