"""Removing archives a discarded history record left behind in the repository.

A failed `borg create` usually leaves nothing, but not always: borg writes
checkpoint archives named `<archive>.checkpoint` as it goes, and a run killed
partway through leaves the last one in place. Dropping the database row
without them would leave orphans nothing in the UI refers to any more.

The matching rule is the part worth being careful about, so it is a pure
function: `WS20240170-20260623124040` must claim its own checkpoints and
nothing belonging to `WS20240170-20260623124041`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Iterable, Optional

from core import repo_paths
from core.repo_lock import LOCK_WAIT_SECONDS
from core.borg_local import borg_kwargs

logger = logging.getLogger(__name__)

_LIST_TIMEOUT_SECONDS = 60
_DELETE_TIMEOUT_SECONDS = 120


def matching_archives(repo_archives: Iterable[str], archive_name: str) -> list[str]:
    """Names in the repository that belong to `archive_name`.

    That is the archive itself plus anything suffixed onto it with a dot —
    borg's checkpoint naming. A shared prefix is not enough: two archives one
    second apart differ only in their final character.
    """
    if not archive_name:
        return []
    prefix = archive_name + "."
    return sorted(
        name for name in repo_archives
        if name == archive_name or name.startswith(prefix)
    )


def repo_path() -> str:
    """Fallback for callers with no node in hand — shard 0, the one repository
    that always exists. Anything that knows its node should pass that node's
    path explicitly; see core.repo_paths."""
    return repo_paths.shard_path(0)


def _env() -> dict:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = os.getenv("BORG_PASSPHRASE", "")
    return env


def list_repo_archives(path: Optional[str] = None) -> Optional[set[str]]:
    """Every archive name in the repository, or None if it cannot be read.

    Read with `--bypass-lock`: this only informs a decision about what to try
    deleting, and waiting on a running backup's lock just to answer it would
    block the request for as long as the backup takes.
    """
    target = path or repo_path()
    if not os.path.isdir(target):
        return None

    env = _env()
    try:
        result = subprocess.run(
            ["borg", "list", "--bypass-lock", "--json", target],
            env=env,
            capture_output=True,
            text=True,
            timeout=_LIST_TIMEOUT_SECONDS,
            **borg_kwargs(target, env),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not list archives in {target}: {e}")
        return None

    if result.returncode != 0:
        logger.warning(f"borg list failed for {target}: {result.stderr.strip()[:200]}")
        return None

    try:
        payload = json.loads(result.stdout)
    except ValueError:
        logger.warning(f"Unparseable borg list output for {target}")
        return None

    return {entry.get("name") for entry in payload.get("archives", []) if entry.get("name")}


def delete_archives(names: Iterable[str], path: Optional[str] = None) -> int:
    """Delete the named archives, returning how many actually went.

    Deletes take the repository lock — bypassing it here would corrupt the
    repository. A backup holding the lock means this returns fewer than asked
    for, which the caller reports rather than retries.
    """
    target = path or repo_path()
    wanted = [n for n in names if n]
    if not wanted or not os.path.isdir(target):
        return 0

    env = _env()
    removed = 0
    for name in wanted:
        try:
            result = subprocess.run(
                ["borg", "delete", "--lock-wait", str(LOCK_WAIT_SECONDS), f"{target}::{name}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=_DELETE_TIMEOUT_SECONDS,
                **borg_kwargs(target, env),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Could not delete archive {name}: {e}")
            continue

        if result.returncode == 0:
            removed += 1
        else:
            logger.warning(f"borg delete failed for {name}: {result.stderr.strip()[:200]}")

    return removed
