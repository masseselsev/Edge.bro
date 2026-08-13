"""No database session is open at the moment a subprocess starts.

`test_session_hygiene.py` checks the shape of the source. This checks the
behaviour: it runs the two worst offenders for real — a backup and an ansible
playbook — with a counting session factory, and asserts the count is zero at
the instant the child process is spawned.

The static check can be satisfied by moving a `SessionLocal()` into a helper;
this one cannot. It is the test that would have caught the original bug, where
`run_backup_task` held one session, in an open transaction, for the entire
multi-hour `borg create`.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base

TEST_DB_PATH = "./test_no_session_across_subprocess.db"


class _SessionCensus:
    """A SessionLocal replacement that counts sessions currently checked out."""

    def __init__(self, factory):
        self._factory = factory
        self.open_count = 0
        self.peak = 0

    def __call__(self):
        census = self

        class _Tracked:
            def __init__(self):
                self._session = census._factory()
                census.open_count += 1
                census.peak = max(census.peak, census.open_count)
                self._closed = False

            def __getattr__(self, name):
                return getattr(self._session, name)

            def close(self):
                if not self._closed:
                    self._closed = True
                    census.open_count -= 1
                self._session.close()

        return _Tracked()


@pytest.fixture
def census(monkeypatch):
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    counter = _SessionCensus(factory)
    monkeypatch.setattr("database.SessionLocal", counter)
    monkeypatch.setattr("tasks.SessionLocal", counter)

    setup = factory()
    try:
        yield counter, setup
    finally:
        setup.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)


def test_the_census_actually_counts():
    """A guard on the guard: a counter stuck at zero would pass everything."""
    engine = create_engine("sqlite://")
    counter = _SessionCensus(sessionmaker(bind=engine))

    session = counter()
    assert counter.open_count == 1
    session.close()
    assert counter.open_count == 0
    # Closing twice must not drive the count negative and mask a real leak.
    session.close()
    assert counter.open_count == 0


def test_backup_holds_no_session_while_borg_runs(census, monkeypatch):
    counter, setup = census
    from celery.app.task import Task

    settings = models.Settings(orchestrator_ip="203.0.113.5", borg_ssh_port=12345)
    setup.add(settings)
    node = models.Node(hostname="CENSUS-NODE", ip_address="10.0.0.9", ssh_port=22, status="NEEDS_FIX")
    setup.add(node)
    setup.commit()
    node_id = node.id

    observed = []

    def spy_run(args, *a, **kw):
        observed.append((counter.open_count, args[0]))
        if args[0] == "ssh":
            return MagicMock(
                returncode=0,
                stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n",
                stderr="",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    def spy_popen(cmd, *a, **kw):
        observed.append((counter.open_count, "borg create"))
        proc = MagicMock()
        proc.stdout = iter([
            json.dumps({"archive": {"stats": {"original_size": 100, "deduplicated_size": 50}}})
        ])
        proc.stderr = iter([])
        proc.returncode = 0
        return proc

    monkeypatch.setattr("celery.app.task.Task.request", MagicMock(id="census-backup"))
    monkeypatch.setattr(Task, "request", MagicMock(id="census-backup"))

    with patch("subprocess.run", side_effect=spy_run), \
         patch("subprocess.Popen", side_effect=spy_popen), \
         patch("redis.Redis.from_url", return_value=MagicMock()):
        from backup_tasks import run_backup_task
        result = run_backup_task.run(node_id)

    assert result["status"] == "SUCCESS"
    assert observed, "the backup never spawned anything — the test proved nothing"

    held = [name for open_count, name in observed if open_count > 0]
    assert not held, (
        f"a session was open while these subprocesses ran: {held}. "
        f"On a real backup that connection stays checked out for hours."
    )


def test_ansible_holds_no_session_while_the_playbook_runs(census, monkeypatch):
    counter, setup = census
    setup.add(models.TaskLog(id="census-play", task_type="BOOTSTRAP", status="RUNNING", log_output=""))
    setup.commit()

    open_at_spawn = []

    def spy_popen(cmd, *a, **kw):
        open_at_spawn.append(counter.open_count)
        proc = MagicMock()
        remaining = ["TASK [Install dependencies] ***\n", "PLAY RECAP ***\n"]
        proc.stdout.readline.side_effect = lambda: remaining.pop(0) if remaining else ""
        proc.poll.side_effect = lambda: None if remaining else 0
        proc.wait.return_value = 0
        return proc

    import ansible_utils
    with patch("subprocess.Popen", side_effect=spy_popen):
        result = ansible_utils.run_ansible_playbook(
            task_id="census-play",
            playbook_name="bootstrap.yml",
            host_ip="1.2.3.4",
            ssh_port=22,
            extra_vars={},
            ssh_key_path="/tmp/nonexistent-key",
        )

    assert result["status"] == "SUCCESS"
    assert open_at_spawn == [0], (
        f"ansible-playbook started with {open_at_spawn} session(s) open; a "
        f"playbook run is minutes long"
    )
    # And the run still leaves nothing behind.
    assert counter.open_count == 0
