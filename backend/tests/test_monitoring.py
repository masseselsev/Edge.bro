import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from core import harvest as harvest_io
from core import thermal
from database import Base
from tasks import monitoring

TEST_DATABASE_URL = "sqlite:///./test_monitoring_db.db"
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# A realistic epoch. The parser rejects timestamps outside 2020-2100 as a
# broken node clock, so a 1970-era fixture value would be dropped.
BASE_TS = 1_786_000_000


@pytest.fixture
def session_factory():
    if os.path.exists("./test_monitoring_db.db"):
        os.remove("./test_monitoring_db.db")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_monitoring_db.db"):
            os.remove("./test_monitoring_db.db")


@pytest.fixture
def db(session_factory, monkeypatch):
    monkeypatch.setattr(monitoring, "SessionLocal", session_factory)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def make_node(db, hostname="node-1", ip="192.168.1.50", **kwargs):
    node = models.Node(hostname=hostname, ip_address=ip, ssh_port=2222, **kwargs)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def real_smart_report():
    with open(os.path.join(FIXTURES_DIR, "samsung_870_evo_smartctl.json")) as f:
        return json.load(f)


def telemetry_buffer(count=400, dt=60.0, theta=1.5, tau=2100.0, ambient=25.0, start=BASE_TS):
    """Serialise a known thermal system the way the node's collector would."""
    powers = [4.0 + 8.0 * (i % 17) / 16.0 for i in range(count)]
    truth = thermal.simulate(theta, tau, ambient, powers, dt)

    energy_uj = 1_000_000_000
    lines = []
    for i, sample in enumerate(truth):
        lines.append(json.dumps({
            "v": 1,
            "ts": start + int(i * dt),
            "up": 10_000.0 + i * dt,
            "rapl_uj": energy_uj,
            "rapl_max": 262143328850,
            "t_pkg": round(sample.temp_c, 1),
            "t_board": 24.0,
            "thr_pkg": 0,
            "cpu_busy": 1000 + i * 10,
            "cpu_total": 10_000 + i * 100,
        }))
        energy_uj += int(sample.power_w * dt * 1_000_000)
    return "\n".join(lines) + "\n"


def stub_harvest(monkeypatch, **kwargs):
    result = harvest_io.HarvestResult(
        reachable=kwargs.pop("reachable", True),
        buffer_text=kwargs.pop("buffer_text", ""),
        smart_reports=kwargs.pop("smart_reports", {}),
        capabilities=kwargs.pop("capabilities", {"rapl": True, "smartctl": True}),
        errors=kwargs.pop("errors", []),
    )
    monkeypatch.setattr(harvest_io, "harvest", lambda *a, **k: result)
    return result


# --- setting resolution ------------------------------------------------------

def test_a_node_override_beats_the_global_default(session_factory):
    db = session_factory()
    settings = models.Settings(smart_temp_warn_c=60)
    node = models.Node(hostname="hot", ip_address="10.0.0.1", smart_temp_warn_c=75)

    assert monitoring.resolve_setting(node, settings, "smart_temp_warn_c", 0) == 75
    db.close()


def test_a_null_override_inherits_the_global(session_factory):
    """NULL means inherit, deliberately distinct from an explicit value that
    happens to equal the current global."""
    settings = models.Settings(smart_temp_warn_c=60)
    node = models.Node(hostname="n", ip_address="10.0.0.1", smart_temp_warn_c=None)

    assert monitoring.resolve_setting(node, settings, "smart_temp_warn_c", 0) == 60


def test_the_fallback_applies_when_there_are_no_settings_at_all():
    node = models.Node(hostname="n", ip_address="10.0.0.1")
    assert monitoring.resolve_setting(node, None, "smart_temp_warn_c", 60) == 60


# --- scheduling --------------------------------------------------------------

def test_a_never_harvested_node_is_due():
    node = models.Node(hostname="n", ip_address="10.0.0.1", last_harvest_at=None)
    assert monitoring.monitoring_due(node, None, datetime.utcnow()) is True


def test_a_node_harvested_within_its_interval_is_not_due():
    now = datetime.utcnow()
    settings = models.Settings(monitoring_interval_days=30)
    node = models.Node(hostname="n", ip_address="10.0.0.1",
                       last_harvest_at=now - timedelta(days=5))

    assert monitoring.monitoring_due(node, settings, now) is False


def test_a_node_past_its_interval_is_due():
    now = datetime.utcnow()
    settings = models.Settings(monitoring_interval_days=30)
    node = models.Node(hostname="n", ip_address="10.0.0.1",
                       last_harvest_at=now - timedelta(days=31))

    assert monitoring.monitoring_due(node, settings, now) is True


def test_a_per_node_interval_overrides_the_global_one():
    now = datetime.utcnow()
    settings = models.Settings(monitoring_interval_days=30)
    node = models.Node(hostname="n", ip_address="10.0.0.1",
                       monitoring_interval_days=7,
                       last_harvest_at=now - timedelta(days=10))

    assert monitoring.monitoring_due(node, settings, now) is True


def test_monitoring_can_be_switched_off_for_one_node():
    now = datetime.utcnow()
    settings = models.Settings(monitoring_enabled=True, monitoring_interval_days=1)
    node = models.Node(hostname="n", ip_address="10.0.0.1",
                       monitoring_enabled=False, last_harvest_at=None)

    assert monitoring.monitoring_due(node, settings, now) is False


def test_monitoring_can_be_switched_off_fleet_wide():
    settings = models.Settings(monitoring_enabled=False)
    node = models.Node(hostname="n", ip_address="10.0.0.1", last_harvest_at=None)

    assert monitoring.monitoring_due(node, settings, datetime.utcnow()) is False


# --- harvesting end to end ---------------------------------------------------

def test_a_harvest_stores_rollups_fits_and_smart(db, monkeypatch):
    node = make_node(db)
    stub_harvest(
        monkeypatch,
        buffer_text=telemetry_buffer(),
        smart_reports={"/dev/sda": real_smart_report()},
    )

    summary = monitoring.harvest_node(node.id)

    assert summary["status"] == "SUCCESS"
    assert summary["samples"] == 400
    assert db.query(models.TelemetryRollup).filter_by(node_id=node.id).count() > 0
    assert db.query(models.SmartSnapshot).filter_by(node_id=node.id).count() == 1
    assert db.query(models.ThermalFit).filter_by(node_id=node.id).count() > 0


def test_the_stored_theta_matches_the_system_that_produced_the_data(db, monkeypatch):
    """The whole pipeline, end to end: a known thermal system serialised as the
    collector would, harvested, and the theta that lands in the database."""
    node = make_node(db)
    stub_harvest(monkeypatch, buffer_text=telemetry_buffer(count=600, theta=1.5))

    monitoring.harvest_node(node.id)

    fits = db.query(models.ThermalFit).filter_by(
        node_id=node.id, rejection="OK"
    ).all()
    assert fits, "expected at least one successful fit"
    assert fits[0].theta_c_per_w == pytest.approx(1.5, rel=0.15)
    assert fits[0].theta_normalised is not None


def test_a_degraded_interface_lands_as_a_higher_stored_theta(db, monkeypatch):
    healthy_node = make_node(db, "healthy", "10.0.0.1")
    degraded_node = make_node(db, "degraded", "10.0.0.2")

    stub_harvest(monkeypatch, buffer_text=telemetry_buffer(count=600, theta=1.5))
    monitoring.harvest_node(healthy_node.id)

    stub_harvest(monkeypatch, buffer_text=telemetry_buffer(count=600, theta=2.2))
    monitoring.harvest_node(degraded_node.id)

    healthy = db.query(models.ThermalFit).filter_by(node_id=healthy_node.id, rejection="OK").first()
    degraded = db.query(models.ThermalFit).filter_by(node_id=degraded_node.id, rejection="OK").first()

    assert healthy and degraded
    assert degraded.theta_c_per_w > healthy.theta_c_per_w * 1.2


def test_a_flat_load_is_recorded_as_a_rejection_not_silently_dropped(db, monkeypatch):
    """A node with no theta must be distinguishable from one nobody looked at."""
    node = make_node(db)
    lines = []
    energy = 1_000_000_000
    for i in range(400):
        lines.append(json.dumps({
            "v": 1, "ts": BASE_TS + i * 60, "up": 10_000.0 + i * 60,
            "rapl_uj": energy, "rapl_max": 262143328850, "t_pkg": 45.0, "thr_pkg": 0,
        }))
        energy += 8 * 60 * 1_000_000  # perfectly constant 8 W
    stub_harvest(monkeypatch, buffer_text="\n".join(lines))

    monitoring.harvest_node(node.id)

    fits = db.query(models.ThermalFit).filter_by(node_id=node.id).all()
    assert fits
    assert all(f.rejection == "NO_EXCITATION" for f in fits)
    assert all(f.theta_c_per_w is None for f in fits)
    assert fits[0].excitation is not None


def test_smart_is_scored_with_the_nodes_own_temperature_thresholds(db, monkeypatch):
    """A unit in full sun legitimately needs a looser ceiling."""
    node = make_node(db, smart_temp_warn_c=20, smart_temp_crit_c=30)
    stub_harvest(monkeypatch, smart_reports={"/dev/sda": real_smart_report()})

    monitoring.harvest_node(node.id)

    snapshot = db.query(models.SmartSnapshot).filter_by(node_id=node.id).first()
    thermal_sub = next(s for s in snapshot.subscores if s["name"] == "thermal")
    # The drive reads 37 C, which is above a 30 C critical threshold.
    assert thermal_sub["score"] == 0.0


def test_the_full_smart_report_is_kept_for_the_latest_query(db, monkeypatch):
    node = make_node(db)
    stub_harvest(monkeypatch, smart_reports={"/dev/sda": real_smart_report()})

    monitoring.harvest_node(node.id)

    snapshot = db.query(models.SmartSnapshot).filter_by(node_id=node.id).first()
    assert snapshot.raw is not None
    assert snapshot.raw["model_name"] == "Samsung SSD 870 EVO 500GB"
    assert snapshot.score >= 95


def test_an_unreachable_node_fails_without_touching_the_harvest_timestamp(db, monkeypatch):
    node = make_node(db)
    stub_harvest(monkeypatch, reachable=False, errors=["unreachable: timed out"])

    summary = monitoring.harvest_node(node.id)

    db.refresh(node)
    assert summary["status"] == "FAILED"
    assert node.last_harvest_at is None


def test_a_drive_that_stopped_answering_does_not_lose_the_telemetry(db, monkeypatch):
    """Partial success is the normal outcome and must be reported as such."""
    node = make_node(db)
    stub_harvest(
        monkeypatch,
        buffer_text=telemetry_buffer(),
        smart_reports={},
        errors=["/dev/sda: no parseable smartctl output"],
    )

    summary = monitoring.harvest_node(node.id)

    assert summary["status"] == "PARTIAL"
    assert summary["errors"]
    assert db.query(models.TelemetryRollup).filter_by(node_id=node.id).count() > 0


def test_a_node_with_no_collector_yet_is_not_an_error(db, monkeypatch):
    """The normal state of a node provisioned minutes ago."""
    node = make_node(db)
    stub_harvest(monkeypatch, buffer_text="", smart_reports={"/dev/sda": real_smart_report()})

    summary = monitoring.harvest_node(node.id)

    assert summary["status"] == "SUCCESS"
    assert summary["samples"] == 0
    assert db.query(models.SmartSnapshot).filter_by(node_id=node.id).count() == 1


def test_capabilities_are_recorded_so_the_ui_can_explain_an_empty_panel(db, monkeypatch):
    node = make_node(db)
    stub_harvest(monkeypatch, capabilities={"rapl": False, "smartctl": True, "buffered": 0})

    monitoring.harvest_node(node.id)

    db.refresh(node)
    assert node.monitoring_capabilities["rapl"] is False
    assert node.last_harvest_at is not None


def test_harvesting_a_node_that_does_not_exist_is_reported_not_raised(db):
    assert monitoring.harvest_node(999999)["status"] == "FAILED"


# --- idempotency -------------------------------------------------------------

def test_re_harvesting_overlapping_data_updates_rather_than_duplicates(db, monkeypatch):
    """A harvest that drained but failed before committing leaves the next
    buffer covering ground already seen."""
    node = make_node(db)
    buffer_text = telemetry_buffer(count=400)

    stub_harvest(monkeypatch, buffer_text=buffer_text)
    monitoring.harvest_node(node.id)
    first_rollups = db.query(models.TelemetryRollup).filter_by(node_id=node.id).count()
    first_fits = db.query(models.ThermalFit).filter_by(node_id=node.id).count()

    stub_harvest(monkeypatch, buffer_text=buffer_text)
    monitoring.harvest_node(node.id)

    assert db.query(models.TelemetryRollup).filter_by(node_id=node.id).count() == first_rollups
    assert db.query(models.ThermalFit).filter_by(node_id=node.id).count() == first_fits


# --- retention ---------------------------------------------------------------

def test_retention_drops_old_rollups_but_keeps_smart_scalars(db, monkeypatch):
    node = make_node(db)
    old = datetime.utcnow() - timedelta(days=200)

    db.add(models.TelemetryRollup(node_id=node.id, bucket_start=old, sample_count=15))
    db.add(models.SmartSnapshot(
        node_id=node.id, captured_at=old, device="/dev/sda",
        score=97, grade="OK", raw={"big": "report"},
    ))
    db.add(models.Settings(telemetry_retention_days=90))
    db.commit()

    result = monitoring.monitoring_retention_task()

    assert result["rollups_removed"] == 1
    assert result["raw_reports_cleared"] == 1

    snapshot = db.query(models.SmartSnapshot).filter_by(node_id=node.id).first()
    assert snapshot.raw is None
    assert snapshot.score == 97, "the parsed scalars are what the history graph plots"


def test_retention_prunes_rejected_fits_but_keeps_the_trend(db, monkeypatch):
    """A month of buffer yields ~180 windows per harvest, and on a flat-load
    fleet nearly all are rejections — 2.16M rows a year across a thousand
    nodes saying only "the load never varied enough". The successful fits are
    the degradation trend and must survive."""
    node = make_node(db)
    old = datetime.utcnow() - timedelta(days=200)

    db.add(models.ThermalFit(
        node_id=node.id, window_start=old, window_end=old + timedelta(hours=4),
        rejection="NO_EXCITATION", n_samples=240, excitation=0.02,
    ))
    db.add(models.ThermalFit(
        node_id=node.id, window_start=old + timedelta(hours=4),
        window_end=old + timedelta(hours=8),
        rejection="OK", n_samples=240, excitation=0.31, theta_c_per_w=1.52,
    ))
    db.add(models.Settings(telemetry_retention_days=90))
    db.commit()

    result = monitoring.monitoring_retention_task()

    assert result["rejected_fits_removed"] == 1
    survivors = db.query(models.ThermalFit).all()
    assert len(survivors) == 1
    assert survivors[0].rejection == "OK"
    assert survivors[0].theta_c_per_w == 1.52


def test_retention_leaves_recent_rejections_alone(db, monkeypatch):
    """Recent rejections are how an operator diagnoses why a node has no theta."""
    node = make_node(db)
    recent = datetime.utcnow() - timedelta(days=3)

    db.add(models.ThermalFit(
        node_id=node.id, window_start=recent, window_end=recent + timedelta(hours=4),
        rejection="NO_EXCITATION", n_samples=240, excitation=0.02,
    ))
    db.add(models.Settings(telemetry_retention_days=90))
    db.commit()

    assert monitoring.monitoring_retention_task()["rejected_fits_removed"] == 0
    assert db.query(models.ThermalFit).count() == 1


def test_clearing_a_raw_report_stores_sql_null_not_json_null(db, monkeypatch):
    """SQLAlchemy's JSON type stores Python None as the JSON value `null` by
    default, which still satisfies `IS NOT NULL`. Without none_as_null the
    retention sweep would re-clear the same rows every run and report a false
    count, and the full-statistics endpoint would hand back a null report
    instead of saying none is stored."""
    node = make_node(db)
    old = datetime.utcnow() - timedelta(days=200)
    db.add(models.SmartSnapshot(node_id=node.id, captured_at=old, device="/dev/sda",
                                score=97, raw={"big": "report"}))
    db.add(models.Settings(telemetry_retention_days=90))
    db.commit()

    assert monitoring.monitoring_retention_task()["raw_reports_cleared"] == 1
    # The second sweep must find nothing left to clear.
    assert monitoring.monitoring_retention_task()["raw_reports_cleared"] == 0

    remaining = db.query(models.SmartSnapshot).filter(
        models.SmartSnapshot.raw.isnot(None)
    ).count()
    assert remaining == 0


def test_retention_leaves_recent_data_alone(db, monkeypatch):
    node = make_node(db)
    recent = datetime.utcnow() - timedelta(days=3)

    db.add(models.TelemetryRollup(node_id=node.id, bucket_start=recent, sample_count=15))
    db.add(models.SmartSnapshot(
        node_id=node.id, captured_at=recent, device="/dev/sda", raw={"kept": True},
    ))
    db.add(models.Settings(telemetry_retention_days=90))
    db.commit()

    result = monitoring.monitoring_retention_task()

    assert result["rollups_removed"] == 0
    assert db.query(models.SmartSnapshot).first().raw is not None


# --- the sweep ---------------------------------------------------------------

def test_the_sweep_dispatches_only_overdue_nodes(db, monkeypatch):
    now = datetime.utcnow()
    db.add(models.Settings(monitoring_interval_days=30, monitoring_enabled=True))
    make_node(db, "fresh", "10.0.0.1", last_harvest_at=now - timedelta(days=2))
    make_node(db, "overdue", "10.0.0.2", last_harvest_at=now - timedelta(days=40))
    make_node(db, "never", "10.0.0.3", last_harvest_at=None)
    db.commit()

    dispatched = []
    monkeypatch.setattr(
        monitoring.harvest_node_task, "apply_async",
        lambda args=None, **kwargs: dispatched.append(args[0]),
    )

    result = monitoring.monitoring_sweep_task()

    assert result["dispatched"] == 2
    assert len(dispatched) == 2


def test_one_unreachable_node_does_not_stop_the_sweep(db, monkeypatch):
    """Across a thousand roadside units some are always unreachable."""
    db.add(models.Settings(monitoring_interval_days=30))
    for i in range(5):
        make_node(db, f"node-{i}", f"10.0.0.{i + 1}", last_harvest_at=None)
    db.commit()

    dispatched = []
    monkeypatch.setattr(
        monitoring.harvest_node_task, "apply_async",
        lambda args=None, **kwargs: dispatched.append(args[0]),
    )

    assert monitoring.monitoring_sweep_task()["dispatched"] == 5
