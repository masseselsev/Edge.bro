"""Every SSH call to a node goes through core.ssh.

There were thirteen hand-rolled copies of the invocation. Eleven omitted
`BatchMode=yes`, and that is the reason this file exists rather than a style
preference: without it, an ssh that cannot authenticate prompts, and with no
terminal attached the child waits for input that never comes. Seven of those
eleven ran inside FastAPI request handlers.
"""
import ast
import pathlib

import pytest

from core import ssh

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCLUDED_PARTS = {"venv", "tests", "__pycache__", "alembic"}

#: core/ssh.py is where the literal belongs. payload_client ships to the kiosk
#: and cannot import the orchestrator's modules.
ALLOWED = {"core/ssh.py"}


def _python_sources():
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def test_no_module_builds_its_own_ssh_argv():
    """A list literal starting with "ssh" is a copy of core.ssh.command."""
    offenders = []
    for path in _python_sources():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "ssh":
                offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "These build an ssh command line by hand instead of calling "
        "core.ssh.command, which is how eleven of them ended up without "
        "BatchMode=yes and hung on a password prompt:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_would_notice_a_hand_rolled_command():
    """A guard on the guard, since the whole test is one AST pattern."""
    tree = ast.parse('cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "host", "uptime"]')
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.List) and n.elts
        and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "ssh"
    ]
    assert len(found) == 1


def test_every_command_refuses_to_prompt():
    """BatchMode is not optional on any path. A prompt here is a hang."""
    for kwargs in (
        {},
        {"keepalive": True},
        {"discard_host_key": True},
        {"connect_timeout": None},
        {"extra_args": ["-R", "12345:borg-server:22"]},
    ):
        argv = ssh.command("10.0.0.9", 22, "uptime", **kwargs)
        assert "BatchMode=yes" in argv, kwargs


def test_the_remote_command_is_always_last():
    """Anything appended after it would be read as another destination."""
    argv = ssh.command("10.0.0.9", 2222, "borg create ...", extra_args=["-R", "1:2:3"])
    assert argv[-1] == "borg create ..."
    assert argv[-2] == "root@10.0.0.9"


def test_extra_args_land_before_the_destination():
    """ssh parses options only up to the destination; a -R after it is a host."""
    argv = ssh.command("10.0.0.9", 22, "uptime", extra_args=["-R", "12345:borg-server:22"])
    assert argv.index("-R") < argv.index("root@10.0.0.9")


def test_a_connect_timeout_can_be_waived_but_is_there_by_default():
    """Request handlers need the cap; the backup transfer must not have it."""
    assert f"ConnectTimeout={ssh.DEFAULT_CONNECT_TIMEOUT_S}" in ssh.command("h", 22, "x")
    assert not any(
        a.startswith("ConnectTimeout") for a in ssh.command("h", 22, "x", connect_timeout=None)
    )


def test_only_the_monitoring_probe_discards_the_host_key():
    """Backup and bootstrap record host keys on purpose — see core.known_hosts."""
    assert "UserKnownHostsFile=/dev/null" not in ssh.command("h", 22, "x")
    assert "UserKnownHostsFile=/dev/null" in ssh.command("h", 22, "x", discard_host_key=True)
