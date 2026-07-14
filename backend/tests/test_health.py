import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base
import models
from main import app
from database import get_db

TEST_DATABASE_URL = "sqlite:///./test_health_db.db"

@pytest.fixture(scope="module")
def db_session():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_health_db.db"):
            os.remove("./test_health_db.db")

@pytest.fixture(scope="module")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    
    from routers.users import require_admin
    def override_require_admin():
        return models.User(username="test_admin", is_superadmin=True)
    app.dependency_overrides[require_admin] = override_require_admin
    
    with TestClient(app) as c:
        yield c
        
    app.dependency_overrides.clear()

def test_health_check_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "warnings" in data
