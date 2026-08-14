"""How much room the fleet repository takes and how much is left.

Kept apart from `backup_stats` because this half touches the filesystem. The
figures the database can produce — how many bytes backups reported writing —
answer a different question from how many bytes are actually on the disk, and
conflating the two is what made the old Archives cards misleading.

`borg info` is deliberately not used. On a large repository it can block for
minutes rebuilding its cache, which is not something an HTTP handler should
ever wait for.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

from core import repo_paths

logger = logging.getLogger(__name__)

#: Walking a repository of thousands of segment files is not free, and the
#: number moves slowly. Re-measuring once every few minutes is plenty.
_CACHE_TTL_SECONDS = 300
_DU_TIMEOUT_SECONDS = 30

_size_cache: dict[str, tuple[float, Optional[int]]] = {}


def repo_path() -> str:
    """Shard 0, for callers measuring a single repository with none specified.
    Fleet-wide totals sum over core.repo_paths.all_shard_paths() instead."""
    return repo_paths.shard_path(0)


def disk_usage(path: Optional[str] = None) -> dict[str, Optional[int]]:
    """Total, used and free bytes on the filesystem holding the repository.

    Falls back to the parent directory when the repository itself does not
    exist yet, so a fresh install still reports the disk it will land on.
    """
    target = path or repo_path()
    for candidate in (target, os.path.dirname(target) or "/", "/"):
        try:
            usage = shutil.disk_usage(candidate)
        except OSError:
            continue
        return {
            "disk_total_bytes": usage.total,
            "disk_used_bytes": usage.used,
            "disk_free_bytes": usage.free,
        }
    return {"disk_total_bytes": None, "disk_used_bytes": None, "disk_free_bytes": None}


def repo_size_bytes(path: Optional[str] = None, use_cache: bool = True) -> Optional[int]:
    """Bytes the repository directory actually occupies, or None if unreadable.

    This is the honest answer to "how much storage are the backups using",
    unlike any sum of per-archive sizes: it already accounts for deduplication,
    compression and whatever retention has pruned.
    """
    target = path or repo_path()

    if use_cache:
        cached = _size_cache.get(target)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    size = _measure(target)
    _size_cache[target] = (time.monotonic(), size)
    return size


def fleet_repo_size_bytes(use_cache: bool = True) -> Optional[int]:
    """Bytes every shard occupies together.

    Shards that do not exist yet contribute nothing rather than making the
    whole total unknown — an uncreated shard genuinely holds zero bytes. None
    only if no shard could be measured at all.

    Note there is no matching sum for `disk_usage`: every shard lives on the
    same volume, so summing free space would report it several times over.
    """
    total = 0
    measured = False
    for path in repo_paths.all_shard_paths():
        size = repo_size_bytes(path, use_cache=use_cache)
        if size is not None:
            total += size
            measured = True
    return total if measured else None


def _measure(target: str) -> Optional[int]:
    if not os.path.isdir(target):
        return None
    try:
        result = subprocess.run(
            ["du", "-sb", target],
            capture_output=True,
            text=True,
            timeout=_DU_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not measure repository size at {target}: {e}")
        return None

    # du reports what it could read on stderr and still exits non-zero; a
    # partial total is worse than no total, so only a clean run counts.
    if result.returncode != 0:
        logger.warning(f"du failed for {target}: {result.stderr.strip()[:200]}")
        return None

    field = result.stdout.split("\t", 1)[0].strip()
    try:
        return int(field)
    except ValueError:
        logger.warning(f"Unexpected du output for {target}: {result.stdout[:120]!r}")
        return None


def reset_cache() -> None:
    """Drop the memoised sizes. For tests, and after a purge."""
    _size_cache.clear()
