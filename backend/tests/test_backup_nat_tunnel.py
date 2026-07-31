import os
import json
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_backup_nat_tunnel_db.db"


@pytest.fixture
def db_session():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_backup_nat_tunnel_db.db"):
            os.remove("./test_backup_nat_tunnel_db.db")


# --- resolve_borg_target: pure function, no mocking needed ---

def test_resolve_borg_target_direct_mode():
    from backup_tasks import resolve_borg_target

    extra_args, repo_url = resolve_borg_target(
        orchestrator_behind_nat=False,
        direct_ip="10.0.0.5",
        borg_ssh_port=12345,
    )
    assert extra_args == []
    assert repo_url == "ssh://borg@10.0.0.5:12345/data/borg/fleet"


def test_resolve_borg_target_nat_mode():
    from backup_tasks import resolve_borg_target

    extra_args, repo_url = resolve_borg_target(
        orchestrator_behind_nat=True,
        direct_ip=None,  # unused in NAT mode — must not raise or leak into the URL
        borg_ssh_port=12345,
    )
    assert extra_args == ["-R", "12345:borg-server:22"]
    assert repo_url == "ssh://borg@127.0.0.1:12345/data/borg/fleet"


def test_resolve_borg_target_custom_repo_path():
    from backup_tasks import resolve_borg_target

    _, repo_url = resolve_borg_target(
        orchestrator_behind_nat=True,
        direct_ip=None,
        borg_ssh_port=9999,
        repo_path="/custom/path",
    )
    assert repo_url == "ssh://borg@127.0.0.1:9999/custom/path"


# --- build_borg_create_cmd: extra_ssh_args must not disturb the existing shape ---

def test_build_borg_create_cmd_without_extra_args_is_unchanged():
    """Baseline: omitting extra_ssh_args must produce byte-identical output to
    before this feature existed — this is what every existing caller does."""
    from backup_tasks import build_borg_create_cmd

    cmd = build_borg_create_cmd(
        node_ip="192.168.1.5",
        node_ssh_port=22,
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-archive",
        exclude_str="",
        compression="lz4",
        rate_limit_kib=0,
        checkpoint_secs=1800,
        cpu_quota=None,
        borg_passphrase="secret",
    )
    assert cmd[0] == "ssh"
    assert cmd[7] == "-p"
    assert cmd[9] == "-i"
    assert cmd[10] == "/root/.ssh/id_ed25519"
    assert cmd[11] == "root@192.168.1.5"
    assert "-R" not in cmd


def test_build_borg_create_cmd_with_extra_ssh_args_inserts_before_destination():
    from backup_tasks import build_borg_create_cmd

    cmd = build_borg_create_cmd(
        node_ip="192.168.1.5",
        node_ssh_port=22,
        borg_repo_url="ssh://borg@127.0.0.1:12345/data/borg/fleet",
        archive_name="test-archive",
        exclude_str="",
        compression="lz4",
        rate_limit_kib=0,
        checkpoint_secs=1800,
        cpu_quota=None,
        borg_passphrase="secret",
        extra_ssh_args=["-R", "12345:borg-server:22"],
    )
    # -R flags must land after -i <key> and before root@<node> — anywhere else
    # either breaks ssh's own arg parsing or forwards the wrong session.
    key_idx = cmd.index("/root/.ssh/id_ed25519")
    dest_idx = cmd.index("root@192.168.1.5")
    assert cmd[key_idx + 1] == "-R"
    assert cmd[key_idx + 2] == "12345:borg-server:22"
    assert dest_idx == key_idx + 3


# --- cleanup_locks_and_resolve_ip: NAT mode must skip the reachability probe ---

@patch("tasks.log_to_task")
@patch("subprocess.run")
def test_cleanup_locks_skips_reachability_probe_in_nat_mode(mock_run, mock_log):
    from backup_tasks import cleanup_locks_and_resolve_ip

    mock_run.return_value = MagicMock(returncode=0, stdout="10.0.0.9 5000 10.0.0.1 22\nREACHABLE:no\nOK\n", stderr="")

    result = cleanup_locks_and_resolve_ip(
        task_id="task-1",
        node_ip="10.0.0.9",
        node_ssh_port=22,
        repo_path="/data/borg/fleet",
        borg_passphrase="secret",
        configured_ip="203.0.113.5",  # would normally be tested for reachability
        borg_ssh_port=12345,
        orchestrator_behind_nat=True,
    )

    assert result is None
    # Lock cleanup (the node probe + local break-lock) must still have run.
    assert mock_run.call_count == 2
    node_probe_args = mock_run.call_args_list[0].args[0]
    assert node_probe_args[0] == "ssh"
    local_breaklock_args = mock_run.call_args_list[1].args[0]
    assert local_breaklock_args[:2] == ["borg", "break-lock"]
    # No message claiming the configured IP was tested/used should appear.
    logged_messages = " ".join(str(c.args[1]) for c in mock_log.call_args_list if len(c.args) > 1)
    assert "203.0.113.5" not in logged_messages


@patch("tasks.log_to_task")
@patch("subprocess.run")
def test_cleanup_locks_direct_mode_unchanged(mock_run, mock_log):
    """Regression guard: default (non-NAT) behavior must be untouched."""
    from backup_tasks import cleanup_locks_and_resolve_ip

    mock_run.return_value = MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")

    result = cleanup_locks_and_resolve_ip(
        task_id="task-1",
        node_ip="10.0.0.9",
        node_ssh_port=22,
        repo_path="/data/borg/fleet",
        borg_passphrase="secret",
        configured_ip="203.0.113.5",
        borg_ssh_port=12345,
    )

    assert result == "203.0.113.5"


# --- run_backup_task: full wiring, NAT mode vs. direct mode ---

class _SessionProxy:
    """Wraps a real test session so backup_tasks.SessionLocal() returns it,
    while the code's own db.close() calls don't tear down the shared fixture."""
    def __init__(self, s):
        self.s = s
    def __getattr__(self, name):
        return getattr(self.s, name)
    def close(self):
        pass


def _make_node(db_session, hostname, ip):
    node = models.Node(hostname=hostname, ip_address=ip, ssh_port=22, status="NEEDS_FIX")
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    return node


def _fake_subprocess_run(args, *a, **kw):
    if args[0] == "borg" and args[1] == "break-lock":
        return MagicMock(returncode=0, stdout="", stderr="")
    if args[0] == "ssh":
        last = args[-1]
        if "pkill" in last:
            return MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")
        if "borg init" in last:
            return MagicMock(returncode=0, stdout="", stderr="")
    raise AssertionError(f"unexpected subprocess.run call: {args}")


def _fake_popen(cmd, *a, **kw):
    proc = MagicMock()
    proc.communicate.return_value = (
        json.dumps({"archive": {"stats": {"original_size": 100, "deduplicated_size": 50}}}),
        "",
    )
    proc.returncode = 0
    proc._captured_cmd = cmd
    return proc


@patch("redis.Redis.from_url")
@patch("tasks.log_to_task")
@patch("subprocess.Popen", side_effect=_fake_popen)
@patch("subprocess.run", side_effect=_fake_subprocess_run)
def test_run_backup_task_nat_mode_tunnels_through_node(mock_run, mock_popen, mock_log, mock_redis, db_session, monkeypatch):
    from backup_tasks import run_backup_task
    from celery.app.task import Task

    monkeypatch.setattr("backup_tasks.SessionLocal", lambda: _SessionProxy(db_session))
    mock_redis.return_value = MagicMock()

    settings = db_session.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db_session.add(settings)
    settings.orchestrator_ip = "203.0.113.5"
    settings.orchestrator_behind_nat = True
    settings.borg_ssh_port = 12345
    db_session.commit()

    node = _make_node(db_session, "NAT-NODE", "10.0.0.9")

    mock_request = MagicMock()
    mock_request.id = "test-nat-task-id"
    monkeypatch.setattr(Task, "request", mock_request)

    result = run_backup_task.run(node.id)

    assert result["status"] == "SUCCESS"

    popen_call_args = mock_popen.call_args.args[0]
    assert "-R" in popen_call_args
    r_index = popen_call_args.index("-R")
    assert popen_call_args[r_index + 1] == "12345:borg-server:22"
    inner_cmd = popen_call_args[-1]
    assert "ssh://borg@127.0.0.1:12345/data/borg/fleet" in inner_cmd
    assert "203.0.113.5" not in inner_cmd


@patch("redis.Redis.from_url")
@patch("tasks.log_to_task")
@patch("subprocess.Popen", side_effect=_fake_popen)
@patch("subprocess.run", side_effect=_fake_subprocess_run)
def test_run_backup_task_direct_mode_unchanged(mock_run, mock_popen, mock_log, mock_redis, db_session, monkeypatch):
    """Regression guard: default (non-NAT) backups must not gain a tunnel."""
    from backup_tasks import run_backup_task
    from celery.app.task import Task

    monkeypatch.setattr("backup_tasks.SessionLocal", lambda: _SessionProxy(db_session))
    mock_redis.return_value = MagicMock()

    settings = db_session.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db_session.add(settings)
    settings.orchestrator_ip = "203.0.113.5"
    settings.orchestrator_behind_nat = False
    settings.borg_ssh_port = 12345
    db_session.commit()

    node = _make_node(db_session, "DIRECT-NODE", "10.0.0.10")

    mock_request = MagicMock()
    mock_request.id = "test-direct-task-id"
    monkeypatch.setattr(Task, "request", mock_request)

    result = run_backup_task.run(node.id)

    assert result["status"] == "SUCCESS"

    popen_call_args = mock_popen.call_args.args[0]
    assert "-R" not in popen_call_args
    inner_cmd = popen_call_args[-1]
    assert "ssh://borg@203.0.113.5:12345/data/borg/fleet" in inner_cmd


# --- three-level NAT override: node > group > global ---

class _Obj:
    """Minimal stand-in exposing only orchestrator_behind_nat."""
    def __init__(self, value):
        self.orchestrator_behind_nat = value


@pytest.mark.parametrize("node_val,group_val,global_val,expected", [
    # nothing overridden -> global wins
    (None,  None,  False, False),
    (None,  None,  True,  True),
    # group overrides global, in both directions
    (None,  True,  False, True),
    (None,  False, True,  False),
    # node overrides everything, in both directions
    (True,  False, False, True),
    (False, True,  True,  False),
    # explicit False must not be mistaken for "unset"
    (False, None,  True,  False),
])
def test_resolve_behind_nat_precedence(node_val, group_val, global_val, expected):
    from backup_tasks import resolve_behind_nat
    assert resolve_behind_nat(_Obj(node_val), _Obj(group_val), _Obj(global_val)) is expected


def test_resolve_behind_nat_without_group():
    """A node with no group falls straight through to the global setting."""
    from backup_tasks import resolve_behind_nat
    assert resolve_behind_nat(_Obj(None), None, _Obj(True)) is True
    assert resolve_behind_nat(_Obj(False), None, _Obj(True)) is False
