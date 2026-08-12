import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

import models
import tasks
from database import Base
from tasks.alerts import evaluate_alerts_task, LOCK_KEY

TEST_DATABASE_URL = "sqlite:///./test_alerts_task_db.db"


@pytest.fixture
def db_session(monkeypatch):
    if os.path.exists("./test_alerts_task_db.db"):
        os.remove("./test_alerts_task_db.db")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_alerts_task_db.db"):
            os.remove("./test_alerts_task_db.db")


@pytest.fixture(autouse=True)
def clear_lock():
    tasks.redis_client.delete(LOCK_KEY)
    yield
    tasks.redis_client.delete(LOCK_KEY)


def make_node(db):
    node = models.Node(hostname="node-1", ip_address="10.0.0.1", ssh_port=2222,
                       cpu_info="test-cpu")
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def test_new_bad_smart_snapshot_produces_one_open_alert(db_session):
    node = make_node(db_session)
    snapshot = models.SmartSnapshot(
        node_id=node.id, captured_at=datetime.utcnow(), device="/dev/sda",
        protocol="SATA", model="Test", health_passed=False, temperature_c=40,
        power_on_hours=1, written_bytes=1, percent_used=1.0, score=10,
        grade="REPLACE", subscores=[], overrides=[], advisories=[],
    )
    db_session.add(snapshot)
    db_session.commit()

    with patch("core.notify.dispatch.notify") as notify_mock:
        result = evaluate_alerts_task()

    assert result["status"] == "SUCCESS"
    assert result["opened"] == 1
    notify_mock.assert_called_once()
    stored = db_session.query(models.Alert).all()
    assert len(stored) == 1
    assert stored[0].severity == "ALERT"


def test_second_sweep_with_no_changes_opens_nothing_new(db_session):
    node = make_node(db_session)
    snapshot = models.SmartSnapshot(
        node_id=node.id, captured_at=datetime.utcnow(), device="/dev/sda",
        protocol="SATA", model="Test", health_passed=False, temperature_c=40,
        power_on_hours=1, written_bytes=1, percent_used=1.0, score=10,
        grade="REPLACE", subscores=[], overrides=[], advisories=[],
    )
    db_session.add(snapshot)
    db_session.commit()

    with patch("core.notify.dispatch.notify"):
        evaluate_alerts_task()
        result = evaluate_alerts_task()

    assert result["opened"] == 0
    assert db_session.query(models.Alert).count() == 1


def test_a_broken_source_does_not_stop_the_sweep(db_session):
    node = make_node(db_session)
    snapshot = models.SmartSnapshot(
        node_id=node.id, captured_at=datetime.utcnow(), device="/dev/sda",
        protocol="SATA", model="Test", health_passed=False, temperature_c=40,
        power_on_hours=1, written_bytes=1, percent_used=1.0, score=10,
        grade="REPLACE", subscores=[], overrides=[], advisories=[],
    )
    db_session.add(snapshot)
    db_session.commit()

    def broken(db):
        raise RuntimeError("boom")

    with patch("core.notify.dispatch.notify"):
        with patch.dict("core.alert_sources.SOURCES", {"thermal": broken}):
            result = evaluate_alerts_task()

    assert result["status"] == "SUCCESS"
    assert result["opened"] == 1  # smart still ran


def test_sweep_is_skipped_while_lock_is_held(db_session):
    tasks.redis_client.setex(LOCK_KEY, 300, "1")
    result = evaluate_alerts_task()
    assert result["status"] == "SKIPPED"
