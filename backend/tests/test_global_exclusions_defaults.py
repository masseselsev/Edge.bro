import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
import models
from main import app

TEST_DATABASE_URL = "sqlite:///./test_global_exclusions_defaults_db.db"


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
        if os.path.exists("./test_global_exclusions_defaults_db.db"):
            os.remove("./test_global_exclusions_defaults_db.db")


@pytest.fixture
def admin_client(db_session):
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db

    from auth import require_admin
    app.dependency_overrides[require_admin] = lambda: models.User(username="test_admin", is_superadmin=True)

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db_session):
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_defaults_endpoint_returns_the_canonical_list(admin_client):
    from core.default_exclusions import DEFAULT_GLOBAL_EXCLUSIONS

    res = admin_client.get("/api/settings/global-exclusions/defaults")
    assert res.status_code == 200
    assert res.json()["global_exclusions"] == DEFAULT_GLOBAL_EXCLUSIONS


def test_defaults_endpoint_requires_admin(anonymous_client):
    res = anonymous_client.get("/api/settings/global-exclusions/defaults")
    assert res.status_code in (401, 403)
