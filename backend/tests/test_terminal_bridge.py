from unittest.mock import MagicMock

from core import terminal_bridge


def _patched(monkeypatch):
    monkeypatch.setattr(terminal_bridge.pty, "openpty", lambda: (11, 12))
    fake_popen = MagicMock()
    monkeypatch.setattr(terminal_bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal_bridge.os, "close", lambda fd: None)
    return fake_popen


def test_open_bridge_key_path_uses_orchestrator_key_and_batch_mode_off(monkeypatch):
    fake_popen = _patched(monkeypatch)
    terminal_bridge.open_bridge(host="10.0.0.5", port=22, user="root", use_key=True)
    argv = fake_popen.call_args.args[0]
    assert "-i" in argv
    assert argv[argv.index("-i") + 1] == terminal_bridge.ssh.DEFAULT_KEY
    assert "-tt" in argv
    assert "BatchMode=yes" not in argv


def test_open_bridge_password_path_omits_dash_i(monkeypatch):
    fake_popen = _patched(monkeypatch)
    terminal_bridge.open_bridge(host="10.0.0.5", port=22, user="alice", use_key=False)
    argv = fake_popen.call_args.args[0]
    assert "-i" not in argv
    assert "alice@10.0.0.5" in argv


def test_open_bridge_wires_pty_to_stdio(monkeypatch):
    fake_popen = _patched(monkeypatch)
    terminal_bridge.open_bridge(host="10.0.0.5", port=22, user="root", use_key=True)
    kwargs = fake_popen.call_args.kwargs
    assert kwargs["stdin"] == 12
    assert kwargs["stdout"] == 12
    assert kwargs["stderr"] == 12


def test_session_resize_calls_ioctl(monkeypatch):
    calls = []
    monkeypatch.setattr(terminal_bridge.fcntl, "ioctl", lambda *a: calls.append(a))
    session = terminal_bridge.TerminalSession(master_fd=99, process=MagicMock(poll=lambda: None))
    session.resize(120, 40)
    assert calls[0][0] == 99
    assert calls[0][1] == terminal_bridge.termios.TIOCSWINSZ
