"""Delivery adapter contract. A second adapter (e.g. email) implements the
same signature and needs nothing changed in core.notify.dispatch.
"""
from __future__ import annotations

from typing import Protocol, Tuple

import models


class NotificationAdapter(Protocol):
    def send(self, user: "models.User", alert: "models.Alert", kind: str) -> Tuple[bool, str]:
        """kind is one of 'opened', 'reopened', 'resolved'.
        Returns (success, human-readable detail — always present, used for
        both logging on failure and the /notifications/test response).
        """
        ...
