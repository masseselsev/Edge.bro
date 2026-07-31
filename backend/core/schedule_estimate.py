"""Estimating how long backups actually take, from recorded history.

The scheduler used to assume every backup takes 30 minutes. On the links this
product targets (down to 2 Mbit) a single 5 GB backup takes closer to six
hours, so that assumption made the "will this finish before the window closes"
maths meaningless — and drove concurrency in exactly the wrong direction.

Borg reports `deduplicated_size` per archive, which is what actually crosses
the wire, so past runs give a far better estimate than a constant.
"""
import logging
from statistics import median
from typing import List, Optional

from sqlalchemy.orm import Session

import models

logger = logging.getLogger(__name__)

# Used when there is no usable history and no configured rate limit — matches
# the historical hard-coded assumption so behaviour is unchanged for setups
# that have neither.
DEFAULT_BACKUP_MINUTES = 30.0

# How many recent successful runs to consider per node.
HISTORY_SAMPLE_SIZE = 5


def estimate_node_transfer_bytes(db: Session, node_id: int) -> Optional[int]:
    """Median transferred size of the node's recent successful backups.

    Median rather than mean so one unusually large run (a first full backup,
    or a big software rollout) doesn't skew the estimate for months.
    """
    rows = (
        db.query(models.BackupHistory.deduplicated_size)
        .filter(
            models.BackupHistory.node_id == node_id,
            models.BackupHistory.status == "SUCCESS",
            models.BackupHistory.deduplicated_size > 0,
        )
        .order_by(models.BackupHistory.timestamp.desc())
        .limit(HISTORY_SAMPLE_SIZE)
        .all()
    )
    sizes = [r[0] for r in rows if r[0]]
    if not sizes:
        return None
    return int(median(sizes))


def minutes_for_bytes(num_bytes: int, rate_kib_s: Optional[int]) -> Optional[float]:
    """Transfer time in minutes for `num_bytes` at `rate_kib_s` KiB/s."""
    if not rate_kib_s or rate_kib_s <= 0 or num_bytes <= 0:
        return None
    return (num_bytes / 1024.0) / rate_kib_s / 60.0


def estimate_node_backup_minutes(db: Session, node_id: int, rate_kib_s: Optional[int]) -> Optional[float]:
    """Expected duration of one backup of this node, or None if unknown."""
    est_bytes = estimate_node_transfer_bytes(db, node_id)
    if est_bytes is None:
        return None
    return minutes_for_bytes(est_bytes, rate_kib_s)


def estimate_group_backup_minutes(db: Session, group, nodes: List) -> float:
    """Average expected backup duration for a group's pending nodes.

    Falls back to DEFAULT_BACKUP_MINUTES when the group has no rate limit set
    or none of its nodes have usable history, so this is never worse than the
    constant it replaces.
    """
    rate = getattr(group, "upload_rate_limit", None)
    if not rate:
        return DEFAULT_BACKUP_MINUTES

    estimates = []
    for node in nodes:
        est = estimate_node_backup_minutes(db, node.id, rate)
        if est is not None:
            estimates.append(est)

    if not estimates:
        return DEFAULT_BACKUP_MINUTES

    return max(1.0, sum(estimates) / len(estimates))


# Bounds for how long a "backup is running" lock may live.
MIN_LOCK_TTL_SECONDS = 4 * 3600      # previous fixed value; floor for short backups
MAX_LOCK_TTL_SECONDS = 24 * 3600     # never hold a node hostage longer than a day
LOCK_TTL_SAFETY_FACTOR = 3           # slow links vary a lot; leave generous headroom


def backup_lock_ttl_seconds(db: Session, node_id: int, rate_kib_s: Optional[int]) -> int:
    """TTL for a node's "backup running" lock.

    This must outlive the backup itself. The lock used to be a flat 4 hours,
    but at 2 Mbit a 3.5 GB backup already exceeds that: the key expired mid-run,
    the scheduler saw a free node and started a second backup, and that run's
    pre-flight `pkill -f 'borg create'` killed the one still in progress — so a
    slow backup could never finish. Size the TTL from the node's own history.
    """
    est_minutes = estimate_node_backup_minutes(db, node_id, rate_kib_s)
    if est_minutes is None:
        return MIN_LOCK_TTL_SECONDS
    ttl = int(est_minutes * 60 * LOCK_TTL_SAFETY_FACTOR)
    return max(MIN_LOCK_TTL_SECONDS, min(MAX_LOCK_TTL_SECONDS, ttl))
