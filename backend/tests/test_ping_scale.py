"""The ping sweep at fleet scale.

This task runs every 30 seconds against every node. The original version did
`asyncio.gather` over an unbounded list of `create_subprocess_exec` calls, so a
2000-node fleet forked 2000 `ping` processes at once, every half minute. These
tests pin the two properties that fixes: a hard ceiling on concurrency, and a
write-back that touches only rows whose state actually changed.
"""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import tasks
from database import Base
from tasks import ping_all_nodes_task
from tasks.ping import PING_CONCURRENCY, ping_all_async


@pytest.fixture(scope="function")
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    factory.kw["bind"] = engine  # keep a handle for the statement counter
    yield factory
    Base.metadata.drop_all(bind=engine)


def test_ping_concurrency_is_capped():
    """Never more than PING_CONCURRENCY probes in flight at once."""
    in_flight = 0
    peak = 0

    async def fake_ping(ip):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)  # yield, so the scheduler can pile others on
        in_flight -= 1
        return True

    with patch.object(tasks, "async_ping_ip", side_effect=fake_ping):
        results = asyncio.run(ping_all_async([f"10.0.0.{i}" for i in range(2000)]))

    assert len(results) == 2000
    assert all(results)
    assert peak <= PING_CONCURRENCY, (
        f"{peak} concurrent pings for a 2000-node fleet; the cap is "
        f"{PING_CONCURRENCY}. An unbounded gather here forks one process per "
        f"node and exhausts PIDs."
    )


def test_ping_results_map_to_the_right_nodes_under_concurrency():
    """Bounded execution must not reorder results relative to their inputs."""
    async def fake_ping(ip):
        # Make later addresses finish sooner, so any ordering bug shows up.
        await asyncio.sleep((255 - int(ip.split(".")[-1])) / 10000)
        return int(ip.split(".")[-1]) % 2 == 0

    ips = [f"10.0.0.{i}" for i in range(200)]
    with patch.object(tasks, "async_ping_ip", side_effect=fake_ping):
        results = asyncio.run(ping_all_async(ips))

    assert results == [i % 2 == 0 for i in range(200)]


@patch("tasks.SessionLocal")
@patch("tasks.async_ping_ip", new_callable=AsyncMock)
def test_unchanged_nodes_are_not_rewritten(mock_ping, mock_session, session_factory):
    """A steady fleet should not generate an UPDATE per node per sweep."""
    mock_session.side_effect = session_factory
    db = session_factory()
    for i in range(50):
        db.add(models.Node(
            hostname=f"n{i}", ip_address=f"10.1.0.{i}",
            status="READY", last_ping_status=False,
        ))
    db.commit()
    db.close()

    mock_ping.side_effect = lambda ip: False  # every node stays down

    statements = []
    engine = session_factory.kw["bind"]

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, params, context, executemany):
        if statement.strip().upper().startswith("UPDATE"):
            statements.append(statement)

    res = ping_all_nodes_task()
    event.remove(engine, "before_cursor_execute", record)

    assert res["status"] == "SUCCESS"
    assert res["checked"] == 50
    assert res["changed"] == 0
    assert statements == [], (
        f"{len(statements)} UPDATE(s) issued for a fleet where nothing "
        "changed; the sweep should write only transitions"
    )


@patch("tasks.SessionLocal")
@patch("tasks.async_ping_ip", new_callable=AsyncMock)
def test_never_probed_node_gets_a_definite_status(mock_ping, mock_session, session_factory):
    """NULL is not False.

    A node whose first-ever probe fails must be written as offline rather than
    left NULL, or the UI cannot distinguish "down" from "never checked".
    """
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Node(hostname="fresh", ip_address="10.2.0.1", status="READY"))
    db.commit()
    assert db.query(models.Node).first().last_ping_status is None
    db.close()

    mock_ping.side_effect = lambda ip: False
    res = ping_all_nodes_task()

    assert res["status"] == "SUCCESS"
    assert res["changed"] == 1
    node = session_factory().query(models.Node).filter_by(hostname="fresh").first()
    assert node.last_ping_status is False


@patch("tasks.SessionLocal")
@patch("tasks.async_ping_ip", new_callable=AsyncMock)
def test_recovery_stamps_availability(mock_ping, mock_session, session_factory):
    mock_session.side_effect = session_factory
    db = session_factory()
    db.add(models.Node(
        hostname="flap", ip_address="10.3.0.1",
        status="READY", last_ping_status=False,
    ))
    db.commit()
    db.close()

    mock_ping.side_effect = lambda ip: True
    res = ping_all_nodes_task()

    assert res["changed"] == 1
    node = session_factory().query(models.Node).filter_by(hostname="flap").first()
    assert node.last_ping_status is True
    assert node.last_available_at is not None
