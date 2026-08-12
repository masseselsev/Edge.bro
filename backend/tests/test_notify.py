import os
from unittest.mock import patch, MagicMock

import models
from core.notify import telegram


def make_user(telegram_id="123456789"):
    return models.User(id=1, username="tester", hashed_password="x", name="Tester",
                       telegram_id=telegram_id)


def make_alert():
    return models.Alert(id=1, module="thermal", severity="WATCH", status="OPEN",
                        title="Thermal interface watch: node-1")


def test_send_fails_without_token():
    with patch.dict(os.environ, {}, clear=True):
        ok, detail = telegram.send(make_user(), make_alert(), "opened")
    assert ok is False
    assert "TELEGRAM_BOT_TOKEN" in detail


def test_send_fails_without_telegram_id():
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
        ok, detail = telegram.send(make_user(telegram_id=None), make_alert(), "opened")
    assert ok is False
    assert "telegram_id" in detail


def test_send_success_calls_the_bot_api():
    fake_response = MagicMock(status_code=200)
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
        with patch("core.notify.telegram.requests.post", return_value=fake_response) as post:
            ok, detail = telegram.send(make_user(), make_alert(), "opened")
    assert ok is True
    assert post.call_args.kwargs["json"]["chat_id"] == "123456789"
    assert "test-token" in post.call_args.args[0]


def test_send_surfaces_the_telegram_error_description():
    fake_response = MagicMock(status_code=400)
    fake_response.json.return_value = {"description": "chat not found"}
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
        with patch("core.notify.telegram.requests.post", return_value=fake_response):
            ok, detail = telegram.send(make_user(), make_alert(), "opened")
    assert ok is False
    assert detail == "chat not found"


def test_send_handles_network_error():
    import requests
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
        with patch("core.notify.telegram.requests.post", side_effect=requests.RequestException("boom")):
            ok, detail = telegram.send(make_user(), make_alert(), "opened")
    assert ok is False
    assert "boom" in detail


def test_send_never_leaks_token_in_network_error():
    """Verify that connection/timeout errors don't leak the token via requests exception strings."""
    import requests
    token = "SUPER_SECRET_TOKEN_12345"
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token}):
        # Simulate a real requests exception that embeds the URL with token
        error_msg = f"Max retries exceeded with url: /bot{token}/sendMessage (Caused by NewConnectionError)"
        with patch("core.notify.telegram.requests.post", side_effect=requests.ConnectionError(error_msg)):
            ok, detail = telegram.send(make_user(), make_alert(), "opened")
    assert ok is False
    assert token not in detail, f"Token leaked in error detail: {detail}"
    assert "network error:" in detail


# Task 6: dispatch tests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from core.alerts import SyncResult
from core.notify import dispatch


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return factory()


def _add_user(db, telegram_enabled=True, min_severity="WATCH", telegram_id="1"):
    user = models.User(
        username=f"u_{telegram_id}", hashed_password="x", name="U", telegram_id=telegram_id,
        notification_prefs={"telegram_enabled": telegram_enabled, "min_severity": min_severity},
    )
    db.add(user)
    db.commit()
    return user


def test_dispatch_skips_users_who_are_not_subscribed():
    db = _db()
    _add_user(db, telegram_enabled=False)
    alert = models.Alert(module="thermal", dedup_key="k", severity="WATCH",
                         status="OPEN", title="t")
    db.add(alert)
    db.commit()
    with patch("core.notify.telegram.send") as send:
        dispatch.notify(db, SyncResult(opened=[alert]))
    send.assert_not_called()


def test_dispatch_respects_min_severity():
    db = _db()
    _add_user(db, min_severity="ALERT")
    alert = models.Alert(module="thermal", dedup_key="k", severity="WATCH",
                         status="OPEN", title="t")
    db.add(alert)
    db.commit()
    with patch("core.notify.telegram.send") as send:
        dispatch.notify(db, SyncResult(opened=[alert]))
    send.assert_not_called()


def test_dispatch_sends_to_subscribed_user_at_or_above_threshold():
    db = _db()
    _add_user(db, min_severity="WATCH")
    alert = models.Alert(module="thermal", dedup_key="k", severity="WATCH",
                         status="OPEN", title="t")
    db.add(alert)
    db.commit()
    with patch("core.notify.telegram.send", return_value=(True, "sent")) as send:
        dispatch.notify(db, SyncResult(opened=[alert]))
    send.assert_called_once()
    assert send.call_args.args[2] == "opened"


def test_dispatch_resolution_bypasses_severity_gate():
    db = _db()
    _add_user(db, min_severity="ALERT")
    alert = models.Alert(module="thermal", dedup_key="k", severity="WATCH",
                         status="RESOLVED", title="t")
    db.add(alert)
    db.commit()
    with patch("core.notify.telegram.send", return_value=(True, "sent")) as send:
        dispatch.notify(db, SyncResult(resolved=[alert]))
    send.assert_called_once()
    assert send.call_args.args[2] == "resolved"


def test_dispatch_one_failing_recipient_does_not_block_others():
    db = _db()
    _add_user(db, telegram_id="1")
    _add_user(db, telegram_id="2")
    alert = models.Alert(module="thermal", dedup_key="k", severity="WATCH",
                         status="OPEN", title="t")
    db.add(alert)
    db.commit()
    with patch("core.notify.telegram.send", side_effect=[(False, "boom"), (True, "sent")]) as send:
        dispatch.notify(db, SyncResult(opened=[alert]))
    assert send.call_count == 2
