"""Celery application and its runtime configuration.

The important part of this file is the queue split. Every task used to land in
one default queue served by one worker pool, which meant a multi-hour
`run_backup_task` and a 30-second ping sweep competed for the same slots. With
Celery's default prefetch of 4, a handful of long backups could hold every
prefetched slot while the periodic tasks queued up behind them — so the
scheduler tick and the reachability sweep silently stopped running at exactly
the moment the fleet was busiest.
"""
import os

from celery import Celery
from kombu import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

#: Long, serial, I/O-bound work: borg transfers and ansible runs.
QUEUE_BACKUPS = "backups"
#: Short work that must stay responsive — the beat-driven sweeps.
QUEUE_PERIODIC = "periodic"
#: ISO builds. Rare, very long, and disk-heavy; isolated so they cannot block
#: anything else.
QUEUE_ISO = "iso"

celery_app.conf.update(
    task_default_queue=QUEUE_PERIODIC,
    task_queues=(
        Queue(QUEUE_PERIODIC),
        Queue(QUEUE_BACKUPS),
        Queue(QUEUE_ISO),
    ),
    # Explicit routing. Anything not named here falls to the periodic queue,
    # which is the right default: unrouted tasks are short by assumption, and
    # a short task on the long queue is a worse mistake than the reverse.
    task_routes={
        "backup_tasks.run_backup_task": {"queue": QUEUE_BACKUPS},
        "backup_tasks.run_prepare_task": {"queue": QUEUE_BACKUPS},
        "backup_tasks.global_daily_prune": {"queue": QUEUE_BACKUPS},
        "tasks.run_bootstrap_task": {"queue": QUEUE_BACKUPS},
        "tasks.revoke_node_access_task": {"queue": QUEUE_BACKUPS},
        "tasks.harvest_node_task": {"queue": QUEUE_BACKUPS},
        # Registered under the module that defines them, not the tasks package.
        "restore_tasks.purge_node_archives": {"queue": QUEUE_BACKUPS},
        "restore_tasks.flash_restore_device": {"queue": QUEUE_BACKUPS},
        "iso_tasks.*": {"queue": QUEUE_ISO},
        "tasks.download_base_iso_task": {"queue": QUEUE_ISO},
        "tasks.generate_client_iso_task": {"queue": QUEUE_ISO},
        "tasks.repack_kiosk_iso_task": {"queue": QUEUE_ISO},
    },

    # One task at a time per child. These tasks are long and hold a database
    # session and often a subprocess; hoarding four of them per child is how
    # short tasks end up waiting behind hours of backup.
    worker_prefetch_multiplier=1,

    # Requeue on worker death rather than losing the task. Safe here because
    # the tasks that matter are guarded by their own Redis locks, so a
    # redelivered backup does not start a second concurrent run.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # A backup of a large node over a slow link can legitimately run for
    # hours; anything past this is stuck rather than slow. The soft limit
    # raises an exception the task can still clean up from.
    task_soft_time_limit=int(os.getenv("CELERY_SOFT_TIME_LIMIT", str(6 * 3600))),
    task_time_limit=int(os.getenv("CELERY_TIME_LIMIT", str(7 * 3600))),

    # Results are only ever read to answer "is this task still running", which
    # the callers do within minutes. Keeping them for the default day fills
    # Redis with dead payloads.
    result_expires=int(os.getenv("CELERY_RESULT_EXPIRES", "3600")),

    timezone="UTC",
    enable_utc=True,
)
