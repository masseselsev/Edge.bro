from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, ForeignKey, JSON, Boolean
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
    global_exclusions = Column(Text, default='/dev/*,/proc/*,/sys/*,/run/*,/mnt/*,/media/*,/lost+found,/var/log/edge/*,/var/opt/edge/blobstore/*,/var/spool/edge/*,/var/log/journal/*,/var/log/**/*.gz,/var/log/**/*.1', nullable=False)
    orchestrator_ip = Column(String, default='', nullable=False)
    timezone = Column(String, default='Browser Local', nullable=False)
    language = Column(String, default='en', nullable=False)
    retention_policy = Column(JSON, nullable=True)
    default_compression = Column(String, default='zstd:3', nullable=False)
    default_cpu_quota = Column(Integer, nullable=True)   # % of one core, NULL = no limit
    server_ips = Column(JSON, nullable=True)
    max_kiosk_isos = Column(Integer, default=5, nullable=False)




class BackupGroup(Base):
    """
    BackupGroup model tracking node schedules and allowed time windows.
    """
    __tablename__ = 'backup_groups'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    interval = Column(String, nullable=False)  # weekly, monthly, quarterly, yearly
    target_week = Column(Integer, default=1, nullable=False)
    start_time = Column(String, default="02:00", nullable=False)
    end_time = Column(String, default="05:00", nullable=False)
    concurrency_limit = Column(Integer, default=5, nullable=False)
    randomize_days = Column(Boolean, default=True, nullable=False)
    timezone = Column(String, default='UTC', nullable=False)
    override_retention = Column(Boolean, default=False, nullable=False)
    retention_policy = Column(JSON, nullable=True)

    # Resource limits
    upload_rate_limit = Column(Integer, nullable=True)   # KiB/s, NULL = unlimited
    compression = Column(String, nullable=True)           # e.g. "zstd:3", NULL = global default
    checkpoint_interval = Column(Integer, nullable=True)  # seconds, NULL = auto-calculate
    cpu_quota = Column(Integer, nullable=True)            # % of one core, NULL = no limit


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
    os_version = Column(String, nullable=True)
    
    # Scheduler & Automated Backup fields
    group_id = Column(Integer, ForeignKey('backup_groups.id'), nullable=True)
    backup_paused = Column(Boolean, default=False, nullable=False)
    backup_today = Column(Boolean, default=False, nullable=False)
    missed_window = Column(Boolean, default=False, nullable=False)
    
    # Hardware & Software attributes
    cpu_info = Column(String, nullable=True)
    memory_info = Column(String, nullable=True)
    edge_version = Column(String, nullable=True)
    notes = Column(Text, nullable=True)


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
    comment = Column(Text, nullable=True)


class TaskLog(Base):
    """
    TaskLog model storing execution progress and logs for frontend console streaming.
    """
    __tablename__ = 'task_logs'

    id = Column(String, primary_key=True, index=True) # UUID string representation
    task_type = Column(String, nullable=False) # BOOTSTRAP, PREPARE, BACKUP, RESTORE
    status = Column(String, default='PENDING', nullable=False) # PENDING, RUNNING, SUCCESS, FAILED
    node_id = Column(Integer, ForeignKey('nodes.id', ondelete='CASCADE'), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    log_output = Column(Text, default='', nullable=False)


class SystemLog(Base):
    """
    Model for general system/application logs.
    """
    __tablename__ = 'system_logs'

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class AuditLog(Base):
    """
    Model for recording user action audits (logging who did what, from where, and when).
    """
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class Kiosk(Base):
    """
    Model for dynamic Kiosk connection and pairing.
    """
    __tablename__ = 'kiosks'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    uuid = Column(String, unique=True, index=True, nullable=False)
    key = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default='PENDING', nullable=False) # PENDING, APPROVED, REVOKED
    ip_address = Column(String, nullable=True)
    ssh_pub_key = Column(Text, nullable=True)
    auth_token = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    contact = Column(String, nullable=True)
    comment = Column(Text, nullable=True)



class User(Base):
    """
    Model for administrator and superadmin user accounts.
    """
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    telegram_id = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    is_superadmin = Column(Boolean, default=False, nullable=False)
    is_admin_plus = Column(Boolean, default=False, nullable=False)


