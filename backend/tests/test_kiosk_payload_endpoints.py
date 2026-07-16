import os
import pytest
import tarfile
import io
from fastapi.testclient import TestClient

from main import app

@pytest.fixture(scope="module")
def client():
    # Set app.state.payload_hash for testing
    app.state.payload_hash = "mocked_sha256_hash_value_123456"
    with TestClient(app) as c:
        yield c

def test_get_payload_hash(client):
    response = client.get("/api/kiosks/payload-hash")
    assert response.status_code == 200
    data = response.json()
    assert len(data["hash"]) == 64

def test_download_payload_archive(client):
    # Ensure temporary directories/files are in place if needed by tarfile.add
    # Our implementation will archive "./payload_client" directory.
    # Let's verify we get a 200 and a gzip file.
    response = client.get("/api/kiosks/payload-archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    
    # Read the streamed bytes and check if it's a valid tar archive
    archive_bytes = io.BytesIO(response.content)
    with tarfile.open(fileobj=archive_bytes, mode="r:gz") as tar:
        members = tar.getnames()
        assert len(members) > 0
        # Check if the archived path contains offline-client/backend/main.py or similar
        assert any("main.py" in m for m in members)
