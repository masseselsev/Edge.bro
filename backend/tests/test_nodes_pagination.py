import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base
import models
from main import app
from database import get_db

TEST_DATABASE_URL = "sqlite:///./test_nodes_pag_db.db"

@pytest.fixture(scope="module")
def db_session():
    if os.path.exists("./test_nodes_pag_db.db"):
        try:
            os.remove("./test_nodes_pag_db.db")
        except Exception:
            pass
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
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
