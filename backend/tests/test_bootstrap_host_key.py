"""Bootstrap must forget the node's old SSH host key before it runs.

A (re)install regenerates the node's host keys, and without this the
orchestrator's known_hosts holds a stale entry that makes every backup
afterwards print a full host-identification-changed warning — see
core/known_hosts.py for why.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
import tasks
from core import known_hosts
from database import Base

TEST_DATABASE_URL = "sqlite:///./test_bootstrap_host_key_db.db"


@pytest.fixture
def db_session():
    if os.path.exists("./test_bootstrap_host_key_db.db"):
        os.remove("./test_bootstrap_host_key_db.db")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db, TestingSessionLocal
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_bootstrap_host_key_db.db"):
            os.remove("./test_bootstrap_host_key_db.db")


def make_node(db, hostname="test-node", ip="192.168.100.9", port=2222):
    node = models.Node(hostname=hostname, ip_address=ip, ssh_port=port, status="NEEDS_BOOTSTRAP")
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def stub_out_everything_except_the_host_key_call(monkeypatch, session_local, task_id="host-key-task"):
    monkeypatch.setattr("tasks.SessionLocal", session_local)
    monkeypatch.setattr("tasks.run_ansible_playbook",
                        lambda **kwargs: {"status": "SUCCESS", "parsed_data": {}})
    monkeypatch.setattr("tasks.ensure_orchestrator_ssh_key", lambda: "ssh-ed25519 AAA...")

    class MockRequest:
        id = task_id

    monkeypatch.setattr("celery.app.task.Task.request", MockRequest())


def test_bootstrap_asks_to_forget_the_nodes_own_address_and_port(monkeypatch, db_session):
    db, session_local = db_session
    node = make_node(db, ip="192.168.100.9", port=2222)
    stub_out_everything_except_the_host_key_call(monkeypatch, session_local)

    calls = []
    monkeypatch.setattr(known_hosts, "forget", lambda host, port: calls.append((host, port)) or False)

    tasks.run_bootstrap_task(node_id=node.id, bootstrap_user="root", ssh_password="pwd")

    assert calls == [("192.168.100.9", 2222)]


def test_bootstrap_proceeds_even_if_clearing_the_host_key_fails(monkeypatch, db_session):
    """A failure here must not be able to block provisioning."""
    db, session_local = db_session
    node = make_node(db)
    stub_out_everything_except_the_host_key_call(monkeypatch, session_local)

    played = {"ran": False}

    def fail_to_forget(host, port):
        raise OSError("boom")

    def record_and_succeed(**kwargs):
        played["ran"] = True
        return {"status": "SUCCESS", "parsed_data": {}}

    monkeypatch.setattr(known_hosts, "forget", fail_to_forget)
    monkeypatch.setattr("tasks.run_ansible_playbook", record_and_succeed)

    result = tasks.run_bootstrap_task(node_id=node.id, bootstrap_user="root", ssh_password="pwd")

    assert played.get("ran") is True
    assert result.get("status") != "FAILED" or "boom" not in str(result.get("error", ""))


def test_the_forgotten_entry_is_logged_only_when_something_was_actually_removed(monkeypatch, db_session):
    db, session_local = db_session
    node = make_node(db)
    stub_out_everything_except_the_host_key_call(monkeypatch, session_local, task_id="log-check-task")

    monkeypatch.setattr(known_hosts, "forget", lambda host, port: True)

    tasks.run_bootstrap_task(node_id=node.id, bootstrap_user="root", ssh_password="pwd")

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "log-check-task").first()
    assert log is not None
    assert "Cleared the previous SSH host key" in log.log_output


def test_nothing_is_logged_about_clearing_when_there_was_nothing_to_clear(monkeypatch, db_session):
    db, session_local = db_session
    node = make_node(db)
    stub_out_everything_except_the_host_key_call(monkeypatch, session_local, task_id="quiet-task")

    monkeypatch.setattr(known_hosts, "forget", lambda host, port: False)

    tasks.run_bootstrap_task(node_id=node.id, bootstrap_user="root", ssh_password="pwd")

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "quiet-task").first()
    assert log is not None
    assert "Cleared the previous SSH host key" not in log.log_output
