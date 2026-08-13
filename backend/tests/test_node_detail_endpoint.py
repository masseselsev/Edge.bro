"""GET /api/nodes/{node_id} — the single-node read behind the details modal.

The modal used to fetch the paginated node list and search it for the id it
wanted. The list defaults to 50 rows, so on a fleet of any real size most
nodes were simply absent from the response and the modal hung forever on its
loading state. These tests cover the endpoint that replaced that, and in
particular that it is reachable for a node well past the first page.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from main import app

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="module")
def db_session():
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    from routers.users import require_kiosk_or_admin

    def override_auth():
        return models.User(username="test_admin", is_superadmin=True)

    app.dependency_overrides[require_kiosk_or_admin] = override_auth

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seeded_nodes(db_session):
    """A fleet larger than one page of the list endpoint (default limit 50)."""
    nodes = [
        models.Node(
            hostname=f"node-{i:04d}",
            ip_address=f"10.20.{i // 256}.{i % 256}",
            status="READY",
            notes=f"note for {i}",
        )
        for i in range(120)
    ]
    db_session.add_all(nodes)
    db_session.commit()
    return nodes


def test_returns_the_requested_node(client, seeded_nodes):
    target = seeded_nodes[0]
    res = client.get(f"/api/nodes/{target.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == target.id
    assert body["hostname"] == target.hostname
    assert body["ip_address"] == target.ip_address


def test_reaches_a_node_far_past_the_first_list_page(client, seeded_nodes, db_session):
    """The regression this endpoint exists for.

    Node ~100 is nowhere in `GET /api/nodes` at its default page size, which
    is exactly the case that used to wedge the details modal.
    """
    target = seeded_nodes[100]

    listing = client.get("/api/nodes").json()
    assert len(listing["nodes"]) == 50, "default page size changed; update this test"
    assert target.id not in {n["id"] for n in listing["nodes"]}, (
        "test setup is wrong — the target must be absent from page 1"
    )

    res = client.get(f"/api/nodes/{target.id}")
    assert res.status_code == 200
    assert res.json()["hostname"] == target.hostname


def test_unknown_node_is_404_not_a_hang(client):
    res = client.get("/api/nodes/99999")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_literal_history_path_is_not_swallowed_by_the_id_route(client):
    """`/{node_id}` is registered last so it cannot shadow its literal siblings.

    FastAPI matches in registration order; if this route were declared before
    `/history`, that path would bind node_id="history" and 422.
    """
    res = client.get("/api/nodes/history")
    assert res.status_code == 200
    assert "history" in res.json()


def test_detail_matches_the_list_representation(client, seeded_nodes):
    """Both endpoints serialize through the same helper; keep it that way."""
    target = seeded_nodes[1]
    detail = client.get(f"/api/nodes/{target.id}").json()

    listed = next(
        n for n in client.get("/api/nodes?limit=50").json()["nodes"]
        if n["id"] == target.id
    )

    # repo_size_bytes is intentionally not computed per node (it costs a full
    # walk of the shared borg repo and no single-node view shows it).
    ignored = {"repo_size_bytes"}
    assert set(detail) == set(listed), "detail and list disagree on which fields exist"
    for key in set(detail) - ignored:
        assert detail[key] == listed[key], f"field '{key}' differs between detail and list"
