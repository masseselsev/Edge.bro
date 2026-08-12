"""Push-only Telegram delivery. No inbound handling anywhere: no webhook, no
getUpdates polling, no bot commands. The bot can only message a user who has
already started a conversation with it — that is a Telegram platform
restriction, not a choice made here, and the failure message from an
un-started chat is surfaced rather than swallowed.
"""
from __future__ import annotations

import os
import re
from typing import Tuple

import requests

import models

API_BASE = "https://api.telegram.org"

_KIND_LABEL = {"opened": "New", "reopened": "Escalated", "resolved": "Resolved"}


def _scrub_token_from_string(s: str) -> str:
    """Remove any Telegram bot token from a string, redacting /bot<token>/ segments."""
    return re.sub(r'/bot[^/]+/', '/bot***/', s)


def _format_message(alert: "models.Alert", kind: str) -> str:
    if kind == "resolved":
        return f"[Resolved] {alert.title}"
    label = _KIND_LABEL.get(kind, kind)
    return f"[{label}] {alert.severity}: {alert.title}"


def send(user: "models.User", alert: "models.Alert", kind: str) -> Tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return False, "TELEGRAM_BOT_TOKEN is not configured on the server"
    if not user.telegram_id:
        return False, "user has no telegram_id set"

    try:
        response = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={"chat_id": user.telegram_id, "text": _format_message(alert, kind)},
            timeout=10,
        )
    except requests.RequestException as exc:
        scrubbed_error = _scrub_token_from_string(str(exc))
        return False, f"network error: {scrubbed_error}"

    if response.status_code != 200:
        try:
            description = response.json().get("description", response.text)
        except ValueError:
            description = response.text
        return False, description

    return True, "sent"
