import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from core.alerts import AlertCandidate, sync

TEST_DATABASE_URL = "sqlite:///./test_alerts_db.db"


@pytest.fixture
def db_session():
    if os.path.exists("./test_alerts_db.db"):
        os.remove("./test_alerts_db.db")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_alerts_db.db"):
            os.remove("./test_alerts_db.db")


def candidate(severity="WATCH", key="thermal:1"):
    return AlertCandidate(module="thermal", node_id=1, dedup_key=key,
                           severity=severity, title="Thermal watch: node-1",
                           detail={"theta": 1.9})


def test_new_candidate_opens_an_alert(db_session):
    result = sync(db_session, [candidate()])
    assert len(result.opened) == 1
    assert result.opened[0].status == "OPEN"
    assert result.reopened == []
    assert result.resolved == []
    stored = db_session.query(models.Alert).all()
    assert len(stored) == 1
    assert stored[0].dedup_key == "thermal:1"


def test_repeated_candidate_does_not_duplicate_or_renotify(db_session):
    sync(db_session, [candidate()])
    first_id = db_session.query(models.Alert).one().id

    result = sync(db_session, [candidate()])
    assert result.opened == []
    assert result.reopened == []
    assert result.resolved == []
    stored = db_session.query(models.Alert).all()
    assert len(stored) == 1
    assert stored[0].id == first_id
    assert stored[0].status == "OPEN"


def test_candidate_disappearing_resolves_the_alert(db_session):
    sync(db_session, [candidate()])
    result = sync(db_session, [])
    assert len(result.resolved) == 1
    assert result.resolved[0].status == "RESOLVED"
    assert result.resolved[0].resolved_at is not None


def test_resolved_then_recurring_opens_a_new_row(db_session):
    sync(db_session, [candidate()])
    sync(db_session, [])  # resolves it
    result = sync(db_session, [candidate()])
    assert len(result.opened) == 1
    stored = db_session.query(models.Alert).all()
    assert len(stored) == 2
    assert {row.status for row in stored} == {"RESOLVED", "OPEN"}


def test_acknowledged_alert_at_same_severity_does_not_reopen(db_session):
    sync(db_session, [candidate(severity="WATCH")])
    alert = db_session.query(models.Alert).one()
    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_at = datetime.utcnow()
    db_session.commit()

    result = sync(db_session, [candidate(severity="WATCH")])
    assert result.reopened == []
    assert db_session.query(models.Alert).one().status == "ACKNOWLEDGED"


def test_acknowledged_alert_escalating_reopens_and_clears_ack(db_session):
    sync(db_session, [candidate(severity="WATCH")])
    alert = db_session.query(models.Alert).one()
    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by_id = 7
    db_session.commit()

    result = sync(db_session, [candidate(severity="ALERT")])
    assert len(result.reopened) == 1
    stored = db_session.query(models.Alert).one()
    assert stored.status == "OPEN"
    assert stored.severity == "ALERT"
    assert stored.acknowledged_at is None
    assert stored.acknowledged_by_id is None
