import os
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base, get_db
from main import app

TEST_DATABASE_URL = "sqlite:///./test_notifications_api_db.db"


@pytest.fixture
def db_session():
    if os.path.exists("./test_notifications_api_db.db"):
        os.remove("./test_notifications_api_db.db")
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
        if os.path.exists("./test_notifications_api_db.db"):
            os.remove("./test_notifications_api_db.db")


@pytest.fixture
def admin(db_session):
    user = models.User(username="tester", hashed_password="x", name="Tester",
                       is_superadmin=True, telegram_id="555")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session, admin):
    from routers.users import get_current_auth, require_admin

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[get_current_auth] = lambda: admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_node(db, hostname="node-1"):
    node = models.Node(hostname=hostname, ip_address="10.0.0.1", ssh_port=2222)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def add_alert(db, node, status="OPEN", severity="WATCH"):
    alert = models.Alert(module="thermal", node_id=node.id, dedup_key=f"thermal:{node.id}",
                         severity=severity, status=status, title="Thermal watch",
                         first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def test_list_alerts_returns_node_hostname(client, db_session):
    node = make_node(db_session)
    add_alert(db_session, node)
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["node_hostname"] == "node-1"
    assert body[0]["status"] == "OPEN"


def test_list_alerts_filters_by_status(client, db_session):
    node = make_node(db_session)
    add_alert(db_session, node, status="OPEN")
    add_alert(db_session, node, status="RESOLVED")
    resp = client.get("/api/alerts", params={"status": "RESOLVED"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["status"] == "RESOLVED"


def test_acknowledge_sets_status_and_actor(client, db_session, admin):
    node = make_node(db_session)
    alert = add_alert(db_session, node)
    resp = client.post(f"/api/alerts/{alert.id}/acknowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ACKNOWLEDGED"
    assert body["acknowledged_by"] == "tester"


def test_acknowledge_resolved_alert_is_rejected(client, db_session):
    node = make_node(db_session)
    alert = add_alert(db_session, node, status="RESOLVED")
    resp = client.post(f"/api/alerts/{alert.id}/acknowledge")
    assert resp.status_code == 400


def test_get_preferences_defaults_to_disabled(client):
    resp = client.get("/api/notifications/preferences")
    assert resp.status_code == 200
    assert resp.json() == {"telegram_enabled": False, "min_severity": "WATCH"}


def test_set_preferences_persists(client, db_session, admin):
    resp = client.post("/api/notifications/preferences",
                       json={"telegram_enabled": True, "min_severity": "ALERT"})
    assert resp.status_code == 200
    db_session.refresh(admin)
    assert admin.notification_prefs == {"telegram_enabled": True, "min_severity": "ALERT"}


def test_notification_status_reflects_env_var(client):
    with patch.dict(os.environ, {}, clear=True):
        resp = client.get("/api/notifications/status")
    assert resp.json() == {"telegram_configured": False}
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x"}):
        resp = client.get("/api/notifications/status")
    assert resp.json() == {"telegram_configured": True}


def test_test_endpoint_reports_telegram_failure_reason(client):
    with patch("routers.notifications.telegram.send", return_value=(False, "chat not found")):
        resp = client.post("/api/notifications/test")
    assert resp.status_code == 200
    assert resp.json() == {"success": False, "detail": "chat not found"}


def test_kiosk_principal_is_rejected_from_notification_routes(client, db_session):
    """A Kiosk and a User have independent id sequences, so a Kiosk token
    resolving `current_auth.id` against the users table can collide with an
    unrelated user's account. Every route that reads/writes a User row by
    the caller's identity must reject a Kiosk principal outright.
    """
    from routers.users import get_current_auth

    kiosk = models.Kiosk(id=1, name="k", kiosk_id="k1", key="key1", status="APPROVED")
    app.dependency_overrides[get_current_auth] = lambda: kiosk

    assert client.get("/api/notifications/preferences").status_code == 403
    assert client.post(
        "/api/notifications/preferences",
        json={"telegram_enabled": True, "min_severity": "WATCH"},
    ).status_code == 403
    assert client.post("/api/notifications/test").status_code == 403
