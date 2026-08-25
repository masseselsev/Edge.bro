import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from core import archive_cleanup
from database import Base, get_db
from main import app
from routers import history as history_router
from core.clock import utcnow

TEST_DATABASE_URL = "sqlite:///./test_history_deletion_db.db"


@pytest.fixture
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
        engine.dispose()
        if os.path.exists("./test_history_deletion_db.db"):
            os.remove("./test_history_deletion_db.db")


@pytest.fixture
def no_repo(monkeypatch):
    """No borg repository reachable — the usual case in tests, and the case on
    an orchestrator whose storage is temporarily away."""
    monkeypatch.setattr(archive_cleanup, "list_repo_archives", lambda *a, **k: None)


@pytest.fixture
def client(db_session, no_repo):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    from routers.users import require_admin

    def override_require_admin():
        return models.User(username="test_admin", is_superadmin=True)

    app.dependency_overrides[require_admin] = override_require_admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def make_node(db, hostname="node-1", ip="192.168.1.101"):
    node = models.Node(hostname=hostname, ip_address=ip)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def make_history(db, node, name, status="FAILED", days_ago=1):
    row = models.BackupHistory(
        node_id=node.id,
        archive_name=name,
        timestamp=utcnow() - timedelta(days=days_ago),
        original_size=0,
        deduplicated_size=0,
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- matching rule ----------------------------------------------------------

def test_an_archive_claims_its_own_checkpoints():
    present = {"WS-20260623124040", "WS-20260623124040.checkpoint"}
    assert archive_cleanup.matching_archives(present, "WS-20260623124040") == [
        "WS-20260623124040",
        "WS-20260623124040.checkpoint",
    ]


def test_an_archive_does_not_claim_its_neighbour():
    """Two runs a second apart differ only in their last character; a plain
    prefix match would delete the wrong archive."""
    present = {"WS-20260623124040", "WS-20260623124041"}
    assert archive_cleanup.matching_archives(present, "WS-2026062312404") == []
    assert archive_cleanup.matching_archives(present, "WS-20260623124040") == ["WS-20260623124040"]


def test_nothing_in_the_repository_means_nothing_to_delete():
    assert archive_cleanup.matching_archives(set(), "WS-1") == []
    assert archive_cleanup.matching_archives({"WS-1"}, "") == []


def test_checkpoints_can_be_excluded_from_the_match():
    """A checkpoint left by a failed run holds the chunks the next attempt
    would otherwise have to transfer again, so it is not always leftovers."""
    present = {"WS-20260623124040", "WS-20260623124040.checkpoint"}
    assert archive_cleanup.matching_archives(
        present, "WS-20260623124040", include_checkpoints=False
    ) == ["WS-20260623124040"]


# --- single deletion --------------------------------------------------------

def test_a_failed_record_can_be_deleted(client, db_session):
    node = make_node(db_session)
    row = make_history(db_session, node, "bad-1")

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert res.status_code == 200
    assert res.json()["deleted"] == 1
    assert db_session.query(models.BackupHistory).count() == 0


def test_a_successful_archive_is_refused(client, db_session):
    """It holds restorable data; removing it belongs to retention or a purge."""
    node = make_node(db_session)
    row = make_history(db_session, node, "good-1", status="SUCCESS")

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert res.status_code == 400
    assert db_session.query(models.BackupHistory).count() == 1


def test_deleting_a_record_that_is_not_there_is_a_404(client, db_session):
    assert client.delete("/api/nodes/history/9999").status_code == 404


def test_the_deletion_is_written_to_the_audit_log(client, db_session):
    node = make_node(db_session)
    row = make_history(db_session, node, "bad-1")

    client.delete(f"/api/nodes/history/{row.id}")

    entry = db_session.query(models.AuditLog).filter(
        models.AuditLog.action == "Delete Failed Backup"
    ).first()
    assert entry is not None
    assert "bad-1" in entry.details


# --- bulk purge -------------------------------------------------------------

def test_purge_clears_every_failed_record_and_keeps_the_successes(client, db_session):
    node = make_node(db_session)
    make_history(db_session, node, "bad-1")
    make_history(db_session, node, "bad-2")
    make_history(db_session, node, "good-1", status="SUCCESS")

    res = client.post("/api/nodes/history/purge-failed", json={})

    assert res.json()["deleted"] == 2
    remaining = db_session.query(models.BackupHistory).all()
    assert [r.archive_name for r in remaining] == ["good-1"]


def test_purge_can_be_limited_to_one_node(client, db_session):
    keep = make_node(db_session, "keeper", "192.168.1.101")
    clear = make_node(db_session, "target", "192.168.1.102")
    make_history(db_session, keep, "keeper-bad")
    make_history(db_session, clear, "target-bad")

    res = client.post("/api/nodes/history/purge-failed", json={"node_id": clear.id})

    assert res.json()["deleted"] == 1
    remaining = db_session.query(models.BackupHistory).all()
    assert [r.archive_name for r in remaining] == ["keeper-bad"]


def test_purge_can_be_limited_by_date(client, db_session):
    node = make_node(db_session)
    make_history(db_session, node, "ancient", days_ago=40)
    make_history(db_session, node, "recent", days_ago=2)

    cutoff = (utcnow() - timedelta(days=30)).isoformat()
    res = client.post("/api/nodes/history/purge-failed", json={"before": cutoff})

    assert res.json()["deleted"] == 1
    remaining = db_session.query(models.BackupHistory).all()
    assert [r.archive_name for r in remaining] == ["recent"]


def test_purging_an_unknown_node_is_a_404(client, db_session):
    res = client.post("/api/nodes/history/purge-failed", json={"node_id": 9999})
    assert res.status_code == 404


def test_purging_nothing_succeeds_quietly(client, db_session):
    res = client.post("/api/nodes/history/purge-failed", json={})
    assert res.status_code == 200
    assert res.json() == {"deleted": 0, "checkpoints_removed": 0}


def test_an_unexpectedly_large_purge_is_refused(client, db_session, monkeypatch):
    """Thousands of rows means the request was not what its author thought."""
    monkeypatch.setattr(history_router, "_BULK_SAFETY_LIMIT", 2)
    node = make_node(db_session)
    for i in range(3):
        make_history(db_session, node, f"bad-{i}")

    res = client.post("/api/nodes/history/purge-failed", json={})

    assert res.status_code == 400
    assert db_session.query(models.BackupHistory).count() == 3


# --- repository leftovers ---------------------------------------------------

def test_leftover_checkpoints_are_removed_once_a_later_backup_has_succeeded(
    client, db_session, monkeypatch
):
    """With a newer success on the node, the checkpoint's chunks are already
    referenced by a real archive and it is genuinely dead weight."""
    node = make_node(db_session)
    row = make_history(db_session, node, "WS-1", days_ago=2)
    make_history(db_session, node, "WS-2", status="SUCCESS", days_ago=1)

    deleted = []
    monkeypatch.setattr(archive_cleanup, "list_repo_archives",
                        lambda *a, **k: {"WS-1.checkpoint", "WS-2"})
    monkeypatch.setattr(archive_cleanup, "delete_archives",
                        lambda names, *a, **k: deleted.extend(names) or len(names))

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert deleted == ["WS-1.checkpoint"]
    assert res.json()["checkpoints_removed"] == 1


def test_a_checkpoint_survives_the_purge_while_it_can_still_resume_a_backup(
    client, db_session, monkeypatch
):
    """The natural reaction to a run of failures is to clear them, and that
    used to delete the very chunks the next attempt would have skipped —
    turning a resumable transfer back into a full one."""
    node = make_node(db_session)
    row = make_history(db_session, node, "WS-1")

    deleted = []
    monkeypatch.setattr(archive_cleanup, "list_repo_archives",
                        lambda *a, **k: {"WS-1.checkpoint", "WS-2"})
    monkeypatch.setattr(archive_cleanup, "delete_archives",
                        lambda names, *a, **k: deleted.extend(names) or len(names))

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert deleted == [], "the checkpoint is the next attempt's head start"
    assert res.json() == {"deleted": 1, "checkpoints_removed": 0}
    assert db_session.query(models.BackupHistory).count() == 0, "the record still goes"


def test_an_unreachable_repository_still_lets_the_record_go(client, db_session, monkeypatch):
    """Otherwise an operator cannot clear a failure they can plainly see."""
    node = make_node(db_session)
    row = make_history(db_session, node, "WS-1")

    def explode(*args, **kwargs):
        raise OSError("storage is away")

    monkeypatch.setattr(archive_cleanup, "list_repo_archives", explode)

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert res.status_code == 200
    assert res.json() == {"deleted": 1, "checkpoints_removed": 0}
    assert db_session.query(models.BackupHistory).count() == 0


def test_a_failed_borg_delete_does_not_block_the_record(client, db_session, monkeypatch):
    node = make_node(db_session)
    row = make_history(db_session, node, "WS-1")

    monkeypatch.setattr(archive_cleanup, "list_repo_archives", lambda *a, **k: {"WS-1"})
    monkeypatch.setattr(archive_cleanup, "delete_archives",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("repo locked")))

    res = client.delete(f"/api/nodes/history/{row.id}")

    assert res.status_code == 200
    assert res.json()["checkpoints_removed"] == 0
    assert db_session.query(models.BackupHistory).count() == 0
