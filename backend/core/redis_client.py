"""One way to reach Redis, with timeouts that actually let callers recover.

Thirteen modules built their own client, every one of them as a bare
`redis.Redis.from_url(...)` with no socket timeouts. The redis-py default for
both connect and read is **no timeout at all**, which does not mean "fail
fast" — it means block until the kernel gives up, which for a host that
accepts the TCP connection and then never answers is effectively forever.

That turned every `try: ... except Exception: fall back` around a Redis call
into decoration. `routers/nodes_crud._mget` is the clearest case: its docstring
says it tolerates an unavailable Redis, and it cannot, because the call it
guards never returns. `GET /api/nodes` is polled every few seconds by the fleet
tab, so a Redis that is reachable-but-silent — a network partition, a paused
container, a firewall drop, not a clean refusal — parks one worker per poll
until none are left. A crashed Redis was survivable; an unreachable one was
not, which is the wrong way round.

Blocking commands would be the reason to leave `socket_timeout` unset. This
codebase issues none: no BLPOP, no XREAD, no pub/sub listen.
"""
from __future__ import annotations

import os

import redis

#: Inside compose the hostname is the service name. Kept as the default so a
#: container needs no configuration, but every call site now reads it from
#: here rather than repeating the string.
DEFAULT_REDIS_URL = "redis://redis:6379/0"

#: Long enough to survive a slow moment, short enough that a request handler
#: which is merely decorating its result with Redis state gives up while the
#: caller is still waiting rather than after they have gone.
CONNECT_TIMEOUT_SECONDS = float(os.getenv("REDIS_CONNECT_TIMEOUT", "3"))
READ_TIMEOUT_SECONDS = float(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))


def redis_url() -> str:
    """The configured Redis URL. Read per call, so tests can repoint it."""
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


def make_client(url: str | None = None, **overrides) -> redis.Redis:
    """A Redis client that fails instead of hanging.

    Just the two timeouts, deliberately. `retry_on_timeout` and
    `health_check_interval` look like obvious companions and are not: against a
    host that accepts connections and never answers, redis-py's health check
    runs inside connection setup, times out, retries, and re-enters connection
    setup — the traceback is the same six frames repeating. Verified here, not
    reasoned about.

    Retrying belongs to the caller anyway. Every call site in this codebase
    wraps its Redis use in a fallback that treats an exception as "no cached
    state", which is the correct behaviour for all of them: the answer Redis
    holds is a decoration on a response, never the response itself.
    """
    kwargs = {
        "socket_connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "socket_timeout": READ_TIMEOUT_SECONDS,
    }
    kwargs.update(overrides)
    return redis.Redis.from_url(url or redis_url(), **kwargs)
