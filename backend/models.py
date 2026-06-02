from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, ForeignKey, JSON
from sqlalchemy.sql import func
from database import Base

class Settings(Base):
    """
    Settings model for global orchestrator configuration.
    Borg passphrase is read from env instead of the DB.
    """
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True, index=True)
    borg_ssh_port = Column(Integer, default=12345, nullable=False)
    borg_repo_path = Column(String, default='/data/borg', nullable=False)
    keep_daily = Column(Integer, default=7, nullable=False)
    keep_weekly = Column(Integer, default=4, nullable=False)
    keep_monthly = Column(Integer, default=6, nullable=False)
    global_exclusions = Column(Text, default='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1', nullable=False)
    orchestrator_ip = Column(String, default='', nullable=False)


class Node(Base):
    """
    Node model tracking physical Debian edge node configurations and statuses.
    """
    __tablename__ = 'nodes'

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String, unique=True, index=True, nullable=False)
    ip_address = Column(String, unique=True, index=True, nullable=False)
    ssh_port = Column(Integer, default=22, nullable=False)
    status = Column(String, default='NEEDS_BOOTSTRAP', nullable=False) # OFFLINE, NEEDS_BOOTSTRAP, NEEDS_FIX, READY
    last_backup = Column(DateTime, nullable=True)
    disk_type = Column(String, default='UNKNOWN', nullable=False) # SATA, NVME, UNKNOWN
    network_iface = Column(String, nullable=True)
    ssh_pub_key = Column(Text, nullable=True)
    efi_uuid = Column(String, nullable=True) # Used to maintain exact ESP filesystem UUID during flasher restore
    partition_layout = Column(JSON, nullable=True)


class BackupHistory(Base):
    """
    BackupHistory model containing compression metrics and execution logs for historical archives.
    """
    __tablename__ = 'backup_history'

    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey('nodes.id'), nullable=False)
    archive_name = Column(String, unique=True, index=True, nullable=False)
    timestamp = Column(DateTime, default=func.now(), nullable=False)
    original_size = Column(BigInteger, nullable=False) # Original uncompressed size
    deduplicated_size = Column(BigInteger, nullable=False) # Deduplicated storage size
    status = Column(String, nullable=False) # SUCCESS, FAILED
    log_output = Column(Text, nullable=True)


class TaskLog(Base):
    """
    TaskLog model storing execution progress and logs for frontend console streaming.
    """
    __tablename__ = 'task_logs'

    id = Column(String, primary_key=True, index=True) # UUID string representation
    task_type = Column(String, nullable=False) # BOOTSTRAP, PREPARE, BACKUP, RESTORE
    status = Column(String, default='PENDING', nullable=False) # PENDING, RUNNING, SUCCESS, FAILED
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    log_output = Column(Text, default='', nullable=False)
