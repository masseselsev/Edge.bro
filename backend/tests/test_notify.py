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
