"""One place that knows how this orchestrator talks SSH to a node.

Thirteen copies of this invocation had accumulated. Eleven of them omitted
`BatchMode=yes`, which is not cosmetic: without it, an ssh that cannot
authenticate falls back to prompting, and since these run with no terminal the
child sits waiting on a password that will never arrive. Most of those eleven
are inside FastAPI request handlers, so the symptom is a worker thread parked
for as long as the subprocess timeout allows — or forever, where the caller
passed no timeout. Most also omitted `ConnectTimeout`, leaving the kernel's
default of a couple of minutes to a node that is simply switched off.

Two policies here are deliberate rather than sloppy, and both are documented
where they are decided:

* **`StrictHostKeyChecking=no`.** A reprovisioned node legitimately presents a
  new host key, and authentication is by key, not by host identity. Bootstrap
  clears the stale entry explicitly (see `core.known_hosts`), so this is not
  papering over a warning that should have been resolved.
* **Root.** Every one of these commands needs it — borg, systemd units, the
  Sentinel runtime — and the bootstrap playbook is what establishes the key.
"""
import os
from typing import List, Optional

#: The orchestrator's own key, installed on every node by bootstrap.
DEFAULT_KEY = "/root/.ssh/id_ed25519"

#: Enough for a node on a slow link to answer, short enough that an HTTP
#: handler waiting on one does not hold its worker for minutes.
DEFAULT_CONNECT_TIMEOUT_S = 10


def command(
    host: str,
    port: int,
    remote: Optional[str] = None,
    *,
    key_path: Optional[str] = DEFAULT_KEY,
    user: str = "root",
    connect_timeout: Optional[int] = DEFAULT_CONNECT_TIMEOUT_S,
    keepalive: bool = False,
    discard_host_key: bool = False,
    extra_args: Optional[List[str]] = None,
    interactive: bool = False,
) -> List[str]:
    """Build the argv for one SSH call to a node.

    `keepalive` adds ServerAlive probes. Use it for anything that can sit
    silent for minutes — a `borg create` streams nothing between checkpoints,
    and a NAT device that drops the idle connection looks exactly like a failed
    backup.

    `discard_host_key` sends the learned key to /dev/null and quiets the
    resulting warning. It is for read-only probes that must not touch
    known_hosts; the backup and bootstrap paths deliberately do not use it,
    because recording the key is what stops every later run from printing
    REMOTE HOST IDENTIFICATION HAS CHANGED.

    `extra_args` is passed through verbatim before the destination, which is
    how the NAT path inserts its reverse tunnel (`-R`).

    `connect_timeout=None` leaves the system default in place. Only the backup
    transfer wants that: capping the initial connect at ten seconds would fail
    backups on links that are merely slow rather than down.

    `remote=None` omits the trailing remote command entirely, giving an
    interactive login shell instead of running one command and exiting — what
    `core/terminal_bridge.py` wants. Every other caller passes a real command
    string.

    `key_path=None` omits `-i` entirely, so authentication falls through to
    whatever ssh would otherwise try (an agent, or — combined with
    `interactive=True` — a password prompt on the attached pty). Every
    existing caller keeps passing a real path, so this is opt-in.

    `interactive=True` is for a real pty attached locally (see
    core/terminal_bridge.py): it forces `-tt` (ssh's own heuristic for
    whether to allocate a remote pty can guess wrong when stdin is a pty it
    did not open itself) and, critically, skips `BatchMode=yes` — a hung
    password prompt is a bug everywhere else in this codebase, but it is
    exactly what the password credential path needs to reach the human
    sitting at the other end of the pty.
    """
    argv = ["ssh", "-o", "StrictHostKeyChecking=no"]

    if discard_host_key:
        argv += ["-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

    if connect_timeout is not None:
        argv += ["-o", f"ConnectTimeout={connect_timeout}"]

    if keepalive:
        argv += [
            "-o", f"ServerAliveInterval={keepalive_interval()}",
            "-o", f"ServerAliveCountMax={keepalive_count()}",
        ]

    if interactive:
        argv += ["-tt"]
    else:
        # Never prompt. There is no terminal to prompt on, so a prompt is a hang.
        argv += ["-o", "BatchMode=yes"]

    argv += ["-p", str(port)]
    if key_path is not None:
        argv += ["-i", key_path]
    argv += list(extra_args or [])
    argv += [f"{user}@{host}"]
    if remote:
        argv += [remote]
    return argv


def keepalive_interval() -> int:
    """Read per call, not at import: the workers pick these up from the
    environment and a redeploy should not need a code change."""
    return int(os.getenv("SSH_KEEPALIVE_INTERVAL", "30"))


def keepalive_count() -> int:
    return int(os.getenv("SSH_KEEPALIVE_COUNT", "3"))


#: The key borg uses on the *node* to reach the orchestrator's borg-server.
#: Installed by the bootstrap playbook, and distinct from DEFAULT_KEY, which is
#: the orchestrator's own key for reaching nodes — opposite direction.
BORG_NODE_KEY = "/home/borg/.ssh/id_ed25519"


def borg_rsh(*, compression: bool = False) -> str:
    """The `BORG_RSH` value the node uses to push archives to the orchestrator.

    A shell string rather than an argv, because that is the interface borg
    offers: it word-splits this and execs it. It is written by the
    orchestrator but runs on the node, so none of the argv builder above
    applies.

    `Compression=no` is set on the transfer itself: borg has already
    compressed every chunk, and asking ssh to compress compressed data costs
    CPU on a small box to make the stream slightly larger.
    """
    parts = [
        "ssh", "-i", BORG_NODE_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", f"ServerAliveInterval={keepalive_interval()}",
        "-o", f"ServerAliveCountMax={keepalive_count()}",
    ]
    if not compression:
        # Appended, not spliced. This was `parts[4:4] = [...]`, which inserts
        # between the first `-o` and the value it belongs to, producing
        # `-o -o Compression=no StrictHostKeyChecking=no` — ssh then reports
        # "no argument after keyword -o" and the remote closes the connection.
        # Every backup failed; `borg init` did not, because it is the one
        # caller that asks for compression and so skipped this branch.
        parts += ["-o", "Compression=no"]
    return " ".join(parts)
