import os
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from database import Base
import models
import schemas
from routers.kiosks import (
    generate_kiosk_key,
    create_kiosk,
    list_kiosks,
    delete_kiosk,
    revoke_kiosk,
    handshake
)

TEST_DATABASE_URL = "sqlite:///./test_kiosks_db.db"

@pytest.fixture(scope="module")
def db_session():
    """
    Creates an in-memory SQLite database session for unit testing Kiosk DB schema & API.
    """
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        if os.path.exists("./test_kiosks_db.db"):
            os.remove("./test_kiosks_db.db")

def test_generate_kiosk_key():
    """
    Test format of generated security keys.
    """
    key = generate_kiosk_key()
    assert len(key) == 9 # XXXX-XXXX
    assert "-" in key
    parts = key.split("-")
    assert len(parts) == 2
    assert len(parts[0]) == 4
    assert len(parts[1]) == 4
    # No ambiguous characters
    ambiguous = ["O", "0", "I", "1"]
    for char in ambiguous:
        assert char not in key

@patch("routers.kiosks.authorize_ssh_key")
@patch("routers.kiosks.revoke_ssh_key")
def test_kiosks_crud_and_handshake(mock_revoke, mock_authorize, db_session):
    """
    Test CRUD actions and handshake by calling route handler functions directly.
    """
    kiosk_uuid = "e1c0800b-33de-40fb-88fc-ef7e06a38b1f"
    kiosk_name = "Room A Test Kiosk"
    
    # 1. Register Kiosk
    req_create = schemas.KioskCreate(name=kiosk_name, uuid=kiosk_uuid)
    kiosk = create_kiosk(req_create, db=db_session)
    
    assert kiosk.name == kiosk_name
    assert kiosk.uuid == kiosk_uuid
    assert kiosk.status == "PENDING"
    assert len(kiosk.key) == 9
    
    key = kiosk.key
    kiosk_id = kiosk.id
    
    # Check duplicate UUID registration fails
    with pytest.raises(HTTPException) as exc_info:
        create_kiosk(req_create, db=db_session)
    assert exc_info.value.status_code == 400
    
    # 2. Get list of kiosks
    kiosks = list_kiosks(db=db_session)
    assert len(kiosks) >= 1
    found = [k for k in kiosks if k.uuid == kiosk_uuid]
    assert len(found) == 1
    assert found[0].name == kiosk_name
    
    # 3. Trigger handshake
    fake_ssh_pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqgXgGf18V... KioskSSHKey"
    req_handshake = schemas.HandshakeRequest(
        uuid=kiosk_uuid,
        key=key,
        ssh_pub_key=fake_ssh_pub
    )
    
    # Perform handshake
    hs_data = handshake(req_handshake, db=db_session)
    assert hs_data["status"] == "SUCCESS"
    assert "auth_token" in hs_data
    
    # Verify DB record updated
    db_session.expire_all()
    kiosk_record = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    assert kiosk_record.status == "APPROVED"
    assert kiosk_record.ssh_pub_key == fake_ssh_pub
    assert kiosk_record.auth_token == hs_data["auth_token"]
    
    mock_authorize.assert_called_once_with(fake_ssh_pub)
    
    # Duplicate handshake should fail (since status is no longer PENDING)
    with pytest.raises(HTTPException) as exc_info:
        handshake(req_handshake, db=db_session)
    assert exc_info.value.status_code == 400
    
    # 4. Revoke Kiosk Access
    revoke_data = revoke_kiosk(kiosk_id, db=db_session)
    assert revoke_data["kiosk_status"] == "REVOKED"
    
    mock_revoke.assert_called_once_with(fake_ssh_pub)
    
    # Verify status in DB
    db_session.expire_all()
    kiosk_record = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    assert kiosk_record.status == "REVOKED"
    
    # 5. Delete Kiosk
    delete_data = delete_kiosk(kiosk_id, db=db_session)
    assert delete_data["status"] == "SUCCESS"
    
    # Verify Kiosk deleted from DB
    db_session.expire_all()
    deleted_record = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk_id).first()
    assert deleted_record is None
