"""Per-request cost of the statistics endpoints.

`/api/stats` is polled by the Archives header and used to load every
backup_history row to produce five integers. `/api/stats/insights` accepted a
`days` window and then applied it in Python, after fetching all of history.
Both now aggregate in the database; these tests keep it that way.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from main import app
from core.clock import utcnow


@pytest.fixture(scope="module")
def engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=e)
    return e


@pytest.fixture(scope="module")
def db_session(engine):
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    from routers.users import require_admin
    app.dependency_overrides[require_admin] = lambda: models.User(
        username="admin", is_superadmin=True
    )

    now = utcnow()
    for n in range(20):
        node = models.Node(hostname=f"s{n}", ip_address=f"10.60.0.{n}", status="READY")
        db_session.add(node)
        db_session.flush()
        # 5 rows inside a 30-day window, 40 far outside it.
        for d in list(range(0, 25, 5)) + list(range(200, 600, 10)):
            db_session.add(models.BackupHistory(
                node_id=node.id,
                archive_name=f"s{n}-{d}",
                timestamp=now - timedelta(days=d),
                original_size=1_000_000,
                deduplicated_size=100_000,
                status="SUCCESS" if d % 3 else "FAILED",
            ))
    db_session.commit()

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class SqlCapture:
    """Records the SELECTs issued against backup_history.

    Row counts are not usable here — SQLite reports rowcount -1 for SELECTs —
    so these tests assert on the shape of the SQL instead, which is the more
    direct statement of the property anyway: is the work happening in the
    database, or is it being done in Python after fetching everything?
    """

    def __init__(self, engine):
        self.engine = engine
        self.statements = []

    def __enter__(self):
        @event.listens_for(self.engine, "before_cursor_execute")
        def _before(conn, cursor, statement, params, context, executemany):
            if "backup_history" in statement.lower():
                self.statements.append(" ".join(statement.split()))

        self._h = _before
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._h)
        return False

    @property
    def unfiltered_selects(self):
        """SELECTs pulling detail columns with no WHERE clause at all."""
        return [
            s for s in self.statements
            if s.upper().startswith("SELECT")
            and " WHERE " not in s.upper()
            and " GROUP BY " not in s.upper()
            and "COUNT(" not in s.upper()
            and "SUM(" not in s.upper()
            and "MAX(" not in s.upper()
        ]


def test_global_stats_aggregates_in_sql(client, engine, db_session):
    """The polled header endpoint must not materialise the history table."""
    total_rows = db_session.query(models.BackupHistory).count()
    assert total_rows >= 900, "fixture should have plenty of history to scan"

    with SqlCapture(engine) as cap:
        res = client.get("/api/stats")
    assert res.status_code == 200
    assert cap.statements, "expected the endpoint to touch backup_history"
    assert not cap.unfiltered_selects, (
        "this endpoint fetched raw history rows instead of aggregating:\n  "
        + "\n  ".join(s[:160] for s in cap.unfiltered_selects)
    )
    assert any(
        "SUM(" in s.upper() or "COUNT(" in s.upper() for s in cap.statements
    ), "expected the totals to be computed with SQL aggregates"


def test_insights_applies_the_window_in_sql(client, engine):
    """`days` must filter in the database, not after fetching everything."""
    with SqlCapture(engine) as cap:
        assert client.get("/api/stats/insights?days=30").status_code == 200

    assert not cap.unfiltered_selects, (
        "insights fetched the whole history table; the window must be a "
        "predicate, not a Python filter:\n  "
        + "\n  ".join(s[:160] for s in cap.unfiltered_selects)
    )

    detail = [
        s for s in cap.statements
        if "avg_speed_mbps" in s.lower() or "duration_seconds" in s.lower()
    ]
    assert detail, "expected a query for the in-window detail rows"
    assert all("timestamp >=" in s.lower() or "timestamp >" in s.lower() for s in detail), (
        "the in-window row fetch carries no timestamp bound:\n  "
        + "\n  ".join(s[:200] for s in detail)
    )


def test_insights_totals_still_reflect_the_window(client):
    narrow = client.get("/api/stats/insights?days=30").json()
    wide = client.get("/api/stats/insights?days=365").json()
    assert narrow["reliability"]["total_runs"] < wide["reliability"]["total_runs"]
    assert narrow["window_days"] == 30


def test_lifetime_figures_are_not_truncated_by_the_window(client):
    """Capacity contribution is a lifetime number and must ignore `days`."""
    narrow = client.get("/api/stats/insights?days=1").json()["capacity"]
    wide = client.get("/api/stats/insights?days=365").json()["capacity"]
    assert narrow["top_consumers"], "expected consumers even with a 1-day window"
    assert (
        [c["bytes"] for c in narrow["top_consumers"]]
        == [c["bytes"] for c in wide["top_consumers"]]
    ), "lifetime contribution changed with the analysis window"
