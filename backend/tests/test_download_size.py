"""What restoring an archive actually costs to transfer.

`deduplicated_size` is the archive's *contribution* to the repository, not the
size of its contents, and reading it as a download figure is wrong by orders of
magnitude: a second backup of an unchanged node contributes a few hundred KB
and is still gigabytes to restore. The UI showed exactly that — 743 KB of
deduplicated size beside a 1.27 GB download — because the download column was
`max(deduplicated_size, original_size * 0.4)`, a guess at a compression ratio.

Borg reports the real number, `compressed_size`, in the same JSON the other two
are parsed out of, and it was being discarded. These pin that it is kept.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_download_size_db.db"

#: Shaped like the real thing: a node whose contents barely changed, so its
#: contribution is tiny while its transfer size is not.
BORG_STATS = {
    "original_size": 3_403_073_534,
    "deduplicated_size": 743_310,
    "compressed_size": 1_571_505_451,
}


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
        if os.path.exists("./test_download_size_db.db"):
            os.remove("./test_download_size_db.db")


class _SessionProxy:
    """Hands every caller the one test session, and never closes it."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def _fake_subprocess_run(args, *a, **kw):
    if args[0] == "borg" and args[1] == "break-lock":
        return MagicMock(returncode=0, stdout="", stderr="")
    if args[0] == "ssh":
        last = args[-1]
        if "pkill" in last:
            return MagicMock(
                returncode=0,
                stdout="10.0.0.9 5000 203.0.113.5 12345\nREACHABLE:yes\nOK\n",
                stderr="",
            )
        if "borg init" in last:
            return MagicMock(returncode=0, stdout="", stderr="")
    raise AssertionError(f"unexpected subprocess.run call: {args}")


def _popen_reporting(stats):
    def _fake_popen(cmd, *a, **kw):
        proc = MagicMock()
        payload = json.dumps({"archive": {"stats": stats}})
        proc.communicate.return_value = (payload, "")
        proc.stdout = [payload]
        proc.stderr = []
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    return _fake_popen


def _run_backup(db_session, monkeypatch, stats):
    from backup_tasks import run_backup_task
    from celery.app.task import Task

    monkeypatch.setattr("database.SessionLocal", lambda: _SessionProxy(db_session))

    settings = db_session.query(models.Settings).first() or models.Settings()
    settings.orchestrator_ip = "203.0.113.5"
    settings.borg_ssh_port = 12345
    db_session.add(settings)

    node = models.Node(hostname="WS1", ip_address="10.0.0.9", ssh_port=22, status="READY")
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    request = MagicMock()
    request.id = "test-download-size"
    monkeypatch.setattr(Task, "request", request)

    with patch("redis.Redis.from_url", return_value=MagicMock()), \
         patch("tasks.log_to_task"), \
         patch("subprocess.run", side_effect=_fake_subprocess_run), \
         patch("subprocess.Popen", side_effect=_popen_reporting(stats)):
        result = run_backup_task.run(node.id)

    assert result["status"] == "SUCCESS", result
    return db_session.query(models.BackupHistory).one()


def test_the_compressed_size_borg_reports_is_recorded(db_session, monkeypatch):
    row = _run_backup(db_session, monkeypatch, BORG_STATS)
    assert row.compressed_size == BORG_STATS["compressed_size"]


def test_it_is_kept_separate_from_the_archives_contribution(db_session, monkeypatch):
    """The whole point: these two differ by three orders of magnitude on a
    re-backup, and conflating them is what produced the wrong figure."""
    row = _run_backup(db_session, monkeypatch, BORG_STATS)
    assert row.deduplicated_size == BORG_STATS["deduplicated_size"]
    assert row.compressed_size > row.deduplicated_size * 1000


def test_a_borg_that_does_not_report_it_leaves_it_null(db_session, monkeypatch):
    """Null, not zero — the UI has to tell "not recorded" from "nothing to
    transfer" so it knows to fall back to the estimate."""
    stats = {k: v for k, v in BORG_STATS.items() if k != "compressed_size"}
    row = _run_backup(db_session, monkeypatch, stats)
    assert row.compressed_size is None
    assert row.deduplicated_size == BORG_STATS["deduplicated_size"]


def test_the_api_serves_it(db_session, monkeypatch):
    """A column the response model does not declare is a column the UI never
    sees, which is how the estimate survived having a real number available."""
    import schemas

    row = _run_backup(db_session, monkeypatch, BORG_STATS)
    payload = schemas.BackupHistoryResponse.model_validate(row).model_dump()
    assert payload["compressed_size"] == BORG_STATS["compressed_size"]
