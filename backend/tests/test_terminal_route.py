import os
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from database import Base, get_db
import models
from main import app
from auth import require_admin_ws
from core import terminal_bridge

TEST_DATABASE_URL = "sqlite:///./test_terminal_route_db.db"


class FakeSession:
    """A terminal_bridge.TerminalSession stand-in with no real ssh process.

    `to_send` is what the fake pty "outputs" — one read() call returns the
    next entry, then empty forever, matching a process that printed a banner
    and then went quiet.

    `master_fd` is a real pipe read-end, not a fake int: routers.terminal
    registers it with the event loop via `loop.add_reader`/`remove_reader`,
    which need an actual selectable file descriptor. The write end is closed
    immediately, so the pipe is EOF-readable right away and the loop's
    reader callback fires exactly once, same as a process that has already
    exited."""

    def __init__(self, to_send=(b"",)):
        self._queue = list(to_send)
        self.written = []
        self.resized = []
        self.closed = False
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        self.master_fd = read_fd

    def read(self, size: int = 4096) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        return b""

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.resized.append((cols, rows))

    def poll(self):
        return None

    def close(self) -> None:
        self.closed = True
        try:
            os.close(self.master_fd)
        except OSError:
            pass


@pytest.fixture(scope="function")
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
        if os.path.exists("./test_terminal_route_db.db"):
            os.remove("./test_terminal_route_db.db")


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_node(db_session, ssh_login="root"):
    node = models.Node(hostname="term-node", ip_address="10.99.9.1", ssh_login=ssh_login)
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    return node


def _as_user(is_superadmin=False, is_admin_plus=False):
    return models.User(username="term-tester", is_superadmin=is_superadmin, is_admin_plus=is_admin_plus)


def test_rejects_connection_with_no_auth(client, db_session):
    node = _make_node(db_session)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/nodes/{node.id}/terminal"):
            pass


def test_superadmin_connects_with_the_orchestrator_key(client, db_session, monkeypatch):
    node = _make_node(db_session)
    app.dependency_overrides[require_admin_ws] = lambda: _as_user(is_superadmin=True)

    captured = {}
    def fake_open_bridge(*, host, port, user, use_key):
        captured["use_key"] = use_key
        return FakeSession()
    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", fake_open_bridge)

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        ws.close()
    assert captured["use_key"] is True


def test_plain_admin_gets_password_path_by_default(client, db_session, monkeypatch):
    node = _make_node(db_session)
    app.dependency_overrides[require_admin_ws] = lambda: _as_user()

    captured = {}
    def fake_open_bridge(*, host, port, user, use_key):
        captured["use_key"] = use_key
        return FakeSession()
    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", fake_open_bridge)

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        ws.close()
    assert captured["use_key"] is False


def test_plain_admin_gets_key_path_when_setting_is_on(client, db_session, monkeypatch):
    node = _make_node(db_session)
    settings = models.Settings(allow_admin_key_terminal_access=True)
    db_session.add(settings)
    db_session.commit()
    app.dependency_overrides[require_admin_ws] = lambda: _as_user()

    captured = {}
    def fake_open_bridge(*, host, port, user, use_key):
        captured["use_key"] = use_key
        return FakeSession()
    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", fake_open_bridge)

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        ws.close()
    assert captured["use_key"] is True


def test_open_and_close_are_audit_logged(client, db_session, monkeypatch):
    node = _make_node(db_session)
    app.dependency_overrides[require_admin_ws] = lambda: _as_user(is_superadmin=True)

    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", lambda **kw: FakeSession())

    import database
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(
        autocommit=False, autoflush=False, bind=db_session.get_bind(),
    ))

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        ws.close()

    # TestClient runs the server side in a background thread; closing from
    # the client does not block until that side's `finally` (where the
    # close log is written) has actually run. Poll briefly rather than
    # asserting immediately.
    actions = []
    for _ in range(40):
        db_session.expire_all()
        actions = [row.action for row in db_session.query(models.AuditLog).all()]
        if "Close Terminal" in actions:
            break
        time.sleep(0.05)

    assert "Open Terminal" in actions
    assert "Close Terminal" in actions


def test_close_reason_reaches_the_client(client, db_session, monkeypatch):
    """The status line in TerminalModal.tsx reads event.reason from the WS
    close frame — if the server never sends one, "why did my session end"
    (idle timeout vs. the node's own ssh dying) is silently lost."""
    node = _make_node(db_session)
    app.dependency_overrides[require_admin_ws] = lambda: _as_user(is_superadmin=True)

    class RemoteClosedSession(FakeSession):
        def poll(self):
            return 0  # process already exited, unlike FakeSession's None

    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", lambda **kw: RemoteClosedSession())

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_bytes()

    assert exc_info.value.reason == "remote closed"


def test_immediate_ssh_failure_output_reaches_the_client(client, db_session, monkeypatch):
    """A fast-failing ssh (bad host, refused connection) can exit before the
    route's read loop ever awaits anything, so on_pty_readable never gets a
    turn on the event loop before session.poll() is checked and the reader
    is torn down. The error text ssh already printed must still reach the
    browser rather than being silently discarded."""
    node = _make_node(db_session)
    app.dependency_overrides[require_admin_ws] = lambda: _as_user(is_superadmin=True)

    class ImmediatelyDeadSession(FakeSession):
        def __init__(self):
            super().__init__(to_send=(b"ssh: connect to host 10.0.0.1 port 22: Connection refused\r\n",))

        def poll(self):
            return 255  # already exited before the route's first poll() check

    import routers.terminal as terminal_route
    monkeypatch.setattr(terminal_route.terminal_bridge, "open_bridge", lambda **kw: ImmediatelyDeadSession())

    with client.websocket_connect(f"/api/nodes/{node.id}/terminal") as ws:
        received = ws.receive_bytes()

    assert b"Connection refused" in received
