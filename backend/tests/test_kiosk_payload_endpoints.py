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

#: The endpoint archives the offline client's source, which the router looks
#: for at /payload_client (a bind mount in the compose stack) and then beside
#: the backend. The backend image is built from ./backend alone and carries
#: neither, so a run inside it has nothing to archive -- an absent input, not
#: a regression.
_PAYLOAD_SOURCES = ("/payload_client", "./payload_client", "../payload_client")
requires_payload_client = pytest.mark.skipif(
    not any(os.path.isdir(p) for p in _PAYLOAD_SOURCES),
    reason="payload_client sources not present (running from the backend image?)",
)


@requires_payload_client
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
