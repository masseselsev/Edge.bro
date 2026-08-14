import os
import shutil
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from database import Base
import models
from routers.nodes import delete_node

TEST_DATABASE_URL = "sqlite:///./test_deletion.db"

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
        if os.path.exists("./test_deletion.db"):
            os.remove("./test_deletion.db")

def test_node_deletion_cleanup(db_session, tmp_path, monkeypatch):
    """
    Test that deleting a node cleans up:
    1. PostgreSQL database records (Node and BackupHistory).
    2. Node's specific backup archives inside the shared repository (/data/borg/fleet).
    3. Restricted public key from /root/.ssh/authorized_keys using its public key string.
    """
    from routers import nodes_crud

    # Deletion now dispatches a revoke task; stub it so the unit test never
    # reaches for the message broker.
    monkeypatch.setattr(
        nodes_crud.revoke_node_access_task, "apply_async", lambda *a, **k: None
    )

    db = db_session
    
    # 1. Create a dummy test node
    test_hostname = "test-delete-node-01"
    test_ip = "192.168.99.99"
    # Real, parseable keys: authorized_keys entries are now matched by
    # fingerprint, which requires a decodable key blob.
    test_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMgix3E0GojmJVKENYSNXib0XQw0PNVdj2ZrQIZxYpvk"
    
    # Ensure no pre-existing node with same details
    existing = db.query(models.Node).filter(models.Node.hostname == test_hostname).first()
    if existing:
        # Delete related backup histories first to prevent foreign key errors
        db.query(models.BackupHistory).filter(models.BackupHistory.node_id == existing.id).delete()
        db.delete(existing)
        db.commit()
        
    node = models.Node(
        hostname=test_hostname,
        ip_address=test_ip,
        ssh_port=22,
        ssh_pub_key=test_key,
        status="READY"
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    
    node_id = node.id
    
    # Add dummy BackupHistory record
    history = models.BackupHistory(
        node_id=node_id,
        archive_name="test-archive-2026",
        original_size=1000,
        deduplicated_size=500,
        status="SUCCESS"
    )
    db.add(history)
    db.commit()
    
    # 2. Setup mock filesystem resources
    repo_dir = "/data/borg/fleet"
    
    # Point the module at a throwaway authorized_keys instead of /root/.ssh.
    from core import ssh_keys
    mock_authorized_keys = str(tmp_path / "authorized_keys")
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", mock_authorized_keys)
    monkeypatch.setattr("tasks.fix_ssh_permissions", lambda: None)

    # Prepopulate authorized_keys with other keys and our test key
    mock_key_line_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGgeESfoGvSePeUP3x9YBz4NDUwzPlIXi28cA1qRcBZM key1\n"
    mock_restricted_key_line = f'command="borg serve --restrict-to-path /data/borg/fleet",no-port-forwarding,no-X11-forwarding,no-pty {test_key}\n'
    mock_key_line_3 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDdIpHgAEoo0eoVX4OLeDH5jRtyp4lth3Ash/QoGR0in key3\n"

    with open(mock_authorized_keys, "w") as f:
        f.write(mock_key_line_1)
        f.write(mock_restricted_key_line)
        f.write(mock_key_line_3)
        
    # Verify setup is correct
    with open(mock_authorized_keys, "r") as f:
        lines = f.readlines()
    assert len(lines) == 3
    assert any(test_key in line for line in lines)
    
    # 3. Call the delete_node FastAPI logic (or view function)
    real_exists = os.path.exists
    real_isfile = os.path.isfile

    def spy_exists(path):
        if path in ("/data/borg/fleet", "/data/borg/fleet/config"):
            return True
        return real_exists(path)

    def spy_isfile(path):
        # repo_paths.is_initialized looks for the repository's config file.
        if path == "/data/borg/fleet/config":
            return True
        return real_isfile(path)

    with patch('subprocess.run') as mock_run, \
         patch('os.path.isfile', side_effect=spy_isfile), \
         patch('os.path.exists', side_effect=spy_exists):
        delete_node(node_id=node_id, db=db)
        
        # Find the borg delete call and the chown repo call
        borg_delete_call = None
        chown_repo_call = None
        for call in mock_run.call_args_list:
            args = call[0][0]
            if len(args) > 0:
                if "borg" in args and "delete" in args:
                    borg_delete_call = args
                elif "chown" in args and repo_dir in args:
                    chown_repo_call = args
        
        assert borg_delete_call is not None, "Borg delete command was not called"
        assert "--glob-archives" in borg_delete_call
        assert f"{test_hostname}-*" in borg_delete_call
        assert repo_dir in borg_delete_call
        
        assert chown_repo_call is not None, "Chown command on repository directory was not called"
        assert "1000:1000" in chown_repo_call
    
    # 4. Verify cleanup assertions
    # Verify DB records are deleted
    deleted_node = db.query(models.Node).filter(models.Node.id == node_id).first()
    assert deleted_node is None
    
    deleted_history = db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).first()
    assert deleted_history is None
    
    # Verify SSH authorized_keys entry is removed
    with open(mock_authorized_keys, "r") as f:
        remaining_lines = f.readlines()
        
    assert len(remaining_lines) == 2
    assert not any(test_key in line for line in remaining_lines)
    assert remaining_lines[0] == mock_key_line_1
    assert remaining_lines[1] == mock_key_line_3
    
    db.close()


def test_delete_node_revokes_and_dispatches(db_session, tmp_path, monkeypatch):
    """Deleting a node must drop the orchestrator grant and try to clear the
    node's own authorized_keys, without letting an unreachable host block it."""
    from core import ssh_keys
    from routers import nodes_crud

    path = str(tmp_path / "authorized_keys")
    open(path, "w").close()
    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", path)
    monkeypatch.setattr("tasks.fix_ssh_permissions", lambda: None)

    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBP7VZ2m3vI0k1V3sK1vJ8xk5cQ0hE9jL2mN4pR6tU8w"
    node = models.Node(
        hostname="doomed-node", ip_address="10.0.0.9", ssh_port=22,
        ssh_pub_key=key, status="READY",
    )
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)

    ssh_keys.authorize(path, key, options=ssh_keys.BORG_SERVE_OPTIONS,
                       tag=ssh_keys.node_tag(node.id))

    dispatched = {}

    def fake_apply_async(args=None, **_kwargs):
        dispatched.update(hostname=args[0], ip_address=args[1], ssh_port=args[2])

    monkeypatch.setattr(nodes_crud.revoke_node_access_task, "apply_async", fake_apply_async)

    nodes_crud.delete_node(node.id, request=None, db=db_session, current_user=None)

    assert ssh_keys.list_entries(path) == []
    assert dispatched == {"hostname": "doomed-node", "ip_address": "10.0.0.9", "ssh_port": 22}


def test_delete_node_survives_a_failing_dispatch(db_session, tmp_path, monkeypatch):
    from core import ssh_keys
    from routers import nodes_crud

    monkeypatch.setattr(ssh_keys, "ORCHESTRATOR_AUTHORIZED_KEYS", str(tmp_path / "ak"))
    node = models.Node(hostname="doomed-2", ip_address="10.0.0.10", ssh_port=22, status="READY")
    db_session.add(node)
    db_session.commit()
    db_session.refresh(node)
    node_id = node.id

    def boom(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(nodes_crud.revoke_node_access_task, "apply_async", boom)

    nodes_crud.delete_node(node_id, request=None, db=db_session, current_user=None)

    assert db_session.query(models.Node).filter(models.Node.id == node_id).first() is None
