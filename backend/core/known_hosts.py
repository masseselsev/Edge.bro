"""Clearing a stale SSH host key before the orchestrator connects to a node.

A different concern from `core.ssh_keys`: that module manages who is allowed
to connect *to* the orchestrator (`authorized_keys`), keyed by fingerprint and
comment tag. This one clears what the orchestrator remembers about a node's
identity when it connects *out* (`known_hosts`), which carries no tag at all —
it is just whatever key the node offered last time.

Reprovisioning reinstalls the node's OS, and every OS install generates fresh
SSH host keys. The orchestrator's own known_hosts still holds the entry from
before, so every StrictHostKeyChecking=no connection afterwards — every
backup — prints a full "REMOTE HOST IDENTIFICATION HAS CHANGED" warning. Not
fatal (StrictHostKeyChecking=no lets the connection through regardless, since
authentication is by key, not by host identity) but permanent noise, and it
leaves the orchestrator unable to ever again tell a routine reinstall from a
real man-in-the-middle for that node, because the stale entry never resolves
either way.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_KNOWN_HOSTS = "/root/.ssh/known_hosts"

_TIMEOUT_SECONDS = 10


def host_spec(host: str, port: int) -> str:
    """The token ssh-keygen expects: bracketed for a non-default port.

    OpenSSH stores non-standard-port entries as `[host]:port`; matching that
    exactly is what makes `-R` find a hashed entry, since ssh-keygen hashes
    this same string to compare rather than storing the host in the clear.
    """
    return host if port == 22 else f"[{host}]:{port}"


def forget(host: str, port: int, known_hosts_path: str = DEFAULT_KNOWN_HOSTS) -> bool:
    """Remove any known_hosts entry for `host:port`. Safe to call unconditionally.

    Bootstrap always means the node's identity may have changed, so this runs
    every time rather than trying to detect whether the key actually did.
    Removing an entry that turns out to still be correct costs nothing — the
    very next connection relearns it, exactly as it would for a node seen for
    the first time. Returns whether an entry was actually removed, so the
    caller can log something only when there was something to clear.
    """
    if not os.path.exists(known_hosts_path):
        return False

    try:
        before = os.path.getsize(known_hosts_path)
    except OSError:
        return False

    try:
        subprocess.run(
            ["ssh-keygen", "-R", host_spec(host, port), "-f", known_hosts_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not clear known_hosts entry for {host}:{port}: {e}")
        return False

    try:
        after = os.path.getsize(known_hosts_path)
    except OSError:
        return False
    return after < before


def record(host: str, port: int, known_hosts_path: str = DEFAULT_KNOWN_HOSTS) -> bool:
    """Fetch and append the current SSH ED25519 host key for `host:port` using `ssh-keyscan`.

    First removes any stale entry using `forget(host, port, known_hosts_path)` to ensure
    no duplicate or outdated host key remains in `known_hosts_path`. Returns True if
    a host key was successfully scanned and appended.
    """
    forget(host, port, known_hosts_path)

    cmd = ["ssh-keyscan", "-t", "ed25519", "-p", str(port), host]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
        output = res.stdout.strip()
        if res.returncode == 0 and output:
            os.makedirs(os.path.dirname(os.path.abspath(known_hosts_path)), exist_ok=True)
            with open(known_hosts_path, "a") as f:
                f.write(output + "\n")
            return True
        logger.warning(f"ssh-keyscan returned empty or non-zero for {host}:{port}: {res.stderr}")
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Could not record known_hosts entry for {host}:{port}: {e}")
    return False
