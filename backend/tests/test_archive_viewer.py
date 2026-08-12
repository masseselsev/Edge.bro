import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from database import get_db, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

TEST_DATABASE_URL = "sqlite:///./test_archive_viewer_db.db"

@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(test_db):
    def _get_test_db():
        try:
            yield test_db
        finally:
            pass
    from routers.users import require_kiosk_or_admin
    def _override_auth():
        return models.User(username="admin", name="Admin", hashed_password="pw", is_superadmin=True)

    app.dependency_overrides[get_db] = _get_test_db
    app.dependency_overrides[require_kiosk_or_admin] = _override_auth
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@patch("routers.nodes_crud.os.path.exists")
@patch("routers.nodes_crud.subprocess.run")
def test_get_archive_files_endpoint(mock_run, mock_exists, client, test_db):
    mock_exists.return_value = True
    node = models.Node(hostname="node-test", ip_address="192.168.1.100")
    test_db.add(node)
    test_db.commit()

    history = models.BackupHistory(
        node_id=node.id,
        archive_name="node-test-20260801000000",
        original_size=1000,
        deduplicated_size=500,
        status="SUCCESS"
    )
    test_db.add(history)
    test_db.commit()

    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='{"path": "etc/fstab", "size": 256, "mode": "-rw-r--r--", "mtime": "2026-08-01T00:00:00"}\n'
    )

    headers = {"X-Kiosk-Secret": "kiosk-secret"}
    response = client.get(f"/api/nodes/history/{history.id}/files", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["archive_name"] == "node-test-20260801000000"
    assert len(data["files"]) == 1
    assert data["files"][0]["path"] == "etc/fstab"
    assert data["files"][0]["size"] == 256


@patch("routers.nodes_crud.os.path.exists")
@patch("routers.nodes_crud.subprocess.Popen")
def test_get_archive_file_content_endpoint(mock_popen, mock_exists, client, test_db):
    mock_exists.return_value = True
    node = models.Node(hostname="node-test2", ip_address="192.168.1.101")
    test_db.add(node)
    test_db.commit()

    history = models.BackupHistory(
        node_id=node.id,
        archive_name="node-test2-20260801000000",
        original_size=1000,
        deduplicated_size=500,
        status="SUCCESS"
    )
    test_db.add(history)
    test_db.commit()

    mock_proc = MagicMock()
    mock_proc.stdout.read.return_value = b"UUID=1234 / ext4 defaults 0 1\n"
    mock_proc.stderr.read.return_value = b""
    mock_proc.wait.return_value = 0
    mock_popen.return_value = mock_proc

    headers = {"X-Kiosk-Secret": "kiosk-secret"}
    response = client.get(f"/api/nodes/history/{history.id}/file-content?path=etc/fstab", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "etc/fstab"
    assert data["is_text"] is True
    assert "UUID=1234" in data["content"]


@patch("routers.nodes_crud.os.path.exists")
@patch("routers.nodes_crud.subprocess.Popen")
def test_download_archive_file_endpoint(mock_popen, mock_exists, client, test_db):
    mock_exists.return_value = True
    node = models.Node(hostname="node-dl", ip_address="192.168.1.102")
    test_db.add(node)
    test_db.commit()

    history = models.BackupHistory(
        node_id=node.id,
        archive_name="node-dl-20260801000000",
        original_size=1000,
        deduplicated_size=500,
        status="SUCCESS"
    )
    test_db.add(history)
    test_db.commit()

    mock_proc = MagicMock()
    mock_proc.stdout.read.side_effect = [b"binary file stream content", b""]
    mock_proc.stderr.read.return_value = b""
    mock_proc.wait.return_value = 0
    mock_popen.return_value = mock_proc

    headers = {"X-Kiosk-Secret": "kiosk-secret"}
    response = client.get(f"/api/nodes/history/{history.id}/download-file?path=var/log/syslog", headers=headers)
    assert response.status_code == 200
    assert response.content == b"binary file stream content"
    assert "syslog" in response.headers.get("content-disposition", "")


@patch("routers.nodes_crud.subprocess.run")
def test_download_archive_directory_as_zip_endpoint(mock_run, client, test_db, tmp_path):
    # Folder download extracts into scratch space next to the repository, at
    # /data/borg/tmp. That path exists only inside the container, so without
    # redirecting it this test can pass nowhere else — it used to fail on the
    # host with PermissionError: '/data' while mocking only os.path.exists.
    scratch = tmp_path / "borg-scratch"

    node = models.Node(hostname="node-zip", ip_address="192.168.1.103")
    test_db.add(node)
    test_db.commit()

    history = models.BackupHistory(
        node_id=node.id,
        archive_name="node-zip-20260801000000",
        original_size=2000,
        deduplicated_size=1000,
        status="SUCCESS"
    )
    test_db.add(history)
    test_db.commit()

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd")
        if cwd:
            target = os.path.join(cwd, "var/log")
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(target, "test.log"), "w") as f:
                f.write("test log content")
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = ""
        mock_res.stderr = ""
        return mock_res

    mock_run.side_effect = fake_run

    real_exists = os.path.exists
    def fake_exists(path):
        if path == "/data/borg/fleet":
            return True
        return real_exists(path)

    real_makedirs = os.makedirs
    def fake_makedirs(path, *args, **kwargs):
        # The endpoint derives its scratch root from the repository path; send
        # that one directory into pytest's tmp_path and leave the rest alone.
        if path == "/data/borg/tmp":
            path = str(scratch)
        return real_makedirs(path, *args, **kwargs)

    real_chmod = os.chmod
    def fake_chmod(path, *args, **kwargs):
        if path == "/data/borg/tmp":
            path = str(scratch)
        return real_chmod(path, *args, **kwargs)

    real_mkdtemp = tempfile.mkdtemp
    def fake_mkdtemp(*args, **kwargs):
        if kwargs.get("dir") == "/data/borg/tmp":
            kwargs["dir"] = str(scratch)
        return real_mkdtemp(*args, **kwargs)

    with patch("routers.nodes_crud.os.path.exists", side_effect=fake_exists), \
         patch("routers.nodes_crud.os.makedirs", side_effect=fake_makedirs), \
         patch("routers.nodes_crud.os.chmod", side_effect=fake_chmod), \
         patch("routers.nodes_crud.tempfile.mkdtemp", side_effect=fake_mkdtemp):
        headers = {"X-Kiosk-Secret": "kiosk-secret"}
        response = client.get(f"/api/nodes/history/{history.id}/download-file?path=var/log&is_dir=true", headers=headers)
        assert response.status_code == 200
        assert "application/zip" in response.headers.get("content-type", "")
        assert "log.zip" in response.headers.get("content-disposition", "")


