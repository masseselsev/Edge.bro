import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base
import models
from main import app
from database import get_db

from sqlalchemy.pool import StaticPool
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="module")
def db_session():
    if os.path.exists("./test_nodes_pag_db.db"):
        try:
            os.remove("./test_nodes_pag_db.db")
        except Exception:
            pass
    engine = create_engine(
        TEST_DATABASE_URL, 
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_nodes_pag_db.db"):
            os.remove("./test_nodes_pag_db.db")

@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    
    from routers.users import require_kiosk_or_admin
    def override_require_kiosk_or_admin():
        return models.User(username="test_admin", is_superadmin=True)
    app.dependency_overrides[require_kiosk_or_admin] = override_require_kiosk_or_admin
    
    with TestClient(app) as c:
        yield c
        
    app.dependency_overrides.clear()

def test_nodes_pagination_filtering_and_sorting(client, db_session):
    # Create test nodes
    n1 = models.Node(hostname="node-alpha", ip_address="192.168.1.10", status="READY")
    n2 = models.Node(hostname="node-beta", ip_address="192.168.1.11", status="NEEDS_BOOTSTRAP")
    n3 = models.Node(hostname="node-gamma", ip_address="192.168.1.12", status="READY")
    db_session.add_all([n1, n2, n3])
    db_session.commit()
    
    # 1. Simple fetch first page with limit=2
    response = client.get("/api/nodes?page=1&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 2
    assert data["total"] == 3
    assert data["pages"] == 2
    
    # 2. Status filter
    response = client.get("/api/nodes?status=READY")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    
    # 3. Search query
    response = client.get("/api/nodes?q=beta")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["nodes"][0]["hostname"] == "node-beta"


def test_nodes_list_includes_latest_smart_percent_used(client, db_session):
    from datetime import timedelta
    from core.clock import utcnow

    node = models.Node(hostname="node-smart", ip_address="192.168.1.20", status="READY")
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    older = models.SmartSnapshot(
        node_id=node.id, device="sda",
        captured_at=utcnow() - timedelta(days=1),
        percent_used=10.0,
    )
    newest = models.SmartSnapshot(
        node_id=node.id, device="sda",
        captured_at=utcnow(),
        percent_used=42.0,
    )
    db_session.add_all([older, newest])
    db_session.commit()

    response = client.get("/api/nodes?q=node-smart")
    assert response.status_code == 200
    data = response.json()
    assert data["nodes"][0]["smart_percent_used"] == 42.0


def test_a_cidr_block_too_large_to_expand_is_refused():
    """A /16 in the bulk-add box is 65,534 Node rows built inside one request.

    The expansion is eager — a list of address strings — and each becomes a
    row, a ping schedule and a bootstrap attempt. A /8 would be 16 million and
    would take the API process with it.
    """
    from routers.nodes_crud import MAX_EXPANDED_IPS, parse_ip_input

    with pytest.raises(ValueError) as excinfo:
        parse_ip_input("10.0.0.0/16")
    assert "65536" in str(excinfo.value)
    assert str(MAX_EXPANDED_IPS) in str(excinfo.value)


def test_a_site_sized_block_still_expands():
    """The limit must not get in the way of the thing it is guarding."""
    from routers.nodes_crud import parse_ip_input

    ips = parse_ip_input("192.168.1.0/24")
    assert len(ips) == 254
    assert ips[0] == "192.168.1.1"
    assert ips[-1] == "192.168.1.254"


def test_ranges_and_single_addresses_are_unaffected():
    from routers.nodes_crud import parse_ip_input

    assert parse_ip_input("192.168.1.5") == ["192.168.1.5"]
    assert parse_ip_input("192.168.1.5-7") == ["192.168.1.5", "192.168.1.6", "192.168.1.7"]
    assert len(parse_ip_input("192.168.1.10-192.168.1.12")) == 3
