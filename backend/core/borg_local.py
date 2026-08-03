"""Running borg locally as the user that owns the repository.

The fleet repository is created by the borg-server container's `borg` user
with borg's default umask of 0077, so every file inside it is mode 0600 and
every directory 0700. The backend container runs as root, which normally
overrides those modes — which is why local borg calls work on most
deployments and why nobody noticed the dependency.

They stop working the moment the repository lives on storage that strips
root's privilege. NFS exported with `root_squash` is the usual case: root is
remapped to an anonymous uid and loses DAC override, so every local borg
command dies with

    PermissionError: [Errno 13] Permission denied: '/data/borg/fleet/config'

while backups keep succeeding — those reach the repository through
borg-server over SSH, as its actual owner.

Depending on being root is the bug. These helpers run borg as whoever owns
the repository instead, which is the right identity everywhere: on ordinary
storage the owner has exactly the access root was borrowing, and on squashed
NFS it is the only identity that has any. It also stops the backend from
leaving root-owned files inside a repository owned by borg, which is what
`fix_repo_permissions` was written to clean up after.
"""
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def repo_run_as(repo_path: str) -> Tuple[Optional[int], Optional[int]]:
    """(uid, gid) borg should run as for `repo_path`, or (None, None) for no change.

    Returns no change when we already are the owner, or when we are not root
    and therefore cannot become anyone else.
    """
    try:
        st = os.stat(repo_path)
    except OSError as e:
        logger.warning(f"Cannot stat {repo_path} to find its owner, running borg as-is: {e}")
        return None, None

    if st.st_uid == os.geteuid():
        return None, None

    if os.geteuid() != 0:
        logger.warning(
            f"{repo_path} is owned by uid {st.st_uid} but we run as uid {os.geteuid()} "
            f"and cannot switch; borg may fail with a permission error."
        )
        return None, None

    return st.st_uid, st.st_gid


def _base_dir_for(uid: int, gid: int) -> Optional[str]:
    """A BORG_BASE_DIR the target uid can actually write.

    borg keeps its cache and its per-repository security records under
    ~/.config/borg and ~/.cache/borg. HOME still points at /root, which a
    dropped-privilege process cannot write, so both have to be relocated or
    borg fails on its own bookkeeping instead of on the repository.
    """
    path = f"/tmp/borg-base-{uid}"
    try:
        os.makedirs(path, exist_ok=True)
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)
        return path
    except OSError as e:
        logger.error(f"Could not prepare BORG_BASE_DIR {path} for uid {uid}: {e}")
        return None


def borg_kwargs(repo_path: str, env: Dict[str, str]) -> Dict[str, object]:
    """Subprocess kwargs that run borg as `repo_path`'s owner.

    `env` is updated in place with a writable BORG_BASE_DIR. Returns an empty
    dict when no identity change is needed or possible, leaving the call
    exactly as it was before.

    Usage:
        kwargs = borg_kwargs(repo_path, env)
        subprocess.run(cmd, env=env, **kwargs)
    """
    uid, gid = repo_run_as(repo_path)
    if uid is None or gid is None:
        return {}

    base_dir = _base_dir_for(uid, gid)
    if base_dir is None:
        # Without a writable base dir borg would fail anyway; staying root at
        # least preserves the previous behaviour on deployments where it works.
        return {}

    env["BORG_BASE_DIR"] = base_dir
    env["HOME"] = base_dir

    logger.debug(f"Running borg against {repo_path} as uid {uid}:{gid}")
    return {"user": uid, "group": gid, "extra_groups": []}


def grant_workdir(path: str, repo_path: str) -> None:
    """Let the identity chosen for `repo_path` write into `path`.

    `borg extract` writes into its working directory, so a temp directory made
    by root has to be handed over to the dropped-privilege process.
    """
    uid, gid = repo_run_as(repo_path)
    if uid is None or gid is None:
        return
    try:
        os.chown(path, uid, gid)
    except OSError as e:
        logger.error(f"Could not hand {path} to uid {uid}: {e}")
