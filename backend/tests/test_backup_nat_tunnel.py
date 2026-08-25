import os
import json
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_backup_nat_tunnel_db.db"


@pytest.fixture(autouse=True)
def repository_is_free(monkeypatch):
    """Nobody else is touching the repository, as far as these tests care.

    What is under test here is NAT tunnelling: which host a backup connects
    through and whether the reachability probe is skipped. The repository lock
    is scenery — but it is scenery backed by Redis, and `writer_in_progress`
    deliberately answers **True** when Redis cannot be read, because guessing
    "nobody" wrongly tears away a live lock and corrupts the repository.

    That is the right production behaviour and the wrong test dependency: with
    no Redis reachable the lock cleanup silently took the "someone else is
    writing" branch, and a test asserting on the cleanup's subprocess calls
    failed for a reason that had nothing to do with NAT. Stated explicitly here
    so these tests exercise the path they are named after.

    `core/tests` around `repo_lock` cover the Redis-unavailable behaviour
    itself.
    """
    import backup_tasks

    monkeypatch.setattr(backup_tasks, "maintenance_in_progress", lambda *a, **k: False)
    monkeypatch.setattr(backup_tasks, "writer_in_progress", lambda *a, **k: False)


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
        repo_path="/data/borg/fleet",
    )
    assert extra_args == []
    assert repo_url == "ssh://borg@10.0.0.5:12345/data/borg/fleet"


def test_resolve_borg_target_nat_mode():
    from backup_tasks import resolve_borg_target

    extra_args, repo_url = resolve_borg_target(
        orchestrator_behind_nat=True,
        direct_ip=None,  # unused in NAT mode — must not raise or leak into the URL
        borg_ssh_port=12345,
        repo_path="/data/borg/fleet",
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


# --- build_borg_create_inner_cmd: the shell fragment spliced into the ---
# --- single locked script (core.node_lock) ssh.command() then wraps.   ---
#
# It used to build the full SSH argv itself (node_ip/port/extra_ssh_args and
# all), one of three separate SSH calls a backup made. Now it only returns
# the inner `bash -c "..."` fragment: cleanup, init and create are spliced
# together and sent as one SSH call so they can share one flock — see
# core.node_lock's docstring. NAT tunnel (-R) positioning is exercised where
# it now actually happens, in test_run_backup_task_nat_mode_tunnels_through_node
# below, which asserts on the single combined ssh.command() call.

def test_build_borg_create_inner_cmd_is_unchanged():
    """Baseline shape: no ssh argv concerns here any more, just the borg
    invocation itself."""
    from backup_tasks import build_borg_create_inner_cmd

    cmd = build_borg_create_inner_cmd(
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-archive",
        exclude_str="",
        compression="lz4",
        rate_limit_kib=0,
        checkpoint_secs=1800,
        cpu_quota=None,
        borg_passphrase="secret",
    )
    assert "BORG_RSH=" in cmd
    assert "borg create" in cmd
    assert "systemd-run" not in cmd


def test_build_borg_create_inner_cmd_with_cpu_quota_wraps_in_systemd_run():
    from backup_tasks import build_borg_create_inner_cmd

    cmd = build_borg_create_inner_cmd(
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-archive",
        exclude_str="",
        compression="lz4",
        rate_limit_kib=0,
        checkpoint_secs=1800,
        cpu_quota=40,
        borg_passphrase="secret",
    )
    assert cmd.startswith("systemd-run --scope -p CPUQuota=40% -- bash -c")


def test_build_borg_create_inner_cmd_waits_for_the_repository_lock():
    """Borg's own default is a one-second wait, which fails the backup instead
    of queueing it. Two nodes sharing a repository must queue, not race."""
    from backup_tasks import build_borg_create_inner_cmd, LOCK_WAIT_SECONDS

    cmd = build_borg_create_inner_cmd(
        borg_repo_url="ssh://borg@192.168.1.1:12345/data/borg/fleet",
        archive_name="test-archive",
        exclude_str="",
        compression="lz4",
        rate_limit_kib=0,
        checkpoint_secs=1800,
        cpu_quota=None,
        borg_passphrase="secret",
    )
    assert f"--lock-wait {LOCK_WAIT_SECONDS}" in cmd
    assert LOCK_WAIT_SECONDS > 1


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
    """Wraps a real test session so every session_scope() in the task returns it,
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
        # The only remaining subprocess.run "ssh" call is the reachability
        # probe — pkill/cache-cleanup and `borg init` now run inside the one
        # locked script sent via subprocess.Popen (see _fake_popen).
        return MagicMock(returncode=0, stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n", stderr="")
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

    monkeypatch.setattr("database.SessionLocal", lambda: _SessionProxy(db_session))
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

    monkeypatch.setattr("database.SessionLocal", lambda: _SessionProxy(db_session))
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


# --- CPU quota override: node > group > default ---

class _CpuObj:
    """Minimal stand-in exposing only cpu_quota / default_cpu_quota."""
    def __init__(self, cpu_quota=None, default_cpu_quota=None):
        self.cpu_quota = cpu_quota
        self.default_cpu_quota = default_cpu_quota


def test_resolve_cpu_quota_inherits_when_node_unset():
    from backup_tasks import resolve_cpu_quota
    node = _CpuObj(cpu_quota=None)
    group = _CpuObj(cpu_quota=60)
    settings = _CpuObj(default_cpu_quota=30)
    assert resolve_cpu_quota(node, group, settings) == (60, "group")


def test_resolve_cpu_quota_falls_through_to_default_with_no_group():
    from backup_tasks import resolve_cpu_quota
    node = _CpuObj(cpu_quota=None)
    settings = _CpuObj(default_cpu_quota=30)
    assert resolve_cpu_quota(node, None, settings) == (30, "default")


def test_resolve_cpu_quota_falls_through_when_group_unset_too():
    from backup_tasks import resolve_cpu_quota
    node = _CpuObj(cpu_quota=None)
    group = _CpuObj(cpu_quota=None)
    settings = _CpuObj(default_cpu_quota=30)
    assert resolve_cpu_quota(node, group, settings) == (30, "default")


def test_resolve_cpu_quota_node_custom_value_wins():
    from backup_tasks import resolve_cpu_quota
    node = _CpuObj(cpu_quota=85)
    group = _CpuObj(cpu_quota=60)
    settings = _CpuObj(default_cpu_quota=30)
    assert resolve_cpu_quota(node, group, settings) == (85, "node")


def test_resolve_cpu_quota_node_zero_means_explicit_unlimited():
    """0 on the node is terminal — unlike upload_rate_limit, it must NOT
    fall through to the group's value."""
    from backup_tasks import resolve_cpu_quota
    node = _CpuObj(cpu_quota=0)
    group = _CpuObj(cpu_quota=60)
    settings = _CpuObj(default_cpu_quota=30)
    assert resolve_cpu_quota(node, group, settings) == (None, "node")
