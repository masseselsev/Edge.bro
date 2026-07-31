import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
import main


@pytest.fixture
def db_session():
    """In-memory SQLite session, fresh per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_new_settings_row_seeds_orchestrator_ip_from_env(db_session, monkeypatch):
    """A fresh install must show the .env value in Settings, not an empty default."""
    monkeypatch.setenv("ORCHESTRATOR_IP", "10.20.30.40")

    settings = models.Settings()
    db_session.add(settings)
    db_session.commit()

    assert settings.orchestrator_ip == "10.20.30.40"


def test_new_settings_row_without_env_stays_empty(db_session, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_IP", raising=False)

    settings = models.Settings()
    db_session.add(settings)
    db_session.commit()

    assert settings.orchestrator_ip == ""


def test_upgrade_backfills_empty_orchestrator_ip(db_session, monkeypatch):
    """Existing deployments created before seeding get the .env value on startup."""
    monkeypatch.delenv("ORCHESTRATOR_IP", raising=False)
    settings = models.Settings()
    db_session.add(settings)
    db_session.commit()
    assert settings.orchestrator_ip == ""

    monkeypatch.setenv("ORCHESTRATOR_IP", "10.20.30.40")
    main.upgrade_settings(db_session)

    assert settings.orchestrator_ip == "10.20.30.40"


def test_upgrade_does_not_overwrite_user_configured_ip(db_session, monkeypatch):
    """A value set through the UI wins over .env — the DB is the source of truth."""
    monkeypatch.setenv("ORCHESTRATOR_IP", "10.20.30.40")
    settings = models.Settings(orchestrator_ip="192.168.5.5")
    db_session.add(settings)
    db_session.commit()

    main.upgrade_settings(db_session)

    assert settings.orchestrator_ip == "192.168.5.5"
