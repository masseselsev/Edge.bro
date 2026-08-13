"""Incremental fetch for the task log console.

The console polls once a second while a task runs. Re-sending the whole log
each time is quadratic in its length: a provision producing a few hundred
kilobytes of output transferred hundreds of megabytes over its lifetime.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    from routers.users import require_kiosk_or_admin
    app.dependency_overrides[require_kiosk_or_admin] = lambda: models.User(
        username="admin", is_superadmin=True
    )
    session.add(models.TaskLog(
        id="task-1", task_type="BOOTSTRAP", status="RUNNING",
        log_output="line one\nline two\n",
    ))
    session.commit()
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()


def test_first_poll_returns_the_whole_log(client):
    c, _ = client
    body = c.get("/api/tasks/task-1").json()
    assert body["log_output"] == "line one\nline two\n"
    assert body["log_offset"] == 0
    assert body["log_length"] == len("line one\nline two\n")


def test_subsequent_poll_returns_only_the_new_tail(client):
    c, session = client
    first = c.get("/api/tasks/task-1").json()

    task = session.query(models.TaskLog).one()
    task.log_output += "line three\n"
    session.commit()

    second = c.get(f"/api/tasks/task-1?since={first['log_length']}").json()
    assert second["log_output"] == "line three\n", (
        "expected only the appended text, got the whole log again"
    )
    assert second["log_offset"] == first["log_length"]
    assert second["log_length"] == len("line one\nline two\nline three\n")


def test_splicing_the_tail_reproduces_the_full_log(client):
    c, session = client
    first = c.get("/api/tasks/task-1").json()
    task = session.query(models.TaskLog).one()
    task.log_output += "line three\nline four\n"
    session.commit()

    second = c.get(f"/api/tasks/task-1?since={first['log_length']}").json()
    assert first["log_output"] + second["log_output"] == task.log_output


def test_no_new_output_returns_an_empty_tail(client):
    c, _ = client
    first = c.get("/api/tasks/task-1").json()
    again = c.get(f"/api/tasks/task-1?since={first['log_length']}").json()
    assert again["log_output"] == ""
    assert again["log_length"] == first["log_length"]


def test_offset_past_the_end_resends_everything(client):
    """A restarted task whose log was reset must not leave the client stuck."""
    c, session = client
    task = session.query(models.TaskLog).one()
    task.log_output = "short\n"
    session.commit()

    body = c.get("/api/tasks/task-1?since=9999").json()
    assert body["log_output"] == "short\n"
    assert body["log_offset"] == 0, "a reset log must be re-sent whole"


def test_unknown_task_is_still_404(client):
    c, _ = client
    assert c.get("/api/tasks/nope?since=5").status_code == 404
