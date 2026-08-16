"""The Redis client has to fail, not hang, and the test suite has to stay local.

Two separate hazards, both found the same way — by running the suite on a host
where the deployment's Docker network had been swallowed by a VPN route, so
Redis and PostgreSQL accepted TCP connections and then never answered.

That is the interesting failure mode. A *stopped* Redis refuses the connection
and every fallback in this codebase handles it. A Redis that is merely
unreachable answers nothing, and with redis-py's default of no socket timeout
the call never returns — so the `except Exception` written to tolerate exactly
that never runs.
"""
import os

import pytest

from core import redis_client


def test_both_timeouts_are_set():
    """The default is no timeout at all, which is not "fail fast"."""
    client = redis_client.make_client("redis://localhost:6379/15")
    kwargs = client.connection_pool.connection_kwargs

    assert kwargs.get("socket_connect_timeout") == redis_client.CONNECT_TIMEOUT_SECONDS
    assert kwargs.get("socket_timeout") == redis_client.READ_TIMEOUT_SECONDS
    assert kwargs["socket_connect_timeout"] > 0
    assert kwargs["socket_timeout"] > 0


def test_health_checks_and_retries_stay_off():
    """The combination that turned a hang into a worse hang.

    `retry_on_timeout` with `health_check_interval` looks like the obvious
    hardening and is not: redis-py runs the health check inside connection
    setup, so a timeout there retries, re-enters setup, and loops — the same
    six frames repeating instead of an exception reaching the caller.

    Retrying belongs to the caller, and every call site here already treats a
    Redis error as "no cached state", which is the right answer for all of
    them: what Redis holds decorates a response, it is never the response.
    """
    kwargs = redis_client.make_client(
        "redis://localhost:6379/15"
    ).connection_pool.connection_kwargs

    assert not kwargs.get("retry_on_timeout"), (
        "retry_on_timeout re-enters connection setup on a silent host"
    )
    assert not kwargs.get("health_check_interval"), (
        "health_check_interval runs inside connection setup and recurses with it"
    )


def test_the_url_is_read_at_call_time(monkeypatch):
    """Read per call, not captured at import, so tests can repoint it."""
    monkeypatch.setenv("REDIS_URL", "redis://example.invalid:6379/7")
    assert redis_client.redis_url() == "redis://example.invalid:6379/7"

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert redis_client.redis_url() == redis_client.DEFAULT_REDIS_URL


def test_overrides_win_over_the_defaults():
    client = redis_client.make_client(
        "redis://localhost:6379/15", socket_timeout=0.25
    )
    assert client.connection_pool.connection_kwargs["socket_timeout"] == 0.25


@pytest.mark.parametrize(
    "module_name",
    [
        "routers.nodes_crud",
        "routers.nodes_actions",
        "routers.iso",
        "core.scheduler",
        "core.repo_lock",
        "tasks",
    ],
)
def test_module_level_clients_all_carry_timeouts(module_name):
    """Thirteen modules built their own client; none had a timeout.

    They go through the shared factory now. This fails if a new one is added
    the old way, which is how the original thirteen accumulated.
    """
    import importlib

    module = importlib.import_module(module_name)
    client = getattr(module, "redis_client", None)
    assert client is not None, f"{module_name} no longer exposes redis_client"

    kwargs = client.connection_pool.connection_kwargs
    assert kwargs.get("socket_timeout"), f"{module_name} has an untimed Redis client"
    assert kwargs.get("socket_connect_timeout"), (
        f"{module_name} has no connect timeout"
    )


def test_the_suite_is_not_pointed_at_a_live_deployment():
    """The suite must not open the database a deployment is using.

    `database.py` builds its engine at import time, and app startup — which
    every `TestClient` triggers — opens its own session outside the `get_db`
    override. Both therefore used whatever was listening on localhost:5432. On
    a developer machine that is the running deployment, and startup does not
    only read it: it seeds settings and rewrites the fleet's SSH grants.

    `tests/conftest.py` points DATABASE_URL at a scratch SQLite file before the
    first import. This is the guard on that guard.
    """
    from database import DATABASE_URL

    assert DATABASE_URL.startswith("sqlite"), (
        f"the test suite is connected to {DATABASE_URL.split('@')[-1]} — "
        "app startup writes to it"
    )
