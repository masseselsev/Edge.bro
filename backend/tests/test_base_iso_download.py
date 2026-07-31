import os
import shutil
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models

TEST_DATABASE_URL = "sqlite:///./test_base_iso_download_db.db"


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
        if os.path.exists("./test_base_iso_download_db.db"):
            os.remove("./test_base_iso_download_db.db")


def test_download_base_iso_task_triggers_template_rebuild(db_session):
    """A successful base ISO download must (re)build the USB-Kiosk Client
    template — otherwise a fresh install (or a re-download after clearing the
    cache) leaves client_iso_ready permanently False, since nothing else
    triggers the first build and the UI hides Issue Kiosk / Show Created ISOs
    until it is True.
    """
    import iso_tasks

    workspace_test_cache = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "test_base_iso_download_cache")
    )
    os.makedirs(workspace_test_cache, exist_ok=True)

    orig_cache_dir = iso_tasks.CACHE_DIR
    orig_base_path = iso_tasks.BASE_ISO_PATH
    orig_base_tmp = iso_tasks.BASE_ISO_PATH_TMP
    iso_tasks.CACHE_DIR = workspace_test_cache
    iso_tasks.BASE_ISO_PATH = os.path.join(workspace_test_cache, "base.iso")
    iso_tasks.BASE_ISO_PATH_TMP = iso_tasks.BASE_ISO_PATH + ".tmp"

    def fake_check_output(args, *a, **kw):
        if args[0] == "curl":
            return b"Content-Length: 16\r\n"
        if args[0] == "pgrep":
            raise subprocess.CalledProcessError(1, args)
        raise AssertionError(f"unexpected check_output call: {args}")

    def fake_check_call(args, *a, **kw):
        assert args[0] == "curl"
        out_path = args[args.index("-o") + 1]
        with open(out_path, "wb") as f:
            f.write(b"FAKE ISO CONTENT")
        return 0

    try:
        with patch("subprocess.check_output", side_effect=fake_check_output), \
             patch("subprocess.check_call", side_effect=fake_check_call), \
             patch("database.SessionLocal") as mock_session, \
             patch("iso_tasks.generate_client_iso_task") as mock_generate:
            mock_session.return_value = db_session

            result = iso_tasks.download_base_iso_task.run(url="https://example.test/custom.iso")

        assert result["status"] == "SUCCESS"
        assert os.path.exists(iso_tasks.BASE_ISO_PATH)
        mock_generate.delay.assert_called_once()
    finally:
        iso_tasks.CACHE_DIR = orig_cache_dir
        iso_tasks.BASE_ISO_PATH = orig_base_path
        iso_tasks.BASE_ISO_PATH_TMP = orig_base_tmp
        shutil.rmtree(workspace_test_cache, ignore_errors=True)
