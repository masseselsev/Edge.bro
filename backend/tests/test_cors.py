"""A public website must not be able to drive the orchestrator with a session cookie.

The middleware was configured `allow_origins=["*"]` with
`allow_credentials=True`. That combination is illegal per the fetch spec, and
Starlette resolves it by echoing whatever Origin the request carried — so every
origin was allowed, with credentials. An operator with a live session who
visited any page on the internet could have had a restore triggered or a node
deleted from it.

The replacement allows private-network origins, because the orchestrator is
reached by whichever of its LAN addresses somebody typed and a kiosk reaches
its own backend on localhost. Everything else is refused.
"""
import re

import pytest

from main import PRIVATE_ORIGIN_REGEX

ALLOWED = [
    "http://localhost:7777",
    "https://localhost",
    "http://127.0.0.1:8000",
    "http://10.0.0.21:7777",
    "http://192.168.1.50:7777",
    "http://172.16.4.9:8000",
    "http://172.31.255.254",
]

REFUSED = [
    "https://evil.example.com",
    "http://attacker.test:7777",
    # Public addresses that merely look like the private ranges.
    "http://11.0.0.1:7777",
    "http://172.15.0.1:7777",
    "http://172.32.0.1:7777",
    "http://192.169.1.1:7777",
    # A hostname that embeds an allowed one.
    "http://localhost.evil.example.com",
    "http://10.0.0.1.evil.example.com",
    # Scheme confusion.
    "file://localhost",
]


@pytest.mark.parametrize("origin", ALLOWED)
def test_private_origins_are_allowed(origin):
    assert re.match(PRIVATE_ORIGIN_REGEX, origin), origin


@pytest.mark.parametrize("origin", REFUSED)
def test_public_and_lookalike_origins_are_refused(origin):
    assert not re.match(PRIVATE_ORIGIN_REGEX, origin), origin


def test_the_wildcard_is_gone():
    """Guard the guard: "*" would make every case above pass by accident."""
    from main import app
    from starlette.middleware.cors import CORSMiddleware

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors, "the CORS middleware is not installed at all"

    options = cors[0].kwargs
    assert "*" not in (options.get("allow_origins") or []), (
        "allow_origins contains the wildcard again. With allow_credentials it "
        "makes Starlette echo any Origin back, which is what this replaced."
    )
    assert options.get("allow_credentials") is True, (
        "the session cookie stops working across origins without this; if it "
        "was turned off deliberately, this test needs rewriting rather than "
        "deleting."
    )
