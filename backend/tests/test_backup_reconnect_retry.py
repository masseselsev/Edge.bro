"""run_backup_task's response to the link dropping mid-transfer.

A node behind a flaky tunnel loses its connection partway through `borg
create`. Nothing is wrong with the node, the repository or the data — the
link went away — and borg has been writing checkpoints all along, so the next
attempt skips everything already transferred. Failing the run outright throws
that away and waits for the scheduler's cooldown, which for a manually
triggered backup never comes at all.

So a connection loss retries, and only records a failure once the retries are
spent. Nothing is written to BackupHistory in between: a blip the retry
recovers from is not a failed backup, and a FAILED row would additionally put
the node into the scheduler's retry cooldown while our own retry is pending.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from celery.app.task import Task
from celery.exceptions import MaxRetriesExceededError, Retry

from core.transfer_retry import (
    REPO_BUSY_MAX_RETRIES,
    REPO_BUSY_RETRY_COUNTDOWN_SECONDS,
    RepositoryBusy,
    CONNECTION_LOST_MAX_RETRIES,
    CONNECTION_LOST_RETRY_COUNTDOWN_SECONDS,
    BackupConnectionLost,
)


def _lost_connection():
    return BackupConnectionLost(
        "Connection to node lost during transfer",
        archive_name="WS-20260825101500",
        log_output="Timeout, server 10.200.20.190 not responding.",
        duration_seconds=1234.0,
    )


@contextmanager
def _fake_session_scope():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    _fake_session_scope.added.append(db)
    yield db


def _fake_plan(node_id):
    return MagicMock(node_id=node_id, status="NEEDS_FIX", lock_ttl=3600)


def _install(monkeypatch, task_id):
    """The shared harness: a plan, a session, a request id and a log sink."""
    _fake_session_scope.added = []
    monkeypatch.setattr("backup_tasks.session_scope", _fake_session_scope)

    mock_request = MagicMock()
    mock_request.id = task_id
    monkeypatch.setattr(Task, "request", mock_request)

    logged = []
    monkeypatch.setattr(
        "backup_tasks.log_to_task",
        lambda task_id, msg, status=None: logged.append((msg, status)),
    )
    return logged


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_a_lost_connection_retries_instead_of_failing(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task

    mock_transfer.side_effect = _lost_connection()
    fake_redis = MagicMock()
    mock_redis.return_value = fake_redis

    logged = _install(monkeypatch, "task-reconnect-1")

    retry_calls = []

    def fake_retry(self, exc=None, countdown=None, max_retries=None):
        retry_calls.append({"countdown": countdown, "max_retries": max_retries})
        raise Retry()

    monkeypatch.setattr(Task, "retry", fake_retry)

    with pytest.raises(Retry):
        run_backup_task.run(1)

    assert len(retry_calls) == 1
    assert retry_calls[0]["countdown"] == CONNECTION_LOST_RETRY_COUNTDOWN_SECONDS
    assert retry_calls[0]["max_retries"] == CONNECTION_LOST_MAX_RETRIES
    # Must NOT release the node lock while a retry is pending, or this
    # orchestrator's own scheduler dispatches a second backup for the node.
    fake_redis.delete.assert_not_called()
    assert any("[RECONNECTING]" in msg for msg, _status in logged)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_nothing_is_recorded_while_a_retry_is_still_pending(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    """A blip the retry recovers from is not a failed backup."""
    from backup_tasks import run_backup_task
    import models

    mock_transfer.side_effect = _lost_connection()
    mock_redis.return_value = MagicMock()

    _install(monkeypatch, "task-reconnect-2")
    monkeypatch.setattr(Task, "retry", lambda self, **kw: (_ for _ in ()).throw(Retry()))

    with pytest.raises(Retry):
        run_backup_task.run(1)

    added = [c.args[0] for db in _fake_session_scope.added for c in db.add.call_args_list]
    assert not any(isinstance(obj, models.BackupHistory) for obj in added)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_the_failure_is_recorded_once_the_retries_are_spent(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task
    import models

    mock_transfer.side_effect = _lost_connection()
    fake_redis = MagicMock()
    mock_redis.return_value = fake_redis

    logged = _install(monkeypatch, "task-reconnect-3")

    def exhausted(self, **kwargs):
        raise MaxRetriesExceededError()

    monkeypatch.setattr(Task, "retry", exhausted)

    result = run_backup_task.run(1)

    assert result["status"] == "FAILED"

    added = [c.args[0] for db in _fake_session_scope.added for c in db.add.call_args_list]
    history = [obj for obj in added if isinstance(obj, models.BackupHistory)]
    assert len(history) == 1, "the operator must still see that the backup did not happen"
    assert history[0].status == "FAILED"
    assert history[0].archive_name == "WS-20260825101500"
    assert history[0].error_category == "TIMEOUT"
    assert history[0].duration_seconds == 1234.0

    # The node lock goes now: no further attempt is pending. So does the live
    # speed, which would otherwise sit there until its TTL, and any stop
    # request naming this run, which is spent once the run has ended.
    deleted = {c.args[0] for c in fake_redis.delete.call_args_list}
    assert deleted == {"backup_running:1", "backup_speed:1", "backup_cancel:1"}
    assert any(status == "FAILED" for _msg, status in logged)


# --- a repository someone else is writing to ---
#
# Borg allows one writer per repository and holds the lock for the whole of
# `borg create`, so two backups bound for one shard serialise. The loser used
# to sit out --lock-wait and then be recorded as a failed backup, which is how
# a fleet on a single shard turned an ordinary queue into REPO_LOCKED failures.
# It is the same situation as a busy node: come back later, do not fail.

def _repository_busy():
    return RepositoryBusy(
        "Repository is busy",
        archive_name="WS-20260825163300",
        log_output="Failed to create/acquire the lock /data/borg/fleet/lock.exclusive (timeout).",
        duration_seconds=600.0,
    )


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_a_busy_repository_queues_instead_of_failing(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task

    mock_transfer.side_effect = _repository_busy()
    fake_redis = MagicMock()
    mock_redis.return_value = fake_redis

    logged = _install(monkeypatch, "task-repo-busy-1")

    retry_calls = []

    def fake_retry(self, exc=None, countdown=None, max_retries=None):
        retry_calls.append({"countdown": countdown, "max_retries": max_retries})
        raise Retry()

    monkeypatch.setattr(Task, "retry", fake_retry)

    with pytest.raises(Retry):
        run_backup_task.run(1)

    assert len(retry_calls) == 1
    assert retry_calls[0]["countdown"] == REPO_BUSY_RETRY_COUNTDOWN_SECONDS
    assert retry_calls[0]["max_retries"] == REPO_BUSY_MAX_RETRIES
    fake_redis.delete.assert_not_called()
    assert any("[QUEUED]" in msg for msg, _status in logged)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_waiting_on_a_repository_is_not_recorded_as_a_backup(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task
    import models

    mock_transfer.side_effect = _repository_busy()
    mock_redis.return_value = MagicMock()

    _install(monkeypatch, "task-repo-busy-2")
    monkeypatch.setattr(Task, "retry", lambda self, **kw: (_ for _ in ()).throw(Retry()))

    with pytest.raises(Retry):
        run_backup_task.run(1)

    added = [c.args[0] for db in _fake_session_scope.added for c in db.add.call_args_list]
    assert not any(isinstance(obj, models.BackupHistory) for obj in added)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_a_repository_that_never_frees_up_is_reported_in_the_end(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task
    import models

    mock_transfer.side_effect = _repository_busy()
    mock_redis.return_value = MagicMock()

    _install(monkeypatch, "task-repo-busy-3")
    monkeypatch.setattr(
        Task, "retry", lambda self, **kw: (_ for _ in ()).throw(MaxRetriesExceededError())
    )

    result = run_backup_task.run(1)

    assert result["status"] == "FAILED"
    added = [c.args[0] for db in _fake_session_scope.added for c in db.add.call_args_list]
    history = [obj for obj in added if isinstance(obj, models.BackupHistory)]
    assert len(history) == 1
    assert history[0].error_category == "REPO_LOCKED"
