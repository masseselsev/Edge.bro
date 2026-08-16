"""Session-wide setup that has to happen before anything imports `database`.

Add the backend directory to the path, and — more importantly — make sure the
suite never opens the deployment's own database.

`database.py` builds its engine at import time from `DATABASE_URL`, whose
default points at a local PostgreSQL. Test modules override the `get_db`
dependency, so the requests they make go to their own SQLite file, but the
override does not reach two other paths:

* `@app.on_event("startup")`, which every `TestClient(app)` triggers. It opens
  its own `session_scope()`, seeds settings, and reconciles the fleet's SSH
  grants.
* Anything else calling `SessionLocal()` directly rather than depending on
  `get_db`.

Both therefore ran against whatever real database happened to be listening on
localhost:5432 — on a developer machine, the running deployment. The suite
appeared to pass because that database existed and was migrated; on a machine
without it, 114 tests errored with "relation settings does not exist", and on a
machine *with* it, running the tests wrote to it.

Pointing `DATABASE_URL` at a scratch SQLite file before the first import closes
both paths at once. An explicit `DATABASE_URL` in the environment still wins,
so testing against a real PostgreSQL stays possible when that is the intent.
"""
import os
import sys

# Add the backend directory to the Python path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

#: Scratch database for anything that bypasses the `get_db` override. Test
#: modules keep their own per-module SQLite files for the data they assert on;
#: this one exists so app startup has somewhere harmless to go.
SESSION_DB_PATH = os.path.join(backend_dir, "test_session_scratch.db")

os.environ.setdefault("DATABASE_URL", f"sqlite:///{SESSION_DB_PATH}")

# Redis is reached by URL too, and db 0 is the one a local deployment uses.
# A high-numbered database keeps the suite's keys away from a running stack's
# Celery state, which otherwise shares locks with it.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

# Production waits seconds for Redis because a real request can afford to.
# A test cannot: the endpoints under test decorate their responses with cached
# Redis state and fall back cleanly without it, so where Redis is absent the
# suite should find that out immediately rather than pay the production
# timeout on every call. Tenths of a second is still far longer than a local
# round trip, so a *reachable* Redis behaves exactly as it does in production.
os.environ.setdefault("REDIS_CONNECT_TIMEOUT", "0.3")
os.environ.setdefault("REDIS_SOCKET_TIMEOUT", "0.3")

import pytest  # noqa: E402  - must follow the environment defaults above


@pytest.fixture(scope="session", autouse=True)
def _session_scratch_schema():
    """Give the scratch database its tables, and remove it afterwards.

    App startup queries `settings` before any test fixture has run. Without a
    schema it survives — every block up there is wrapped in try/except — but it
    prints a stack trace per test module, which buries real failures.
    """
    from database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    if os.path.exists(SESSION_DB_PATH):
        os.remove(SESSION_DB_PATH)
