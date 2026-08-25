"""Pressing Backup twice on one node.

The manual trigger dispatched unconditionally: nothing asked whether the node
was already backing up. A second press queued a second `borg create` for the
same node, and since backups bound for one repository serialise on borg's
lock, the newcomer sat out `--lock-wait` behind a transfer it had itself
duplicated.

The scheduler has known better for a while — it counts running backups before
dispatching — but the button bypassed it entirely.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base, get_db
from main import app
from routers import nodes_actions

TEST_DATABASE_URL = "sqlite:///./test_manual_backup_guard.db"


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
        engine.dispose()
        if os.path.exists("./test_manual_backup_guard.db"):
            os.remove("./test_manual_backup_guard.db")


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    from routers.users import require_admin

    def override_require_admin():
        return models.User(username="test_admin", is_superadmin=True)

    app.dependency_overrides[require_admin] = override_require_admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def node(db_session):
    n = models.Node(hostname="node-1", ip_address="192.168.1.101", status="READY")
    db_session.add(n)
    db_session.commit()
    db_session.refresh(n)
    return n


def test_a_node_that_is_not_backing_up_starts_one(client, node, monkeypatch):
    monkeypatch.setattr(nodes_actions.scheduler, "is_backup_lock_live", lambda *a, **k: False)
    dispatched = []
    monkeypatch.setattr(
        nodes_actions.run_backup_task, "delay",
        lambda node_id, **kw: dispatched.append(node_id) or type("T", (), {"id": "t-1"})(),
    )

    res = client.post(f"/api/nodes/{node.id}/backup")

    assert res.status_code == 200
    assert dispatched == [node.id]


def test_a_second_press_while_one_is_running_does_not_start_another(
    client, node, monkeypatch
):
    monkeypatch.setattr(nodes_actions.scheduler, "is_backup_lock_live", lambda *a, **k: True)
    dispatched = []
    monkeypatch.setattr(
        nodes_actions.run_backup_task, "delay",
        lambda node_id, **kw: dispatched.append(node_id) or type("T", (), {"id": "t-2"})(),
    )

    res = client.post(f"/api/nodes/{node.id}/backup")

    assert res.status_code == 409
    assert dispatched == [], "a duplicate run would only queue behind the first"
