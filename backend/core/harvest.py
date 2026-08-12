"""Draining a node's telemetry buffer and reading its drive health.

The transport half of monitoring: what to run on the node and how to read
back what it says. Kept apart from the Celery task so the SSH commands and
their parsing can be tested without a broker, and apart from `core.telemetry`
so that module stays free of I/O.

Reuses the same channel backups already use — `root@node` with the
orchestrator's key — so monitoring adds no new credential, no listening port
on roadside hardware, and works through NAT because the orchestrator is the
side that initiates.
"""
from __future__ import annotations

import json
import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence

from core import ssh_keys

logger = logging.getLogger(__name__)

DEFAULT_BUFFER = "/var/log/edge/edge-bro/telemetry.jsonl"
DEFAULT_KEY = ssh_keys.ORCHESTRATOR_PRIVATE_KEY

#: Draining a buffer is a couple of megabytes at worst over a link that may be
#: a rural 2 Mbit uplink.
DRAIN_TIMEOUT_S = 120
#: smartctl on a healthy drive answers in well under a second; anything near
#: this means the drive is not answering and waiting longer will not help.
SMART_TIMEOUT_S = 45
#: A node that has not completed the SSH handshake by now is down as far as
#: this harvest is concerned. The next one is hours away.
CONNECT_TIMEOUT_S = 15


@dataclass
class HarvestResult:
    reachable: bool = False
    buffer_text: str = ""
    smart_reports: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reachable and not self.errors


def ssh_command(host: str, port: int, remote: str, key_path: str = DEFAULT_KEY) -> list:
    """The SSH invocation used for every monitoring call.

    StrictHostKeyChecking is disabled for the same reason the backup path
    disables it: a reprovisioned node legitimately presents a new host key,
    and authentication here is by key rather than by host identity. Bootstrap
    clears the stale known_hosts entry (see core.known_hosts), so this is not
    papering over a warning that should have been resolved.
    """
    return [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}",
        "-o", "BatchMode=yes",
        "-i", key_path,
        "-p", str(port),
        f"root@{host}",
        remote,
    ]


def drain_command(buffer_path: str = DEFAULT_BUFFER) -> str:
    """Shell to atomically hand the buffer over and start a fresh one.

    The move-then-read order is what makes this lossless. The collector is a
    one-shot process that opens the buffer with `>>` on each invocation, so it
    holds no long-lived descriptor: a sample written between the move and the
    read lands in the moved file and is still harvested, and every sample
    after that creates the new file. Truncating in place would instead lose
    whatever arrived in the gap.

    Prints nothing when there is no buffer yet, which is the normal state of a
    node whose collector was installed minutes ago.
    """
    quoted = shlex.quote(buffer_path)
    staged = shlex.quote(f"{buffer_path}.harvest")
    return (
        f"if [ -f {quoted} ]; then "
        f"mv -f {quoted} {staged} && cat {staged} && rm -f {staged}; "
        f"fi"
    )


def smart_command(device: str) -> str:
    # /usr/sbin is prepended because Debian's non-interactive PATH omits it for
    # non-root users; harmless as root, and it costs nothing to be explicit.
    return f"PATH=/usr/sbin:/sbin:$PATH smartctl -j -a {shlex.quote(device)}"


def list_devices_command() -> str:
    """Physical block devices worth asking smartctl about.

    Excludes partitions, loop and ram devices. `lsblk -d` already gives only
    whole devices; the type filter guards kernels where that is not enough.
    """
    return (
        "lsblk -dn -o NAME,TYPE 2>/dev/null | "
        "awk '$2==\"disk\" {print \"/dev/\" $1}'"
    )


def capability_command(buffer_path: str = DEFAULT_BUFFER) -> str:
    """One-shot probe of what this node can measure, as key:value lines."""
    quoted = shlex.quote(buffer_path)
    return (
        'RAPL=no; for d in /sys/class/powercap/intel-rapl:0 '
        '/sys/class/powercap/intel-rapl/intel-rapl:0; do '
        '[ -r "$d/energy_uj" ] && RAPL=yes && break; done; '
        'SSD=no; for h in /sys/class/hwmon/hwmon*; do [ -r "$h/name" ] || continue; '
        '[ "$(cat "$h/name" 2>/dev/null)" = "drivetemp" ] && SSD=yes && break; done; '
        'SMART=no; (command -v smartctl >/dev/null 2>&1 || [ -x /usr/sbin/smartctl ]) '
        '&& SMART=yes; '
        'TIMER=$(systemctl is-active edge-bro-collect.timer 2>/dev/null || echo unknown); '
        f'LINES=$(wc -l < {quoted} 2>/dev/null || echo 0); '
        'echo "rapl:$RAPL"; echo "drive_temp:$SSD"; echo "smartctl:$SMART"; '
        'echo "timer:$TIMER"; echo "buffered:$LINES"'
    )


def parse_capabilities(text: str) -> dict:
    """Turn the probe's key:value lines into something storable."""
    result: dict = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value in ("yes", "no"):
            result[key] = value == "yes"
        elif value.isdigit():
            result[key] = int(value)
        else:
            result[key] = value
    return result


def _run(host: str, port: int, remote: str, timeout: int, key_path: str) -> tuple:
    """Run one command on the node. Returns (stdout, error-or-None)."""
    try:
        completed = subprocess.run(
            ssh_command(host, port, remote, key_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "", f"timed out after {timeout}s"
    except OSError as e:
        return "", str(e)

    if completed.returncode != 0:
        return completed.stdout, (completed.stderr or "").strip()[:300] or (
            f"exit status {completed.returncode}"
        )
    return completed.stdout, None


def harvest(
    host: str,
    port: int,
    key_path: str = DEFAULT_KEY,
    buffer_path: str = DEFAULT_BUFFER,
    devices: Optional[Sequence[str]] = None,
) -> HarvestResult:
    """Collect everything the orchestrator wants from one node.

    Partial success is the normal outcome and is reported as such: a node
    whose drive stopped answering smartctl still has useful telemetry, and a
    node with no collector installed yet still has a readable drive. Only a
    failure to reach the node at all makes the whole harvest a failure.
    """
    result = HarvestResult()

    capability_text, error = _run(
        host, port, capability_command(buffer_path), SMART_TIMEOUT_S, key_path
    )
    if error and not capability_text:
        result.errors.append(f"unreachable: {error}")
        return result

    result.reachable = True
    result.capabilities = parse_capabilities(capability_text)

    buffer_text, error = _run(host, port, drain_command(buffer_path), DRAIN_TIMEOUT_S, key_path)
    if error:
        result.errors.append(f"telemetry drain failed: {error}")
    else:
        result.buffer_text = buffer_text

    if devices is None:
        listing, error = _run(host, port, list_devices_command(), SMART_TIMEOUT_S, key_path)
        if error:
            result.errors.append(f"device listing failed: {error}")
            devices = []
        else:
            devices = [d.strip() for d in listing.splitlines() if d.strip()]

    for device in devices:
        raw, error = _run(host, port, smart_command(device), SMART_TIMEOUT_S, key_path)
        # smartctl exits non-zero for conditions that still produce a complete
        # report — bit 2 means "some SMART command failed", bit 3 "disk is
        # failing". Refusing to parse those would throw away precisely the
        # readings worth having, so output is tried first and the exit status
        # only matters when nothing parseable came back.
        report = _parse_report(raw)
        if report is None:
            result.errors.append(f"{device}: {error or 'no parseable smartctl output'}")
            continue
        result.smart_reports[device] = report

    return result


def _parse_report(raw: str) -> Optional[dict]:
    if not raw or not raw.strip():
        return None
    try:
        report = json.loads(raw)
    except ValueError:
        return None
    return report if isinstance(report, dict) else None
