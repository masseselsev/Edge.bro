"""Bridges a browser terminal to a real interactive shell on a node.

One `ssh` child process per session, with a pty attached the same way a real
terminal would be — so the remote end sees an actual TTY (relevant for
prompts, job control, colour) and, for the password credential path, ssh's
own interactive password prompt reaches the operator verbatim instead of the
orchestrator needing to parse or relay a credential it should never see.

routers/terminal.py owns the WebSocket lifecycle (auth, idle timeout, audit
logging); this module only knows how to open one of these sessions and move
bytes and resize events through it.
"""
from __future__ import annotations

import fcntl
import os
import pty
import struct
import subprocess
import termios
from dataclasses import dataclass
from typing import Optional

from core import ssh


@dataclass
class TerminalSession:
    """Owns the pty and the ssh child process for one browser terminal."""

    master_fd: int
    process: subprocess.Popen

    def read(self, size: int = 4096) -> bytes:
        """Caller is expected to only call this once the fd has been reported
        readable (see routers.terminal's use of loop.add_reader). Returns
        b"" once the child has exited and the pty has nothing left to give."""
        try:
            return os.read(self.master_fd, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        try:
            os.write(self.master_fd, data)
        except OSError:
            pass

    def resize(self, cols: int, rows: int) -> None:
        try:
            fcntl.ioctl(
                self.master_fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError:
            pass

    def poll(self) -> Optional[int]:
        """None while still running, else the ssh process's exit code."""
        return self.process.poll()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        try:
            os.close(self.master_fd)
        except OSError:
            pass


def open_bridge(*, host: str, port: int, user: str, use_key: bool) -> TerminalSession:
    """Opens one interactive ssh session, pty attached.

    `use_key=True` authenticates with the orchestrator's own key
    (`core.ssh.DEFAULT_KEY` — the same key bootstrap installs and backups
    already use) and never prompts; the caller (routers.terminal) has already
    decided the connecting principal is trusted for that. `use_key=False`
    authenticates by password: no key is offered, and ssh's own "password:"
    prompt reaches the browser as ordinary terminal output — this module
    never sees the password itself.
    """
    argv = ssh.command(
        host, port, None,
        key_path=ssh.DEFAULT_KEY if use_key else None,
        user=user,
        connect_timeout=ssh.DEFAULT_CONNECT_TIMEOUT_S,
        interactive=True,
    )
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        argv,
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        preexec_fn=os.setsid,
        close_fds=True,
    )
    os.close(slave_fd)
    return TerminalSession(master_fd=master_fd, process=process)
