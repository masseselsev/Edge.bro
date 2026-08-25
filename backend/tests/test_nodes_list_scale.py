"""Per-request cost of the node list.

The fleet tab polls this endpoint every five seconds per open browser, so its
cost is multiplied by admins and by page size. Two things used to scale badly:
an uncached os.walk of the entire borg repository on every call, and one or two
Redis round trips per node in the page with no upper bound on page size.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from main import app


@pytest.fixture(scope="module")
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    from routers.users import require_kiosk_or_admin
    app.dependency_overrides[require_kiosk_or_admin] = lambda: models.User(
        username="test_admin", is_superadmin=True
    )
    for i in range(120):
        db_session.add(models.Node(
            hostname=f"scale-{i:04d}",
            ip_address=f"10.50.{i // 256}.{i % 256}",
            status="READY",
        ))
    db_session.commit()

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_limit_is_bounded(client):
    """An unbounded limit let one request fan out over the whole fleet."""
    assert client.get("/api/nodes?limit=100000").status_code == 422
    assert client.get("/api/nodes?limit=0").status_code == 422
    assert client.get("/api/nodes?page=0").status_code == 422
    assert client.get("/api/nodes?limit=200").status_code == 200


def test_repo_size_is_read_from_the_cache_not_walked(client):
    """The handler must go through core.repo_usage, which memoises `du`.

    One measurement per shard, not per node: the figure is fleet-wide and the
    repositories it sums over are a fixed handful, so the cost is flat in fleet
    size — which is the property this guards.
    """
    from core import repo_paths

    with patch("core.repo_usage.repo_size_bytes", return_value=123) as m:
        res = client.get("/api/nodes?limit=50")
    assert res.status_code == 200
    assert m.call_count == repo_paths.SHARD_COUNT, (
        f"repo size resolved {m.call_count} times for one request; expected one "
        f"cached read per shard ({repo_paths.SHARD_COUNT}), independent of fleet size"
    )


def test_repo_walk_is_not_performed_in_the_handler(client):
    """Guards against the os.walk regressing back into the request path."""
    with patch("os.walk") as walk:
        res = client.get("/api/nodes?limit=50")
    assert res.status_code == 200
    assert walk.call_count == 0, "the node list must never walk the repository inline"


def test_redis_lookups_are_batched_per_page(client):
    """One MGET for the page, not one GET per node."""
    with patch("routers.nodes_crud.redis_client") as r:
        r.mget.return_value = [None] * 100
        r.get.return_value = None
        res = client.get("/api/nodes?limit=100")

    assert res.status_code == 200
    assert r.get.call_count == 0, (
        f"{r.get.call_count} per-node Redis GETs; the page should be resolved "
        "with MGET"
    )
    assert r.mget.call_count <= 2, (
        f"{r.mget.call_count} MGETs; expected at most two (running state, "
        "retry schedule)"
    )


def test_batched_and_unbatched_paths_agree(client, db_session):
    """The single-node endpoint takes the unbatched path; results must match."""
    node = db_session.query(models.Node).filter_by(hostname="scale-0003").one()
    listed = next(
        n for n in client.get("/api/nodes?limit=200").json()["nodes"]
        if n["id"] == node.id
    )
    detail = client.get(f"/api/nodes/{node.id}").json()
    for key in (
        "is_backup_running", "current_speed_mbps", "current_speed_limit_mbps",
        "backup_task_id", "next_retry_at",
    ):
        assert listed[key] == detail[key], f"'{key}' differs between batched and single"
