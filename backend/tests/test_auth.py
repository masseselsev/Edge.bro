import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from unittest.mock import patch

TEST_DATABASE_URL = "sqlite:///./test_auth_db.db"

@pytest.fixture(scope="function")
def db_session():
    """
    Creates an in-memory SQLite database session for unit testing auth schemas.
    """
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_auth_db.db"):
            os.remove("./test_auth_db.db")

def test_superadmin_seeding(db_session):
    """
    Verify that the superadmin is successfully seeded on startup if not present,
    and skipped if already present.
    """
    from main import seed_superadmin
    from routers.users import verify_password

    # 1. Seeding when DB is empty
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "testadmin", "ADMIN_PASSWORD": "supersecurepassword"}):
        seed_superadmin(db_session)
        
    db_session.expire_all()
    user = db_session.query(models.User).filter(models.User.is_superadmin == True).first()
    assert user is not None
    assert user.username == "testadmin"
    assert user.name == "Super Administrator"
    assert verify_password("supersecurepassword", user.hashed_password)

    # 2. Seeding again when DB is NOT empty (should not overwrite or duplicate)
    original_id = user.id
    user.name = "Custom Name"
    db_session.commit()

    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "testadmin", "ADMIN_PASSWORD": "newpassword"}):
        seed_superadmin(db_session)

    db_session.expire_all()
    users = db_session.query(models.User).filter(models.User.is_superadmin == True).all()
    assert len(users) == 1
    assert users[0].id == original_id
    assert users[0].name == "Custom Name" # preserved
    assert not verify_password("newpassword", users[0].hashed_password) # password was not updated
    assert verify_password("supersecurepassword", users[0].hashed_password)


@pytest.fixture(scope="function")
def client(db_session):
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db
    
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_auth_login_logout_flow(client, db_session):
    """
    Verify login creates a session and cookie, GET /api/auth/me resolves current profile,
    and logout clears the session.
    """
    from main import seed_superadmin
    
    # Seed superadmin
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "q1w2e3r4"}):
        seed_superadmin(db_session)

    # 1. Login with invalid credentials should fail
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert resp.status_code == 401

    # 2. Login with valid credentials should succeed
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "q1w2e3r4"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "admin_session" in client.cookies

    # 3. GET /api/auth/me should succeed with the session cookie
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["username"] == "admin"
    assert profile["is_superadmin"] is True
    assert profile["name"] == "Super Administrator"

    # 4. Logout should clear the cookie
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert "admin_session" not in client.cookies or client.cookies.get("admin_session") == ""

    # 5. GET /api/auth/me should now fail (unauthenticated)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_profile_update(client, db_session):
    """
    Verify that an authenticated user can update their own profile fields.
    """
    from main import seed_superadmin
    
    # Seed superadmin
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "q1w2e3r4"}):
        seed_superadmin(db_session)

    # Login
    client.post("/api/auth/login", json={"username": "admin", "password": "q1w2e3r4"})

    # Update profile
    update_data = {
        "name": "Updated Admin Name",
        "phone": "+1234567890",
        "telegram_id": "tg_admin_test",
        "password": "newsecurepassword"
    }
    resp = client.put("/api/users/profile", json=update_data)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Admin Name"

    # Logout and login with new password
    client.post("/api/auth/logout")
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "newsecurepassword"})
    assert resp.status_code == 200


def test_endpoint_access_control(client, db_session):
    """
    Test API authorization matrix: admin-only endpoints, kiosk-or-admin endpoints,
    public endpoints.
    """
    from main import seed_superadmin
    import models

    # 1. Seed superadmin and regular admin
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "superadmin", "ADMIN_PASSWORD": "superpassword"}):
        seed_superadmin(db_session)
    
    admin = models.User(username="regular_admin", hashed_password="hashed_placeholder", name="Regular Admin", is_superadmin=False)
    db_session.add(admin)
    
    # 2. Seed approved kiosk
    kiosk = models.Kiosk(name="Approved Kiosk", uuid="kiosk-uuid", key="ABCD-1234", status="APPROVED", auth_token="kiosk-token")
    db_session.add(kiosk)
    db_session.commit()

    # Public endpoint (/api/version) - should allow anyone
    resp = client.get("/api/version")
    assert resp.status_code == 200

    # Kiosk-or-admin endpoint (/api/nodes) - public access should be rejected (401)
    resp = client.get("/api/nodes")
    assert resp.status_code == 401

    # Kiosk-or-admin endpoint (/api/nodes) - kiosk access should be allowed (200)
    resp = client.get("/api/nodes", headers={"Authorization": "Bearer kiosk-token"})
    assert resp.status_code == 200

    # Admin-only endpoint (/api/settings) - kiosk access should be forbidden (403)
    resp = client.get("/api/settings", headers={"Authorization": "Bearer kiosk-token"})
    assert resp.status_code == 403

    # Admin-only endpoint (/api/settings) - public access should be rejected (401)
    resp = client.get("/api/settings")
    assert resp.status_code == 401

    # Login to get admin session
    client.post("/api/auth/login", json={"username": "superadmin", "password": "superpassword"})

    # Admin-only endpoint (/api/settings) - admin session should be allowed (200)
    resp = client.get("/api/settings")
    assert resp.status_code == 200


def test_admin_crud(client, db_session):
    """
    Test Superadmin User CRUD endpoints: GET/POST/PUT/DELETE /api/users.
    """
    from main import seed_superadmin
    import models

    # Seed superadmin
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "superadmin", "ADMIN_PASSWORD": "superpassword"}):
        seed_superadmin(db_session)

    # 1. Accessing CRUD endpoints as public should fail
    resp = client.get("/api/users")
    assert resp.status_code == 401

    # 2. Accessing CRUD endpoints as standard admin should fail (403)
    standard_admin = models.User(username="std_admin", hashed_password="hashed_placeholder", name="Standard Admin", is_superadmin=False)
    db_session.add(standard_admin)
    db_session.commit()

    # Login as standard admin
    # We'll mock password verification or temporarily seed with a known hash
    from routers.users import get_password_hash
    standard_admin.hashed_password = get_password_hash("stdpassword")
    db_session.commit()

    client.post("/api/auth/login", json={"username": "std_admin", "password": "stdpassword"})
    
    resp = client.get("/api/users")
    assert resp.status_code == 403

    resp = client.post("/api/users", json={"username": "new_admin", "password": "newpassword", "name": "New Admin"})
    assert resp.status_code == 403

    # Logout standard admin
    client.post("/api/auth/logout")

    # 3. Accessing CRUD endpoints as superadmin should succeed
    client.post("/api/auth/login", json={"username": "superadmin", "password": "superpassword"})

    # GET /api/users
    resp = client.get("/api/users")
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) == 2 # superadmin and std_admin

    # POST /api/users (Create new admin)
    create_payload = {
        "username": "new_admin",
        "name": "New Administrator",
        "phone": "+111222333",
        "telegram_id": "new_admin_tg",
        "password": "newadminpassword",
        "comment": "New administrator comment"
    }
    resp = client.post("/api/users", json=create_payload)
    assert resp.status_code == 201
    created_user = resp.json()
    assert created_user["username"] == "new_admin"
    assert created_user["name"] == "New Administrator"
    assert created_user["comment"] == "New administrator comment"

    # Try duplicate username - should fail (400)
    resp = client.post("/api/users", json=create_payload)
    assert resp.status_code == 400

    # PUT /api/users/{user_id} (Update user)
    new_user_id = created_user["id"]
    update_payload = {
        "name": "Updated Administrator Name",
        "comment": "Updated comment"
    }
    resp = client.put(f"/api/users/{new_user_id}", json=update_payload)
    assert resp.status_code == 200
    updated_user = resp.json()
    assert updated_user["name"] == "Updated Administrator Name"
    assert updated_user["comment"] == "Updated comment"

    # DELETE /api/users/{user_id} (Delete user)
    resp = client.delete(f"/api/users/{new_user_id}")
    assert resp.status_code == 204

    # Verify deleted
    resp = client.get("/api/users")
    users = resp.json()
    assert not any(u["id"] == new_user_id for u in users)

    # Superadmin cannot delete themselves
    # Get superadmin user ID
    superadmin_id = [u["id"] for u in users if u["username"] == "superadmin"][0]
    resp = client.delete(f"/api/users/{superadmin_id}")
    assert resp.status_code == 400


def test_admin_plus_crud(client, db_session):
    """
    Test Admin+ role permissions and CRUD capabilities.
    """
    from main import seed_superadmin
    import models
    from routers.users import get_password_hash

    # Seed superadmin
    with patch.dict(os.environ, {"SUPERADMIN_USERNAME": "superadmin", "ADMIN_PASSWORD": "superpassword"}):
        seed_superadmin(db_session)

    # 1. Create two admin+ users and a standard admin user
    admin_plus_pwd = get_password_hash("adminpluspwd")
    std_admin_pwd = get_password_hash("stdadminpwd")
    
    admin_plus = models.User(
        username="admin_plus", 
        hashed_password=admin_plus_pwd, 
        name="Admin Plus User", 
        is_superadmin=False,
        is_admin_plus=True
    )
    another_admin_plus = models.User(
        username="another_admin_plus",
        hashed_password=admin_plus_pwd,
        name="Another Admin Plus",
        is_superadmin=False,
        is_admin_plus=True
    )
    std_admin = models.User(
        username="std_admin", 
        hashed_password=std_admin_pwd, 
        name="Standard Admin User", 
        is_superadmin=False,
        is_admin_plus=False
    )
    db_session.add_all([admin_plus, another_admin_plus, std_admin])
    db_session.commit()

    # Login as admin_plus
    resp = client.post("/api/auth/login", json={"username": "admin_plus", "password": "adminpluspwd"})
    assert resp.status_code == 200
    assert resp.json()["is_admin_plus"] is True

    # admin+ can GET /api/users
    resp = client.get("/api/users")
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) == 4 # superadmin, admin_plus, another_admin_plus, std_admin

    # admin+ can create a standard admin
    resp = client.post("/api/users", json={
        "username": "new_std_admin",
        "name": "New Std Admin",
        "password": "somepassword",
        "is_admin_plus": False
    })
    assert resp.status_code == 201
    created_id = resp.json()["id"]

    # admin+ CANNOT create another admin+
    resp = client.post("/api/users", json={
        "username": "yet_another_admin_plus",
        "name": "Yet Another Admin Plus",
        "password": "somepassword",
        "is_admin_plus": True
    })
    assert resp.status_code == 403

    # admin+ can modify standard admin
    resp = client.put(f"/api/users/{created_id}", json={"name": "Modified Std Admin"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Modified Std Admin"

    # admin+ CANNOT modify superadmin
    superadmin_id = db_session.query(models.User).filter(models.User.username == "superadmin").first().id
    resp = client.put(f"/api/users/{superadmin_id}", json={"name": "Malicious Super Name"})
    assert resp.status_code == 403

    # admin+ CANNOT modify another admin+ (including self through CRUD endpoint)
    admin_plus_id = admin_plus.id
    resp = client.put(f"/api/users/{admin_plus_id}", json={"name": "New Self Name"})
    assert resp.status_code == 403

    resp = client.put(f"/api/users/{another_admin_plus.id}", json={"name": "New Other Name"})
    assert resp.status_code == 403

    # admin+ CANNOT promote standard admin to admin+
    resp = client.put(f"/api/users/{created_id}", json={"is_admin_plus": True})
    assert resp.status_code == 403

    # admin+ CANNOT delete superadmin
    resp = client.delete(f"/api/users/{superadmin_id}")
    assert resp.status_code == 403

    # admin+ CANNOT delete other admin+
    resp = client.delete(f"/api/users/{another_admin_plus.id}")
    assert resp.status_code == 403

    # admin+ CANNOT delete themselves (returns 400)
    resp = client.delete(f"/api/users/{admin_plus_id}")
    assert resp.status_code == 400

    # admin+ can delete standard admin
    resp = client.delete(f"/api/users/{created_id}")
    assert resp.status_code == 204

    # Logout admin_plus
    client.post("/api/auth/logout")

    # Login as superadmin to test promotion and demotion
    client.post("/api/auth/login", json={"username": "superadmin", "password": "superpassword"})

    # superadmin can promote standard admin to admin+
    resp = client.put(f"/api/users/{std_admin.id}", json={"is_admin_plus": True})
    assert resp.status_code == 200
    assert resp.json()["is_admin_plus"] is True

    # superadmin can demote admin+ to standard admin
    resp = client.put(f"/api/users/{std_admin.id}", json={"is_admin_plus": False})
    assert resp.status_code == 200
    assert resp.json()["is_admin_plus"] is False


