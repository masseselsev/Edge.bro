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
