"""Growth control for the two log tables.

system_logs and audit_logs had no retention of any kind. Worse, the DB log
handler was attached to `uvicorn.access`, so every HTTP request — including
every poll from an idle browser — became its own session, INSERT and COMMIT.
"""
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import tasks
from database import Base, DBLoggingHandler, setup_db_logging
from tasks.cleanup import prune_log_tables_task
from core.clock import utcnow


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine)


def test_access_logs_are_not_written_to_the_database():
    """One row per HTTP request is not diagnostics, it is a write amplifier."""
    setup_db_logging()
    access = logging.getLogger("uvicorn.access")
    assert not any(isinstance(h, DBLoggingHandler) for h in access.handlers), (
        "uvicorn.access is attached to the DB log handler; every request would "
        "open a session and INSERT a row"
    )


def test_error_logs_are_still_captured():
    """The handler must stay attached where it earns its keep."""
    setup_db_logging()
    for name in ("uvicorn.error", "celery"):
        logger = logging.getLogger(name)
        assert any(isinstance(h, DBLoggingHandler) for h in logger.handlers), (
            f"{name} lost its database log handler"
        )


def test_prune_removes_old_rows_and_keeps_recent_ones(session_factory, monkeypatch):
    monkeypatch.setattr(tasks, "SessionLocal", session_factory)
    db = session_factory()
    now = utcnow()

    db.add(models.SystemLog(level="INFO", message="old", created_at=now - timedelta(days=90)))
    db.add(models.SystemLog(level="INFO", message="new", created_at=now - timedelta(days=1)))
    db.add(models.AuditLog(username="u", action="old", created_at=now - timedelta(days=500)))
    db.add(models.AuditLog(username="u", action="new", created_at=now - timedelta(days=10)))
    db.commit()
    db.close()

    res = prune_log_tables_task()
    assert res["status"] == "SUCCESS"

    check = session_factory()
    assert [r.message for r in check.query(models.SystemLog).all()] == ["new"]
    assert [r.action for r in check.query(models.AuditLog).all()] == ["new"]


def test_audit_logs_are_kept_longer_than_system_logs():
    """They answer different questions and deserve different retention."""
    from tasks.cleanup import _AUDIT_LOG_RETENTION_DAYS, _SYSTEM_LOG_RETENTION_DAYS
    assert _AUDIT_LOG_RETENTION_DAYS > _SYSTEM_LOG_RETENTION_DAYS
