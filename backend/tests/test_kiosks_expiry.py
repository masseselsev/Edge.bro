import os
import pytest
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base, get_db
import models
from main import app
from core.clock import utcnow
from routers.kiosks import sweep_expired_pending_kiosks, PENDING_KIOSK_EXPIRY_HOURS

TEST_DATABASE_URL = "sqlite:///./test_kiosks_expiry_db.db"

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
        if os.path.exists("./test_kiosks_expiry_db.db"):
            os.remove("./test_kiosks_expiry_db.db")

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


def test_sweep_expired_pending_kiosks_deletes_only_stale_pending(db_session):
    now = utcnow()
    
    # 1. Fresh pending kiosk (10 hours old) -> should NOT be deleted
    kiosk_fresh_pending = models.Kiosk(
        name="Fresh Pending Kiosk",
        kiosk_id="KS_FRESH_1",
        key="1111AA",
        status="PENDING",
        created_at=now - timedelta(hours=10)
    )
    
    # 2. Expired pending kiosk (75 hours old) -> SHOULD be deleted
    kiosk_expired_pending = models.Kiosk(
        name="Expired Pending Kiosk",
        kiosk_id="KS_EXPIRED_2",
        key="2222BB",
        status="PENDING",
        created_at=now - timedelta(hours=75)
    )
    
    # 3. Old approved kiosk (100 hours old) -> should NOT be deleted
    kiosk_old_approved = models.Kiosk(
        name="Old Approved Kiosk",
        kiosk_id="KS_APPROVED_3",
        key="3333CC",
        status="APPROVED",
        created_at=now - timedelta(hours=100)
    )
    
    # 4. Old disabled kiosk (100 hours old) -> should NOT be deleted
    kiosk_old_disabled = models.Kiosk(
        name="Old Disabled Kiosk",
        kiosk_id="KS_DISABLED_4",
        key="4444DD",
        status="DISABLED",
        created_at=now - timedelta(hours=100)
    )
    
    db_session.add_all([
        kiosk_fresh_pending,
        kiosk_expired_pending,
        kiosk_old_approved,
        kiosk_old_disabled
    ])
    db_session.commit()
    
    # Run sweep with default 72h max_age
    deleted_count = sweep_expired_pending_kiosks(db_session, max_age_hours=PENDING_KIOSK_EXPIRY_HOURS)
    assert deleted_count == 1
    
    remaining = {k.kiosk_id: k for k in db_session.query(models.Kiosk).all()}
    assert "KS_FRESH_1" in remaining
    assert "KS_APPROVED_3" in remaining
    assert "KS_DISABLED_4" in remaining
    assert "KS_EXPIRED_2" not in remaining


def test_list_kiosks_endpoint_automatically_sweeps_expired(client, db_session):
    now = utcnow()
    
    # Add a stale pending kiosk (80 hours old)
    stale_kiosk = models.Kiosk(
        name="Stale Pending Kiosk For List",
        kiosk_id="KS_STALE_LIST",
        key="5555EE",
        status="PENDING",
        created_at=now - timedelta(hours=80)
    )
    db_session.add(stale_kiosk)
    db_session.commit()
    
    # Call GET /api/kiosks
    resp = client.get("/api/kiosks")
    assert resp.status_code == 200
    kiosks = resp.json()
    kiosk_ids = [k["kiosk_id"] for k in kiosks]
    
    assert "KS_STALE_LIST" not in kiosk_ids
    assert db_session.query(models.Kiosk).filter(models.Kiosk.kiosk_id == "KS_STALE_LIST").first() is None


def test_prune_task_is_in_celery_beat():
    import tasks
    beat_schedule = tasks.celery_app.conf.beat_schedule
    assert "prune-expired-pending-kiosks-task" in beat_schedule
    assert beat_schedule["prune-expired-pending-kiosks-task"]["task"] == "tasks.prune_expired_pending_kiosks_task"
