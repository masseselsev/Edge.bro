"""Estimating how long backups actually take, from recorded history.

The scheduler used to assume every backup takes 30 minutes. On the links this
product targets (down to 2 Mbit) a single 5 GB backup takes closer to six
hours, so that assumption made the "will this finish before the window closes"
maths meaningless — and drove concurrency in exactly the wrong direction.

There are four sources for that estimate, tried in order of how directly they
answer the question:

1. **Measured wall time.** `BackupHistory.duration_seconds` is what the run
   actually took. Nothing beats it, and it needs no assumption about link
   speed at all.
2. **The fleet's full-backup time**, for a node that has never run. The first
   backup of a node is a whole disk image and every one after it is an
   increment, so borrowing another node's *typical* run would understate it by
   orders of magnitude. Borrowing another node's *longest* run does not.
3. **Bytes over the configured rate limit.** What this module did originally.
   Still the best available answer when nothing has ever run and the operator
   has told us the link speed.
4. **A flat constant**, only when none of the above apply.

The order matters more than it looks. Before this, the chain effectively began
at 3 and fell straight to 4 whenever a group had no rate limit — without
consulting history at all — so a node doing thirty-second increments and a node
doing six-hour transfers were both projected at thirty minutes.
"""
import logging
from statistics import median
from typing import Dict, List, Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

import models

logger = logging.getLogger(__name__)

# Used when there is no usable history and no configured rate limit — matches
# the historical hard-coded assumption so behaviour is unchanged for setups
# that have neither.
DEFAULT_BACKUP_MINUTES = 30.0

# How many recent successful runs to consider per node.
HISTORY_SAMPLE_SIZE = 5

#: Where a duration estimate came from. Surfaced so the UI can distinguish a
#: measured figure from a guess instead of showing both with equal confidence.
SOURCE_MEASURED = "measured"
SOURCE_FLEET_FIRST = "fleet_first_backup"
SOURCE_RATE_LIMIT = "rate_limit"
SOURCE_DEFAULT = "default"


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
    """Expected duration of one backup of this node, or None if unknown.

    Kept returning None rather than the constant, because two callers want to
    tell "no idea" apart from "half an hour". Use `DurationEstimator` where a
    whole set of nodes is being priced — this issues its queries per node.
    """
    measured = measured_node_minutes(db, node_id)
    if measured is not None:
        return measured

    est_bytes = estimate_node_transfer_bytes(db, node_id)
    if est_bytes is None:
        return None
    return minutes_for_bytes(est_bytes, rate_kib_s)


def measured_minutes_for_nodes(db: Session, node_ids: List[int]) -> Dict[int, float]:
    """Median measured wall time of each node's recent successful runs.

    Median, not mean: a node's first run is a full disk image and every one
    after it is an increment, so a sample of one six-hour run and four
    three-minute ones has to price as three minutes. A mean would call it an
    hour and a quarter, which is true of no backup that node has ever run.
    """
    if not node_ids:
        return {}

    rows = (
        db.query(
            models.BackupHistory.node_id,
            models.BackupHistory.duration_seconds,
        )
        .filter(
            models.BackupHistory.node_id.in_(node_ids),
            models.BackupHistory.status == "SUCCESS",
            models.BackupHistory.duration_seconds > 0,
        )
        .order_by(
            models.BackupHistory.node_id.asc(),
            models.BackupHistory.timestamp.desc(),
        )
        .all()
    )

    samples: Dict[int, List[float]] = {}
    for node_id, seconds in rows:
        bucket = samples.setdefault(node_id, [])
        # Ordered newest-first within each node, so the first N are the sample.
        if len(bucket) < HISTORY_SAMPLE_SIZE and seconds:
            bucket.append(seconds)

    return {
        node_id: median(values) / 60.0
        for node_id, values in samples.items()
        if values
    }


def measured_node_minutes(db: Session, node_id: int) -> Optional[float]:
    """Measured duration for a single node, or None if it has never run."""
    return measured_minutes_for_nodes(db, [node_id]).get(node_id)


def fleet_full_backup_minutes(db: Session) -> Optional[float]:
    """Typical time for a node's *first* backup, learned from the rest of the fleet.

    A node that has never run has no history to price, and it is precisely the
    run that costs the most: a whole disk image rather than an increment.

    Identified as each node's **longest** successful run rather than its
    earliest, for the same reason `core/backup_stats` picks base backups by
    contribution rather than by age — retention prunes old archives and the
    daily job deletes the matching history rows, so a node's earliest surviving
    record is frequently an increment. Length survives pruning; age does not.

    Taking the median *across nodes* of those per-node maxima keeps one node's
    pathological run (a link that dropped halfway) from setting the figure for
    everybody.
    """
    longest = (
        db.query(func.max(models.BackupHistory.duration_seconds))
        .filter(
            models.BackupHistory.status == "SUCCESS",
            models.BackupHistory.duration_seconds > 0,
        )
        .group_by(models.BackupHistory.node_id)
        .all()
    )
    values = [row[0] for row in longest if row[0]]
    if not values:
        return None
    return median(values) / 60.0


class DurationEstimator:
    """Backup-duration estimates for a set of nodes, resolved in bulk.

    The per-node functions above issue a query each. Both the scheduler tick
    and the load projection price every pending node on every pass, so asking
    them one at a time is the N+1 this class exists to avoid: it runs three
    queries for the whole set, then answers from memory.
    """

    def __init__(self, db: Session, node_ids: List[int]):
        ids = list(node_ids)
        self._measured = measured_minutes_for_nodes(db, ids)
        self._bytes = estimate_transfer_bytes_for_nodes(db, ids)
        # One query regardless of fleet size, and only worth making when some
        # node in the set actually lacks history.
        self._fleet_first = (
            fleet_full_backup_minutes(db)
            if any(i not in self._measured for i in ids)
            else None
        )

    def resolve(self, node_id: int, rate_kib_s: Optional[int]) -> tuple[float, str]:
        """Estimated minutes and which source produced them."""
        measured = self._measured.get(node_id)
        if measured is not None:
            return measured, SOURCE_MEASURED

        if self._fleet_first is not None:
            return self._fleet_first, SOURCE_FLEET_FIRST

        est_bytes = self._bytes.get(node_id)
        if est_bytes is not None:
            minutes = minutes_for_bytes(est_bytes, rate_kib_s)
            if minutes is not None:
                return minutes, SOURCE_RATE_LIMIT

        return DEFAULT_BACKUP_MINUTES, SOURCE_DEFAULT

    def minutes(self, node_id: int, rate_kib_s: Optional[int]) -> float:
        return self.resolve(node_id, rate_kib_s)[0]

    def is_measured(self, node_id: int) -> bool:
        """Whether this node's figure came from its own recorded runs."""
        return node_id in self._measured


def estimate_transfer_bytes_for_nodes(db: Session, node_ids: List[int]) -> dict:
    """Median recent transfer size for several nodes, in one query.

    The per-node form issues a query each, which the scheduler then ran once
    per pending node on every 60-second tick. Here the rows for the whole set
    are fetched together and the per-node sample is taken in Python.
    """
    if not node_ids:
        return {}

    rows = (
        db.query(
            models.BackupHistory.node_id,
            models.BackupHistory.deduplicated_size,
        )
        .filter(
            models.BackupHistory.node_id.in_(node_ids),
            models.BackupHistory.status == "SUCCESS",
            models.BackupHistory.deduplicated_size > 0,
        )
        .order_by(
            models.BackupHistory.node_id.asc(),
            models.BackupHistory.timestamp.desc(),
        )
        .all()
    )

    samples: dict = {}
    for node_id, size in rows:
        bucket = samples.setdefault(node_id, [])
        # Ordered newest-first within each node, so the first N are the sample.
        if len(bucket) < HISTORY_SAMPLE_SIZE and size:
            bucket.append(size)

    return {node_id: int(median(sizes)) for node_id, sizes in samples.items() if sizes}


def estimate_group_backup_minutes(db: Session, group, nodes: List) -> float:
    """Average expected backup duration for a group's pending nodes.

    Every node contributes its best available estimate, so a group with no
    rate limit is no longer priced at the flat constant — its nodes have
    measured history, and that history is what a group of long-established
    nodes should be judged on.
    """
    if not nodes:
        return DEFAULT_BACKUP_MINUTES

    rate = getattr(group, "upload_rate_limit", None)
    estimator = DurationEstimator(db, [n.id for n in nodes])
    estimates = [estimator.minutes(n.id, rate) for n in nodes]

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

    Now sized from measured wall time where the node has any, which is the
    figure a TTL actually needs — how long the run takes, not how long its
    bytes would take at an assumed rate. The floor, ceiling and safety factor
    are unchanged, so this can only move the TTL within bounds that were
    already considered safe.
    """
    est_minutes = estimate_node_backup_minutes(db, node_id, rate_kib_s)
    if est_minutes is None:
        return MIN_LOCK_TTL_SECONDS
    ttl = int(est_minutes * 60 * LOCK_TTL_SAFETY_FACTOR)
    return max(MIN_LOCK_TTL_SECONDS, min(MAX_LOCK_TTL_SECONDS, ttl))
