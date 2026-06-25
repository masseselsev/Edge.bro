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

def test_node_deletion_cleanup(db_session):
    """
    Test that deleting a node cleans up:
    1. PostgreSQL database records (Node and BackupHistory).
    2. Node's specific backup archives inside the shared repository (/data/borg/fleet).
    3. Restricted public key from /root/.ssh/authorized_keys using its public key string.
    """
    db = db_session
    
    # 1. Create a dummy test node
    test_hostname = "test-delete-node-01"
    test_ip = "192.168.99.99"
    test_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPtestkey key2"
    
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
    
    # Mock authorized_keys file path redirection
    import builtins
    mock_authorized_keys = "/tmp/mock_authorized_keys"
    
    # Prepopulate authorized_keys with other keys and our test key
    mock_key_line_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPsomeotherkey key1\n"
    mock_restricted_key_line = f'command="borg serve --restrict-to-path /data/borg/fleet",no-port-forwarding,no-X11-forwarding,no-pty {test_key}\n'
    mock_key_line_3 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPanotherkey key3\n"
    
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
    def spy_exists(path):
        if path in ("/data/borg/fleet", "/data/borg/fleet/config", "/root/.ssh/authorized_keys"):
            return True
        return real_exists(path)

    real_open = builtins.open
    def spy_open(file, *args, **kwargs):
        if file == "/root/.ssh/authorized_keys":
            return real_open(mock_authorized_keys, *args, **kwargs)
        return real_open(file, *args, **kwargs)

    with patch('subprocess.run') as mock_run, \
         patch('os.path.exists', side_effect=spy_exists), \
         patch('builtins.open', side_effect=spy_open):
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
    
    # Cleanup keys file
    if os.path.exists(mock_authorized_keys):
        os.remove(mock_authorized_keys)
    db.close()
