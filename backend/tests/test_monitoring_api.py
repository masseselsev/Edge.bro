import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base, get_db
from main import app
from core.clock import utcnow

TEST_DATABASE_URL = "sqlite:///./test_monitoring_api_db.db"
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def db_session():
    if os.path.exists("./test_monitoring_api_db.db"):
        os.remove("./test_monitoring_api_db.db")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_monitoring_api_db.db"):
            os.remove("./test_monitoring_api_db.db")


@pytest.fixture
def admin(db_session):
    user = models.User(username="tester", hashed_password="x", name="Tester",
                       is_superadmin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session, admin):
    def override_get_db():
        yield db_session

    from routers.users import get_current_auth, require_admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[get_current_auth] = lambda: admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def make_node(db, hostname="node-1", ip="10.0.0.1", cpu="11th Gen Intel(R) Core(TM) i5-1145G7E @ 2.60GHz"):
    node = models.Node(hostname=hostname, ip_address=ip, ssh_port=2222, cpu_info=cpu)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def add_smart(db, node, days_ago=0, percent_used=1.0, score=99, device="/dev/sda", raw=None):
    snapshot = models.SmartSnapshot(
        node_id=node.id,
        captured_at=utcnow() - timedelta(days=days_ago),
        device=device, protocol="SATA", model="Samsung SSD 870 EVO 500GB",
        health_passed=True, temperature_c=37, power_on_hours=1810,
        written_bytes=3_900_000_000_000, percent_used=percent_used,
        score=score, grade="OK",
        subscores=[{"name": "wear", "score": 99.0, "evidence": {"percent_used": percent_used}}],
        overrides=[], advisories=[], raw=raw,
    )
    db.add(snapshot)
    db.commit()
    return snapshot


def add_fit(db, node, days_ago, theta=1.5, rejection="OK", normalised=None):
    start = utcnow() - timedelta(days=days_ago)
    fit = models.ThermalFit(
        node_id=node.id, window_start=start, window_end=start + timedelta(hours=4),
        rejection=rejection, n_samples=240, excitation=0.31,
        theta_c_per_w=theta if rejection == "OK" else None,
        theta_normalised=(normalised if normalised is not None else theta) if rejection == "OK" else None,
        tau_seconds=2100.0, t_ambient_c=25.0, mean_temp_c=45.0,
    )
    db.add(fit)
    db.commit()
    return fit


# --- node health --------------------------------------------------------------

def test_a_node_with_no_monitoring_data_yet_still_answers(client, db_session):
    """A freshly provisioned node must render, not 500."""
    node = make_node(db_session)

    data = client.get(f"/api/monitoring/nodes/{node.id}").json()

    assert data["node_id"] == node.id
    assert data["smart"] == []
    assert data["thermal"]["status"] == "INSUFFICIENT_DATA"
    assert data["last_harvest_at"] is None


def test_an_unknown_node_is_a_404(client, db_session):
    assert client.get("/api/monitoring/nodes/9999").status_code == 404


def test_the_latest_smart_reading_drives_the_badge(client, db_session):
    node = make_node(db_session)
    add_smart(db_session, node, days_ago=30, score=99)
    add_smart(db_session, node, days_ago=0, score=62)

    data = client.get(f"/api/monitoring/nodes/{node.id}").json()

    assert len(data["smart"]) == 1, "one entry per device, the most recent"
    assert data["smart"][0]["score"] == 62


def test_each_device_gets_its_own_entry(client, db_session):
    node = make_node(db_session)
    add_smart(db_session, node, device="/dev/sda", score=99)
    add_smart(db_session, node, device="/dev/nvme0n1", score=80)

    data = client.get(f"/api/monitoring/nodes/{node.id}").json()

    assert {s["device"] for s in data["smart"]} == {"/dev/sda", "/dev/nvme0n1"}


def test_the_wear_projection_carries_its_derivation(client, db_session):
    """A date with no arithmetic behind it is as opaque as a percentage."""
    node = make_node(db_session)
    add_smart(db_session, node, days_ago=200, percent_used=2.0)
    add_smart(db_session, node, days_ago=100, percent_used=6.0)
    add_smart(db_session, node, days_ago=0, percent_used=10.0)

    smart_data = client.get(f"/api/monitoring/nodes/{node.id}").json()["smart"][0]

    assert smart_data["percent_used_per_day"] == pytest.approx(0.04, rel=0.1)
    assert smart_data["observation_points"] == 3
    assert smart_data["projected_date"] is not None


def test_a_projection_that_cannot_be_made_explains_itself(client, db_session):
    node = make_node(db_session)
    add_smart(db_session, node, days_ago=0, percent_used=1.0)

    smart_data = client.get(f"/api/monitoring/nodes/{node.id}").json()["smart"][0]

    assert smart_data["projected_date"] is None
    assert "two readings" in smart_data["projection_unavailable_reason"]


def test_capabilities_explain_why_a_thermal_panel_is_empty(client, db_session):
    node = make_node(db_session)
    node.monitoring_capabilities = {"rapl": False, "smartctl": True}
    db_session.commit()

    data = client.get(f"/api/monitoring/nodes/{node.id}").json()

    assert data["capabilities"]["rapl"] is False


# --- thermal verdict ----------------------------------------------------------

def test_a_lone_node_cannot_be_cohort_judged_and_says_so(client, db_session):
    node = make_node(db_session)
    for i in range(5):
        add_fit(db_session, node, days_ago=i + 1)

    thermal = client.get(f"/api/monitoring/nodes/{node.id}").json()["thermal"]

    assert thermal["cohort_status"] == "INSUFFICIENT_DATA"
    assert thermal["windows_fitted"] == 5


def test_a_node_unlike_its_cohort_is_alerted(client, db_session):
    peers = [make_node(db_session, f"peer-{i}", f"10.0.1.{i}") for i in range(8)]
    for peer in peers:
        for d in range(4):
            add_fit(db_session, peer, days_ago=d + 1, theta=1.5)

    bad = make_node(db_session, "degraded", "10.0.2.1")
    for d in range(4):
        add_fit(db_session, bad, days_ago=d + 1, theta=2.6)

    thermal = client.get(f"/api/monitoring/nodes/{bad.id}").json()["thermal"]

    assert thermal["cohort_status"] == "ALERT"
    assert thermal["status"] == "ALERT"
    assert thermal["cohort_size"] == 9
    assert thermal["excess_ratio"] > 0.15
    assert any("above the cohort median" in r for r in thermal["reasons"])


def test_different_cpus_are_not_compared_with_each_other(client, db_session):
    """An i7 dissipating more heat is not evidence against an i5."""
    for i in range(8):
        peer = make_node(db_session, f"i7-{i}", f"10.0.3.{i}",
                         cpu="11th Gen Intel(R) Core(TM) i7-1185G7E @ 2.80GHz")
        for d in range(4):
            add_fit(db_session, peer, days_ago=d + 1, theta=2.6)

    lone_i5 = make_node(db_session, "i5-node", "10.0.4.1")
    for d in range(4):
        add_fit(db_session, lone_i5, days_ago=d + 1, theta=1.5)

    thermal = client.get(f"/api/monitoring/nodes/{lone_i5.id}").json()["thermal"]

    assert thermal["cohort_key"] == "i5-1145g7e"
    assert thermal["cohort_size"] == 1


def test_rejected_windows_are_surfaced_so_an_empty_verdict_is_explainable(client, db_session):
    node = make_node(db_session)
    for d in range(6):
        add_fit(db_session, node, days_ago=d + 1, rejection="NO_EXCITATION")

    thermal = client.get(f"/api/monitoring/nodes/{node.id}").json()["thermal"]

    assert thermal["windows_fitted"] == 0
    assert thermal["windows_rejected"] == 6
    assert thermal["last_rejection"] == "NO_EXCITATION"


# --- history endpoints ---------------------------------------------------------

def test_smart_history_returns_the_series_in_time_order(client, db_session):
    node = make_node(db_session)
    for d in (30, 10, 20):
        add_smart(db_session, node, days_ago=d, percent_used=float(d))

    points = client.get(f"/api/monitoring/nodes/{node.id}/smart-history?days=90").json()

    assert len(points) == 3
    assert [p["percent_used"] for p in points] == [30.0, 20.0, 10.0]


def test_history_depth_is_selectable(client, db_session):
    node = make_node(db_session)
    add_smart(db_session, node, days_ago=200)
    add_smart(db_session, node, days_ago=5)

    assert len(client.get(f"/api/monitoring/nodes/{node.id}/smart-history?days=30").json()) == 1
    assert len(client.get(f"/api/monitoring/nodes/{node.id}/smart-history?days=365").json()) == 2


def test_thermal_history_hides_rejections_unless_asked(client, db_session):
    node = make_node(db_session)
    add_fit(db_session, node, days_ago=1, theta=1.5)
    add_fit(db_session, node, days_ago=2, rejection="NO_EXCITATION")

    default = client.get(f"/api/monitoring/nodes/{node.id}/thermal-history").json()
    with_rejects = client.get(
        f"/api/monitoring/nodes/{node.id}/thermal-history?include_rejected=true"
    ).json()

    assert len(default) == 1
    assert len(with_rejects) == 2


def test_the_full_smart_report_is_available_for_the_latest_reading(client, db_session):
    node = make_node(db_session)
    with open(os.path.join(FIXTURES_DIR, "samsung_870_evo_smartctl.json")) as f:
        report = json.load(f)
    add_smart(db_session, node, raw=report)

    data = client.get(f"/api/monitoring/nodes/{node.id}/smart-latest").json()

    assert data["report"]["model_name"] == "Samsung SSD 870 EVO 500GB"
    assert len(data["report"]["ata_smart_attributes"]["table"]) == 15


def test_a_node_whose_raw_reports_have_aged_out_says_why(client, db_session):
    node = make_node(db_session)
    add_smart(db_session, node, raw=None)

    response = client.get(f"/api/monitoring/nodes/{node.id}/smart-latest")

    assert response.status_code == 404
    assert "retention window" in response.json()["detail"]


def test_telemetry_rollups_come_back_for_the_graph(client, db_session):
    node = make_node(db_session)
    for i in range(4):
        db_session.add(models.TelemetryRollup(
            node_id=node.id,
            bucket_start=utcnow() - timedelta(hours=i),
            sample_count=15, cpu_temp_c_mean=45.0 + i, power_w_mean=7.0,
        ))
    db_session.commit()

    points = client.get(f"/api/monitoring/nodes/{node.id}/telemetry?days=7").json()

    assert len(points) == 4
    assert points[0]["bucket_start"] < points[-1]["bucket_start"]


# --- thresholds ------------------------------------------------------------------

def test_thresholds_report_the_effective_value_and_what_is_inherited(client, db_session):
    db_session.add(models.Settings(smart_temp_warn_c=60, smart_temp_crit_c=70,
                                   monitoring_interval_days=30, monitoring_enabled=True))
    node = make_node(db_session)
    db_session.commit()

    data = client.get(f"/api/monitoring/nodes/{node.id}/thresholds").json()

    assert data["smart_temp_warn_c"] == 60
    assert data["overridden"] == []


def test_setting_an_override_takes_effect_and_is_reported_as_overridden(client, db_session):
    db_session.add(models.Settings(smart_temp_warn_c=60, smart_temp_crit_c=70))
    node = make_node(db_session)
    db_session.commit()

    data = client.post(f"/api/monitoring/nodes/{node.id}/thresholds",
                       json={"smart_temp_warn_c": 75, "smart_temp_crit_c": 85}).json()

    assert data["smart_temp_warn_c"] == 75
    assert set(data["overridden"]) == {"smart_temp_warn_c", "smart_temp_crit_c"}


def test_clearing_an_override_returns_the_node_to_inherited(client, db_session):
    """Null is how the UI clears one, which is distinct from not sending it."""
    db_session.add(models.Settings(smart_temp_warn_c=60, smart_temp_crit_c=70))
    node = make_node(db_session)
    db_session.commit()

    client.post(f"/api/monitoring/nodes/{node.id}/thresholds", json={"smart_temp_warn_c": 75})
    data = client.post(f"/api/monitoring/nodes/{node.id}/thresholds",
                       json={"smart_temp_warn_c": None}).json()

    assert data["smart_temp_warn_c"] == 60
    assert "smart_temp_warn_c" not in data["overridden"]


def test_a_warning_threshold_at_or_above_critical_is_refused(client, db_session):
    node = make_node(db_session)

    response = client.post(f"/api/monitoring/nodes/{node.id}/thresholds",
                           json={"smart_temp_warn_c": 80, "smart_temp_crit_c": 70})

    assert response.status_code == 400


def test_an_out_of_range_threshold_is_refused(client, db_session):
    node = make_node(db_session)
    assert client.post(f"/api/monitoring/nodes/{node.id}/thresholds",
                       json={"smart_temp_warn_c": 500}).status_code == 422


# --- per-user preferences ---------------------------------------------------------

def test_a_user_who_has_never_chosen_gets_the_defaults(client):
    data = client.get("/api/monitoring/preferences").json()

    assert data["preferences"]["graph_days"] == 90
    assert "score" in data["preferences"]["smart_graph_series"]


def test_preferences_persist_for_the_user(client, db_session, admin):
    client.post("/api/monitoring/preferences",
                json={"preferences": {"graph_days": 30}})

    db_session.refresh(admin)
    assert admin.ui_preferences["graph_days"] == 30
    assert client.get("/api/monitoring/preferences").json()["preferences"]["graph_days"] == 30


def test_default_preferences_include_fleet_column_widths(client):
    resp = client.get("/api/monitoring/preferences")
    assert resp.status_code == 200
    widths = resp.json()["preferences"]["fleet_column_widths"]
    for key in ["hostname", "ip_address", "os_version", "disk_type", "status", "last_backup", "actions"]:
        assert key in widths
        assert isinstance(widths[key], int)


def test_saving_one_graphs_choice_does_not_wipe_another(client):
    """A client that knows about one graph must not clobber settings for a
    graph it has never heard of."""
    client.post("/api/monitoring/preferences",
                json={"preferences": {"smart_graph_series": ["score"]}})
    client.post("/api/monitoring/preferences",
                json={"preferences": {"graph_days": 7}})

    prefs = client.get("/api/monitoring/preferences").json()["preferences"]

    assert prefs["smart_graph_series"] == ["score"]
    assert prefs["graph_days"] == 7
