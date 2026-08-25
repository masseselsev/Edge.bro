"""Stopping a backup that is waiting its turn.

A backup queueing behind another on the same repository retries for up to a
day. That is the right default — the queue does clear — but it leaves an
operator who queued a run by mistake, or who needs the repository for
something else, with no way to call it off.

The flag carries the task id it is cancelling rather than a bare "stop".
A backup that has already given up and been replaced by the next scheduled
run must not be killed by the previous run's leftover flag, and a node is
identified by its id in both keys, so without the task id the two are
indistinguishable.
"""
from unittest.mock import MagicMock

from core import transfer_retry as tr


def _redis():
    """A dictionary pretending to be Redis, for the two calls used here."""
    store = {}
    client = MagicMock()
    client.setex.side_effect = lambda k, ttl, v: store.__setitem__(k, v)
    client.get.side_effect = lambda k: store.get(k)
    client.delete.side_effect = lambda k: store.pop(k, None)
    client._store = store
    return client


def test_no_cancellation_asked_for_means_carry_on():
    client = _redis()
    assert tr.cancel_requested(client, node_id=1, task_id="task-a") is False


def test_a_cancellation_stops_the_task_it_names():
    client = _redis()
    tr.request_cancel(client, node_id=1, task_id="task-a")
    assert tr.cancel_requested(client, node_id=1, task_id="task-a") is True


def test_a_cancellation_does_not_touch_the_run_that_replaced_it():
    """The stale-flag case: the cancelled run is gone and the scheduler has
    started a fresh one for the same node."""
    client = _redis()
    tr.request_cancel(client, node_id=1, task_id="task-a")
    assert tr.cancel_requested(client, node_id=1, task_id="task-b") is False


def test_a_cancellation_is_scoped_to_its_own_node():
    client = _redis()
    tr.request_cancel(client, node_id=1, task_id="task-a")
    assert tr.cancel_requested(client, node_id=2, task_id="task-a") is False


def test_clearing_a_cancellation_lets_the_node_be_backed_up_again():
    client = _redis()
    tr.request_cancel(client, node_id=1, task_id="task-a")
    tr.clear_cancel(client, node_id=1)
    assert tr.cancel_requested(client, node_id=1, task_id="task-a") is False


def test_a_redis_that_is_away_does_not_cancel_anything():
    """Guessing "cancelled" wrongly aborts a healthy backup, so an unreadable
    flag means carry on."""
    client = MagicMock()
    client.get.side_effect = OSError("redis is away")
    assert tr.cancel_requested(client, node_id=1, task_id="task-a") is False


# --- the task's own response to being stopped ---

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from celery.app.task import Task


@contextmanager
def _fake_session_scope():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    _fake_session_scope.added.append(db)
    yield db


def _fake_plan(node_id):
    return MagicMock(node_id=node_id, status="NEEDS_FIX", lock_ttl=3600)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_a_stopped_run_gives_up_its_place_without_transferring(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    """The queued case: the operator pressed Stop while this run was waiting
    out a busy repository. It must notice before starting a transfer."""
    from backup_tasks import run_backup_task

    fake_redis = MagicMock()
    fake_redis.get.side_effect = lambda k: b"task-cancel-1" if k == "backup_cancel:1" else None
    mock_redis.return_value = fake_redis

    _fake_session_scope.added = []
    monkeypatch.setattr("backup_tasks.session_scope", _fake_session_scope)
    mock_request = MagicMock()
    mock_request.id = "task-cancel-1"
    monkeypatch.setattr(Task, "request", mock_request)
    logged = []
    monkeypatch.setattr(
        "backup_tasks.log_to_task",
        lambda task_id, msg, status=None: logged.append((msg, status)),
    )

    result = run_backup_task.run(1)

    assert result["status"] == "CANCELLED"
    mock_transfer.assert_not_called(), "a stopped run must not start a transfer"
    # The node is free for whatever comes next, and the stop request is spent.
    deleted = {c.args[0] for c in fake_redis.delete.call_args_list}
    assert "backup_running:1" in deleted
    assert "backup_cancel:1" in deleted
    assert any("[STOPPED]" in msg for msg, _s in logged)


@patch("redis.Redis.from_url")
@patch("backup_tasks._transfer_and_record")
@patch("backup_tasks._plan_backup", side_effect=_fake_plan)
def test_a_run_nobody_stopped_proceeds_normally(
    mock_plan, mock_transfer, mock_redis, monkeypatch
):
    from backup_tasks import run_backup_task

    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    mock_redis.return_value = fake_redis
    mock_transfer.return_value = {"status": "SUCCESS"}

    _fake_session_scope.added = []
    monkeypatch.setattr("backup_tasks.session_scope", _fake_session_scope)
    mock_request = MagicMock()
    mock_request.id = "task-cancel-2"
    monkeypatch.setattr(Task, "request", mock_request)
    monkeypatch.setattr("backup_tasks.log_to_task", lambda *a, **k: None)

    assert run_backup_task.run(1)["status"] == "SUCCESS"
    mock_transfer.assert_called_once()
