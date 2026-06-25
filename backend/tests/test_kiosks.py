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
    handshake,
    enroll_kiosk,
    generate_kiosk_uuid,
    update_kiosk
)
from routers.iso import issue_kiosk

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
    assert len(key) == 6 # 1234AB (4 digits + 2 letters)
    assert key[:4].isdigit()
    assert key[4:].isalpha()
    assert key.isupper()
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
    kiosk_uuid = "TK1234"
    kiosk_name = "Room A Test Kiosk"
    
    # 1. Register Kiosk
    req_create = schemas.KioskCreate(name=kiosk_name, uuid=kiosk_uuid)
    kiosk = create_kiosk(req_create, db=db_session)
    
    assert kiosk.name == kiosk_name
    assert kiosk.uuid == kiosk_uuid
    assert kiosk.status == "PENDING"
    assert len(kiosk.key) == 6

    
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
        key=key.lower(), # Test case insensitivity
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

def test_kiosk_enrollment_flow(db_session):
    # Test first-time enrollment
    req_enroll = schemas.KioskEnrollRequest(
        uuid="NEW_KIOSK_123",
        name="Dynamic Test Kiosk",
        contact="555-1234",
        comment="Testing dynamic registration flow",
        ssh_pub_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqgXgGf18V... KioskSSHKey"
    )
    res = enroll_kiosk(req_enroll, db=db_session)
    assert res["status"] == "PENDING"
    assert len(res["key"]) == 6
    assert "auth_token" in res
    
    # Verify kiosk is saved in db
    db_session.expire_all()
    kiosk = db_session.query(models.Kiosk).filter(models.Kiosk.uuid == "NEW_KIOSK_123").first()
    assert kiosk is not None
    assert kiosk.name == "Dynamic Test Kiosk"
    assert kiosk.contact == "555-1234"
    assert kiosk.comment == "Testing dynamic registration flow"


@patch("routers.kiosks.authorize_ssh_key")
def test_pre_registered_kiosk_handshake(mock_authorize, db_session):
    # 1. Pre-register kiosk without UUID
    req_create = schemas.KioskCreate(
        name="Pre-registered Kiosk",
        contact="111-2222",
        comment="Test pre-registration",
        uuid=None
    )
    kiosk = create_kiosk(req_create, db=db_session)
    assert kiosk.uuid.startswith("PENDING-")
    assert kiosk.status == "PENDING"
    
    # 2. Perform handshake with actual client UUID
    client_uuid = "HW_UUID_999"
    req_handshake = schemas.HandshakeRequest(
        uuid=client_uuid,
        key=kiosk.key,
        ssh_pub_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqgXgGf18V... KioskSSHKey"
    )
    res = handshake(req_handshake, db=db_session)
    assert res["status"] == "SUCCESS"
    
    # Verify kiosk record updated with actual UUID and APPROVED status
    db_session.expire_all()
    updated_kiosk = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert updated_kiosk.uuid == client_uuid
    assert updated_kiosk.status == "APPROVED"

    # 3. Create another pre-registered kiosk
    req_create_2 = schemas.KioskCreate(
        name="Second Pre-registered Kiosk",
        uuid=None
    )
    kiosk_2 = create_kiosk(req_create_2, db=db_session)
    
    # Attempting to handshake with the duplicate client UUID should fail
    req_handshake_dup = schemas.HandshakeRequest(
        uuid=client_uuid,
        key=kiosk_2.key,
        ssh_pub_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqgXgGf18V... KioskSSHKey"
    )
    with pytest.raises(HTTPException) as exc_info:
        handshake(req_handshake_dup, db=db_session)
    assert exc_info.value.status_code == 400
    assert "already registered" in exc_info.value.detail


@patch("routers.kiosks.authorize_ssh_key")
def test_kiosks_activation_and_auto_handshake(mock_authorize, db_session):
    from routers.kiosks import toggle_kiosk_active, request_kiosk_activation, auto_handshake
    
    # 1. Create an approved kiosk record
    kiosk = models.Kiosk(
        name="Test Kiosk Active",
        uuid="TEST_KIOSK_UUID_111",
        key="1234AB",
        status="APPROVED",
        auth_token="token-active-123"
    )
    db_session.add(kiosk)
    db_session.commit()
    db_session.refresh(kiosk)
    
    # 2. Toggle active (should disable it)
    res = toggle_kiosk_active(kiosk.id, db=db_session)
    assert res["status"] == "SUCCESS"
    assert res["kiosk_status"] == "DISABLED"
    
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db.status == "DISABLED"
    
    # 3. auto_handshake on disabled kiosk should fail with 403
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer token-active-123"}
    req_handshake = schemas.AutoHandshakeRequest(
        uuid="TEST_KIOSK_UUID_111",
        ssh_pub_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPqgXgGf18V... KioskSSHKey"
    )
    with pytest.raises(HTTPException) as exc_info:
        auto_handshake(req_handshake, mock_request, db=db_session)
    assert exc_info.value.status_code == 403
    assert "DISABLED" in exc_info.value.detail
    
    # 4. Request activation
    req_act = schemas.RequestActivationRequest(token="token-active-123")
    res_act = request_kiosk_activation(req_act, db=db_session)
    assert res_act["status"] == "SUCCESS"
    
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db.status == "PENDING"
    
    # 5. auto_handshake on pending kiosk should return PENDING and NOT authorize SSH
    res_hs = auto_handshake(req_handshake, mock_request, db=db_session)
    assert res_hs["status"] == "PENDING"
    mock_authorize.assert_not_called()
    
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db.status == "PENDING"
    
    # 6. Toggle active again (should approve it)
    res_toggle = toggle_kiosk_active(kiosk.id, db=db_session)
    assert res_toggle["status"] == "SUCCESS"
    assert res_toggle["kiosk_status"] == "APPROVED"
    
    # 7. auto_handshake on approved kiosk should succeed and authorize SSH
    res_hs_ok = auto_handshake(req_handshake, mock_request, db=db_session)
    assert res_hs_ok["status"] == "APPROVED"
    assert mock_authorize.call_count == 2


def test_generate_kiosk_uuid():
    """
    Test format of generated memorable kiosk IDs (starts with 'KS' + 4 digits).
    """
    uuid_val = generate_kiosk_uuid()
    assert len(uuid_val) == 6
    assert uuid_val.startswith("KS")
    assert uuid_val[2:].isdigit()
    # No ambiguous characters (0, 1, 2)
    ambiguous = ["0", "1", "2"]
    for char in ambiguous:
        assert char not in uuid_val


@patch("iso_tasks.repack_kiosk_iso_task.delay")
def test_issue_kiosk_flow(mock_repack_task, db_session):
    """
    Test issuing a kiosk (pre-baked ISO) generates a memorable UUID directly.
    """
    mock_repack_task.return_value = MagicMock(id="test-task-123")

    req_issue = schemas.KioskIssueRequest(
        name="Pre-baked Kiosk Vasya",
        contact="555-9999",
        comment="Test pre-baked flow"
    )
    
    # We call issue_kiosk directly
    res = issue_kiosk(req_issue, db=db_session)
    assert "kiosk" in res
    assert "task_id" in res
    assert res["task_id"] == "test-task-123"
    
    kiosk = res["kiosk"]
    assert kiosk.name == "Pre-baked Kiosk Vasya"
    assert kiosk.uuid.startswith("KS")
    assert len(kiosk.uuid) == 6
    assert kiosk.status == "APPROVED"
    assert kiosk.auth_token is not None
    assert len(kiosk.auth_token) == 6
    
    # Verify saved correctly in DB
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db is not None
    assert kiosk_db.uuid == kiosk.uuid
    assert kiosk_db.status == "APPROVED"
    assert kiosk_db.auth_token == kiosk.auth_token


def test_update_kiosk(db_session):
    """
    Test PUT endpoint to update Kiosk profile properties.
    """
    # 1. Create a kiosk
    kiosk = models.Kiosk(
        name="Old Name",
        uuid="KS8888",
        key="8888KS",
        contact="old@contact.com",
        comment="old comment",
        status="APPROVED"
    )
    db_session.add(kiosk)
    db_session.commit()
    db_session.refresh(kiosk)
    
    # 2. Update it
    req_update = schemas.KioskUpdate(
        name="New Name",
        contact="new@contact.com",
        comment="new comment"
    )
    updated = update_kiosk(kiosk.id, req_update, db=db_session)
    assert updated.name == "New Name"
    assert updated.contact == "new@contact.com"
    assert updated.comment == "new comment"
    
    # 3. Verify database
    db_session.expire_all()
    kiosk_db = db_session.query(models.Kiosk).filter(models.Kiosk.id == kiosk.id).first()
    assert kiosk_db.name == "New Name"
    assert kiosk_db.contact == "new@contact.com"
    assert kiosk_db.comment == "new comment"




