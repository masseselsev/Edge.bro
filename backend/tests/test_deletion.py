import os
import shutil
import pytest
from sqlalchemy.orm import Session
from database import SessionLocal
import models
import main

def test_node_deletion_cleanup():
    """
    Test that deleting a node cleans up:
    1. PostgreSQL database records (Node and BackupHistory).
    2. Borg repository directory on disk (/data/borg/{hostname}).
    3. Restricted public key from /root/.ssh/authorized_keys.
    """
    db = SessionLocal()
    
    # 1. Create a dummy test node
    test_hostname = "test-delete-node-01"
    test_ip = "192.168.99.99"
    
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
    # Borg repository directory
    repo_dir = f"/data/borg/{test_hostname}"
    os.makedirs(repo_dir, exist_ok=True)
    with open(os.path.join(repo_dir, "config"), "w") as f:
        f.write("mock borg config")
        
    # SSH keys file
    ssh_dir = "/root/.ssh"
    os.makedirs(ssh_dir, exist_ok=True)
    authorized_keys_path = os.path.join(ssh_dir, "authorized_keys")
    
    # Prepopulate authorized_keys with other keys and our test key
    mock_key_line_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPsomeotherkey key1\n"
    mock_restricted_key_line = f'command="borg serve --restrict-to-path /data/borg/{test_hostname}",no-port-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPtestkey key2\n'
    mock_key_line_3 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPanotherkey key3\n"
    
    with open(authorized_keys_path, "w") as f:
        f.write(mock_key_line_1)
        f.write(mock_restricted_key_line)
        f.write(mock_key_line_3)
        
    # Verify setup is correct
    assert os.path.exists(os.path.join(repo_dir, "config"))
    with open(authorized_keys_path, "r") as f:
        lines = f.readlines()
    assert len(lines) == 3
    assert any(test_hostname in line for line in lines)
    
    # 3. Call the delete_node FastAPI logic (or view function)
    # We call main.delete_node directly with db session dependency
    main.delete_node(node_id=node_id, db=db)
    
    # 4. Verify cleanup assertions
    # Verify DB records are deleted
    deleted_node = db.query(models.Node).filter(models.Node.id == node_id).first()
    assert deleted_node is None
    
    deleted_history = db.query(models.BackupHistory).filter(models.BackupHistory.node_id == node_id).first()
    assert deleted_history is None
    
    # Verify Borg repository directory is gone
    assert not os.path.exists(repo_dir)
    
    # Verify SSH authorized_keys entry is removed
    assert os.path.exists(authorized_keys_path)
    with open(authorized_keys_path, "r") as f:
        remaining_lines = f.readlines()
        
    assert len(remaining_lines) == 2
    assert not any(test_hostname in line for line in remaining_lines)
    assert remaining_lines[0] == mock_key_line_1
    assert remaining_lines[1] == mock_key_line_3
    
    # Cleanup keys file
    os.remove(authorized_keys_path)
    db.close()
