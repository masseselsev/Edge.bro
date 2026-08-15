"""The thermal alert sweep must not scale its query count with fleet size.

The cohort detector compares each node against every peer sharing its CPU, so
it is fleet-wide by nature. The original implementation called
`thermal_verdict(node)` once per node, and each of those calls re-read every
fit in the fleet, ran the cohort assessment over all of them, then discarded
every verdict but one. Query count and rows materialised both grew with n²,
which at 2000 nodes meant tens of millions of rows per hourly sweep.

These tests pin the fix: a fixed number of queries regardless of fleet size,
and identical verdicts to the per-node path.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from core import monitoring_verdicts
from core.alert_sources import thermal as thermal_source
from database import Base
from core.clock import utcnow


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session._engine_for_test = engine
    yield session
    session.close()


def _seed(db, node_count, fits_per_node=8):
    """A fleet of identical hardware, so every node lands in one cohort."""
    now = utcnow()
    for i in range(node_count):
        node = models.Node(
            hostname=f"node-{i:04d}",
            ip_address=f"10.30.{i // 256}.{i % 256}",
            status="READY",
            cpu_info="11th Gen Intel Core i5-1135G7",
        )
        db.add(node)
        db.flush()
        for w in range(fits_per_node):
            db.add(models.ThermalFit(
                node_id=node.id,
                window_start=now - timedelta(days=w * 2),
                window_end=now - timedelta(days=w * 2) + timedelta(hours=4),
                rejection="OK",
                # One clear outlier so the sweep has something to report.
                theta_c_per_w=(3.0 if i == 0 else 1.0) + (w * 0.001),
                theta_normalised=(3.0 if i == 0 else 1.0) + (w * 0.001),
            ))
    db.commit()
    return now


class _QueryCounter:
    def __init__(self, session):
        self.engine = session._engine_for_test
        self.count = 0

    def __enter__(self):
        @event.listens_for(self.engine, "before_cursor_execute")
        def _count(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                self.count += 1

        self._handler = _count
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._handler)
        return False


@pytest.mark.parametrize("node_count", [5, 40])
def test_query_count_is_flat_in_fleet_size(db, node_count):
    """Whatever the fleet size, the sweep issues the same handful of queries."""
    _seed(db, node_count)
    with _QueryCounter(db) as counter:
        thermal_source.evaluate(db)

    # 3 fleet reads (cohort keys, recent fits, baseline fits) + the node list,
    # plus however many batches yield_per needs to stream the baseline.
    assert counter.count <= 8, (
        f"{counter.count} SELECTs for {node_count} nodes. This sweep must not "
        "issue per-node queries — see the module docstring."
    )


def test_query_count_does_not_grow_between_small_and_large_fleets(db):
    """The direct comparison: 10x the nodes must not mean ~10x the queries."""
    _seed(db, 10)
    with _QueryCounter(db) as small:
        thermal_source.evaluate(db)

    for row in db.query(models.ThermalFit).all():
        db.delete(row)
    for row in db.query(models.Node).all():
        db.delete(row)
    db.commit()

    _seed(db, 100)
    with _QueryCounter(db) as large:
        thermal_source.evaluate(db)

    assert large.count <= small.count + 2, (
        f"queries grew from {small.count} (10 nodes) to {large.count} "
        f"(100 nodes); the sweep is still scaling with fleet size"
    )


def test_batched_verdicts_match_the_single_node_path(db):
    """The context path and thermal_verdict() must agree, field for field."""
    now = _seed(db, 12)
    context = monitoring_verdicts.build_thermal_context(db, now)

    for node in db.query(models.Node).all():
        batched = monitoring_verdicts.verdict_from_context(context, node.id)
        single = monitoring_verdicts.thermal_verdict(db, node, now)
        assert batched == single, f"verdicts diverge for {node.hostname}"


def test_the_outlier_is_still_detected(db):
    """Batching must not cost us the actual detection."""
    _seed(db, 12)
    candidates = thermal_source.evaluate(db)

    assert candidates, "expected the seeded outlier to raise a candidate"
    flagged = {c.node_id for c in candidates}
    outlier = db.query(models.Node).filter_by(hostname="node-0000").one()
    assert outlier.id in flagged, "the 3x-theta node was not flagged"
    for c in candidates:
        assert c.module == "thermal"
        assert c.dedup_key == f"thermal:{c.node_id}"


def test_empty_fleet_is_handled(db):
    assert thermal_source.evaluate(db) == []


def test_node_with_no_fits_produces_no_candidate(db):
    db.add(models.Node(hostname="quiet", ip_address="10.40.0.1",
                       status="READY", cpu_info="Some CPU"))
    db.commit()
    assert thermal_source.evaluate(db) == []
