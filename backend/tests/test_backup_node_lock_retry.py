"""run_backup_task's response to NodeLockBusy: retry via Celery instead of
failing outright, without releasing the local `backup_running` lock while the
retry is pending — releasing it would let this orchestrator's own scheduler
dispatch a second backup for the same node during the countdown.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from celery.app.task import Task
from celery.exceptions import MaxRetriesExceededError, Retry

from core.node_lock import (
    NODE_LOCK_MAX_RETRIES,
    NODE_LOCK_RETRY_COUNTDOWN_SECONDS,
    NodeLockBusy,
)


@contextmanager
def _fake_session_scope():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    yield db


def _fake_plan(node_id):
    return MagicMock(node_id=node_id, status="NEEDS_FIX", lock_ttl=3600)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_node_lock_busy_retries_instead_of_failing(mock_plan, mock_transfer, mock_redis, monkeypatch):
    from backup_tasks import run_backup_task

    mock_transfer.side_effect = NodeLockBusy("Node is busy")
    fake_redis = MagicMock()
    mock_redis.return_value = fake_redis

    monkeypatch.setattr("backup_tasks.session_scope", _fake_session_scope)

    mock_request = MagicMock()
    mock_request.id = "task-retry-1"
    monkeypatch.setattr(Task, "request", mock_request)

    retry_calls = []

    def fake_retry(self, exc=None, countdown=None, max_retries=None):
        retry_calls.append({"countdown": countdown, "max_retries": max_retries})
        raise Retry()

    monkeypatch.setattr(Task, "retry", fake_retry)

    logged = []
    monkeypatch.setattr(
        "backup_tasks.log_to_task",
        lambda task_id, msg, status=None: logged.append((msg, status)),
    )

    with pytest.raises(Retry):
        run_backup_task.run(1)

    assert len(retry_calls) == 1
    assert retry_calls[0]["countdown"] == NODE_LOCK_RETRY_COUNTDOWN_SECONDS
    assert retry_calls[0]["max_retries"] == NODE_LOCK_MAX_RETRIES
    assert any("[WAITING]" in msg for msg, _status in logged)
    # Must NOT release the node lock while a retry is pending.
    fake_redis.delete.assert_not_called()


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_node_lock_busy_gives_up_after_max_retries(mock_plan, mock_transfer, mock_redis, monkeypatch):
    from backup_tasks import run_backup_task

    mock_transfer.side_effect = NodeLockBusy("Node is busy")
    fake_redis = MagicMock()
    mock_redis.return_value = fake_redis

    monkeypatch.setattr("backup_tasks.session_scope", _fake_session_scope)

    mock_request = MagicMock()
    mock_request.id = "task-retry-2"
    monkeypatch.setattr(Task, "request", mock_request)

    def fake_retry(self, exc=None, countdown=None, max_retries=None):
        raise MaxRetriesExceededError()

    monkeypatch.setattr(Task, "retry", fake_retry)

    logged = []
    monkeypatch.setattr(
        "backup_tasks.log_to_task",
        lambda task_id, msg, status=None: logged.append((msg, status)),
    )

    result = run_backup_task.run(1)

    assert result["status"] == "FAILED"
    assert any(status == "FAILED" for _msg, status in logged)
    # The final, terminal outcome must release the node lock.
    deleted = {c.args[0] for c in fake_redis.delete.call_args_list}
    assert deleted == {"backup_running:1", "backup_speed:1", "backup_cancel:1"}
