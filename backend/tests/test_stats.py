import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base, get_db
from main import app

TEST_DATABASE_URL = "sqlite:///./test_stats_db.db"


@pytest.fixture
def db_session():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists("./test_stats_db.db"):
            os.remove("./test_stats_db.db")


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    from routers.users import require_admin

    def override_require_admin():
        return models.User(username="test_admin", is_superadmin=True)

    app.dependency_overrides[require_admin] = override_require_admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


NOW = datetime.utcnow()


def add_node(db, hostname, ip, group=None, upload_rate_limit=None):
    node = models.Node(
        hostname=hostname,
        ip_address=ip,
        group_id=group.id if group else None,
        upload_rate_limit=upload_rate_limit,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def add_group(db, name, interval="weekly", start="02:00", end="05:00", upload_rate_limit=None):
    group = models.BackupGroup(
        name=name,
        interval=interval,
        start_time=start,
        end_time=end,
        upload_rate_limit=upload_rate_limit,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def add_history(db, node, name, *, status="SUCCESS", days_ago=1, original=0,
                dedup=0, avg=None, mx=None, duration=None, log=None, category=None):
    row = models.BackupHistory(
        node_id=node.id,
        archive_name=name,
        timestamp=NOW - timedelta(days=days_ago),
        original_size=original,
        deduplicated_size=dedup,
        status=status,
        avg_speed_mbps=avg,
        max_speed_mbps=mx,
        duration_seconds=duration,
        log_output=log,
        error_category=category,
    )
    db.add(row)
    db.commit()
    return row


# --- /api/stats -------------------------------------------------------------

def test_totals_cover_every_archive_not_just_the_first_per_node(client, db_session):
    """The old endpoint summed each node's oldest backup only, so three
    archives stood in for the whole fleet."""
    node1 = add_node(db_session, "node-1", "192.168.1.101")
    node2 = add_node(db_session, "node-2", "192.168.1.102")

    add_history(db_session, node1, "n1-a", days_ago=2, original=10000, dedup=2000)
    add_history(db_session, node1, "n1-b", days_ago=1, original=10000, dedup=100)
    add_history(db_session, node2, "n2-a", days_ago=3, original=5000, dedup=1000)

    data = client.get("/api/stats").json()

    assert data["total_original_size_bytes"] == 25000
    assert data["total_deduplicated_size_bytes"] == 3100
    # The saving is scoped to base backups, so it is not these figures minus
    # each other — see the cross-node tests below.
    assert data["base_original_size_bytes"] == 15000
    assert data["base_deduplicated_size_bytes"] == 3000


def test_failed_archives_are_counted_but_do_not_contribute_size(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "ok", original=5000, dedup=1000)
    add_history(db_session, node, "bad", status="FAILED", original=5000, dedup=5000)

    data = client.get("/api/stats").json()

    assert data["total_archives"] == 2
    assert data["successful_archives"] == 1
    assert data["failed_archives"] == 1
    assert data["total_deduplicated_size_bytes"] == 1000


def test_the_failure_rate_the_old_cards_never_showed(client, db_session):
    """11 of 25 succeeding is the single most important fact on the page."""
    node = add_node(db_session, "node-1", "192.168.1.101")
    for i in range(11):
        add_history(db_session, node, f"ok-{i}", original=100, dedup=10)
    for i in range(14):
        add_history(db_session, node, f"bad-{i}", status="FAILED")

    data = client.get("/api/stats").json()

    assert data["total_archives"] == 25
    assert data["success_rate"] == 44.0


def test_an_empty_fleet_reports_nothing_rather_than_a_ratio_of_one(client, db_session):
    data = client.get("/api/stats").json()

    assert data["total_archives"] == 0
    assert data["deduplication_ratio"] is None
    assert data["success_rate"] is None


def test_the_saving_is_measured_across_nodes_not_across_repeat_backups(client, db_session):
    """A node re-backing up unchanged data is not deduplication. Counting it
    would make the ratio measure how rarely the node changes."""
    node1 = add_node(db_session, "node-1", "192.168.1.101")
    node2 = add_node(db_session, "node-2", "192.168.1.102")

    # Each node's base backup, then six near-empty incrementals.
    add_history(db_session, node1, "n1-base", days_ago=40, original=3_000_000_000, dedup=900_000_000)
    add_history(db_session, node2, "n2-base", days_ago=40, original=2_000_000_000, dedup=400_000_000)
    for i in range(6):
        add_history(db_session, node1, f"n1-inc-{i}", days_ago=i + 1,
                    original=3_000_000_000, dedup=400_000)
        add_history(db_session, node2, f"n2-inc-{i}", days_ago=i + 1,
                    original=2_000_000_000, dedup=300_000)

    data = client.get("/api/stats").json()

    assert data["base_nodes"] == 2
    assert data["base_original_size_bytes"] == 5_000_000_000
    assert data["base_deduplicated_size_bytes"] == 1_300_000_000
    assert data["deduplication_ratio"] == pytest.approx(3.85, rel=0.01)
    assert data["saved_space_bytes"] == 3_700_000_000

    # The cumulative totals still cover everything; only the saving is scoped.
    assert data["total_original_size_bytes"] == 35_000_000_000


def test_the_base_survives_retention_pruning_the_first_archive(client, db_session):
    """global_daily_prune deletes history rows for archives borg no longer has,
    so a node's earliest surviving row eventually becomes an incremental."""
    node = add_node(db_session, "node-1", "192.168.1.101")
    # The real first backup is gone; what remains is an incremental and a later
    # full-ish archive that now carries the bulk of the node's unique data.
    add_history(db_session, node, "n1-inc", days_ago=10, original=3_000_000_000, dedup=400_000)
    add_history(db_session, node, "n1-bulk", days_ago=5, original=3_000_000_000, dedup=900_000_000)

    data = client.get("/api/stats").json()

    assert data["base_deduplicated_size_bytes"] == 900_000_000
    assert data["deduplication_ratio"] == pytest.approx(3.33, rel=0.01)


def test_disk_figures_are_reported_separately_from_the_sums(client, db_session):
    """They answer different questions and must not be conflated the way the
    mislabelled 'Local Backup Storage' card did."""
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "ok", original=10000, dedup=4000)

    data = client.get("/api/stats").json()

    assert data["saved_space_bytes"] == 6000
    assert "disk_total_bytes" in data
    assert "repo_size_bytes" in data


# --- /api/stats/insights: reliability ---------------------------------------

def test_insights_reports_runs_inside_the_window_only(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "recent", days_ago=3)
    add_history(db_session, node, "ancient", days_ago=200)

    data = client.get("/api/stats/insights?days=30").json()

    assert data["window_days"] == 30
    assert data["reliability"]["total_runs"] == 1


def test_a_node_failing_repeatedly_is_listed_with_its_streak(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "old-ok", days_ago=10)
    add_history(db_session, node, "f1", status="FAILED", days_ago=3)
    add_history(db_session, node, "f2", status="FAILED", days_ago=2)
    add_history(db_session, node, "f3", status="FAILED", days_ago=1)

    failing = client.get("/api/stats/insights").json()["reliability"]["failing_nodes"]

    assert len(failing) == 1
    assert failing[0]["hostname"] == "node-1"
    assert failing[0]["consecutive_failures"] == 3


def test_a_node_that_recovered_is_not_listed_as_failing(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "f1", status="FAILED", days_ago=3)
    add_history(db_session, node, "ok", days_ago=1)

    reliability = client.get("/api/stats/insights").json()["reliability"]

    assert reliability["failing_nodes"] == []
    assert reliability["stale_nodes"] == []


def test_staleness_follows_the_group_interval_not_a_flat_cutoff(client, db_session):
    """A monthly node three weeks after its last backup is on schedule."""
    monthly = add_group(db_session, "monthly-sites", interval="monthly")
    weekly = add_group(db_session, "weekly-sites", interval="weekly")
    slow = add_node(db_session, "monthly-node", "192.168.1.101", group=monthly)
    fast = add_node(db_session, "weekly-node", "192.168.1.102", group=weekly)

    add_history(db_session, slow, "m-ok", days_ago=21)
    add_history(db_session, fast, "w-ok", days_ago=21)

    stale = client.get("/api/stats/insights?days=90").json()["reliability"]["stale_nodes"]

    assert [n["hostname"] for n in stale] == ["weekly-node"]


def test_a_node_with_no_history_at_all_is_not_flagged(client, db_session):
    """Every freshly added node would otherwise light up the panel."""
    add_node(db_session, "brand-new", "192.168.1.101")

    reliability = client.get("/api/stats/insights").json()["reliability"]

    assert reliability["stale_nodes"] == []
    assert reliability["nodes_never_succeeded"] == 0


def test_a_node_that_has_only_ever_failed_is_stale(client, db_session):
    node = add_node(db_session, "never-worked", "192.168.1.101")
    add_history(db_session, node, "f1", status="FAILED", days_ago=1)

    reliability = client.get("/api/stats/insights").json()["reliability"]

    assert reliability["nodes_never_succeeded"] == 1
    assert reliability["stale_nodes"][0]["hostname"] == "never-worked"
    assert reliability["stale_nodes"][0]["days_since_success"] is None


def test_failure_categories_are_counted(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "f1", status="FAILED", days_ago=1,
                log="ssh: connect to host 10.0.0.5 port 22: No route to host")
    add_history(db_session, node, "f2", status="FAILED", days_ago=2,
                log="ssh: connect to host 10.0.0.5 port 22: No route to host")
    add_history(db_session, node, "f3", status="FAILED", days_ago=3,
                log="Permission denied (publickey).")

    top = client.get("/api/stats/insights").json()["reliability"]["top_failures"]

    assert top[0] == {"category": "UNREACHABLE", "count": 2}
    assert {"category": "AUTH", "count": 1} in top


def test_old_failures_get_their_category_written_back_once(client, db_session):
    """Deriving it on every request would mean reading every failed log each
    time the page is opened."""
    node = add_node(db_session, "node-1", "192.168.1.101")
    row = add_history(db_session, node, "f1", status="FAILED", days_ago=1,
                      log="OSError: [Errno 28] No space left on device")
    assert row.error_category is None

    client.get("/api/stats/insights")

    db_session.expire_all()
    assert db_session.query(models.BackupHistory).first().error_category == "DISK_FULL"


# --- /api/stats/insights: speed ---------------------------------------------

def test_speed_section_ranks_the_slowest_nodes_first(client, db_session):
    fast = add_node(db_session, "fast", "192.168.1.101")
    slow = add_node(db_session, "slow", "192.168.1.102")
    add_history(db_session, fast, "f1", avg=90.0, mx=100.0, days_ago=1)
    add_history(db_session, slow, "s1", avg=4.0, mx=5.0, days_ago=1)

    speed = client.get("/api/stats/insights").json()["speed"]

    assert speed["measured_runs"] == 2
    assert [n["hostname"] for n in speed["slowest_nodes"]] == ["slow", "fast"]


def test_a_node_whose_cap_is_the_bottleneck_is_marked_as_such(client, db_session):
    # 1250 KiB/s = 10.24 Mbit/s; peaking at 10 means the cap is what binds.
    node = add_node(db_session, "capped", "192.168.1.101", upload_rate_limit=1250)
    add_history(db_session, node, "c1", avg=9.8, mx=10.0, days_ago=1)

    speed = client.get("/api/stats/insights").json()["speed"]
    entry = speed["slowest_nodes"][0]

    assert entry["limit_source"] == "node"
    assert entry["limit_binding"] is True
    assert speed["capped_nodes"] == 1


def test_a_slow_node_far_below_its_cap_is_not_blamed_on_the_cap(client, db_session):
    group = add_group(db_session, "capped-group", upload_rate_limit=12500)  # ~102 Mbit/s
    node = add_node(db_session, "starved", "192.168.1.101", group=group)
    add_history(db_session, node, "s1", avg=3.0, mx=4.0, days_ago=1)

    entry = client.get("/api/stats/insights").json()["speed"]["slowest_nodes"][0]

    assert entry["limit_source"] == "group"
    assert entry["limit_binding"] is False


def test_runs_with_no_speed_recorded_are_left_out(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "old", days_ago=1)  # avg is None

    speed = client.get("/api/stats/insights").json()["speed"]

    assert speed["measured_runs"] == 0
    assert speed["median_mbps"] is None
    assert speed["slowest_nodes"] == []


# --- /api/stats/insights: duration ------------------------------------------

def test_a_node_overrunning_its_window_is_flagged(client, db_session):
    group = add_group(db_session, "night", start="02:00", end="03:00")  # 60 minutes
    node = add_node(db_session, "slowpoke", "192.168.1.101", group=group)
    add_history(db_session, node, "d1", duration=3500, days_ago=1)  # 58 of 60 minutes

    duration = client.get("/api/stats/insights").json()["duration"]
    entry = duration["longest_nodes"][0]

    assert entry["window_minutes"] == 60
    assert entry["at_risk"] is True
    assert duration["nodes_at_risk"] == 1


def test_a_node_comfortably_inside_its_window_is_not_flagged(client, db_session):
    group = add_group(db_session, "night", start="02:00", end="05:00")  # 180 minutes
    node = add_node(db_session, "quick", "192.168.1.101", group=group)
    add_history(db_session, node, "d1", duration=600, days_ago=1)

    duration = client.get("/api/stats/insights").json()["duration"]

    assert duration["longest_nodes"][0]["at_risk"] is False
    assert duration["nodes_at_risk"] == 0


def test_the_worst_run_decides_the_risk_not_the_median(client, db_session):
    """A median inside the window is no comfort if one run in three overruns."""
    group = add_group(db_session, "night", start="02:00", end="03:00")
    node = add_node(db_session, "spiky", "192.168.1.101", group=group)
    add_history(db_session, node, "d1", duration=300, days_ago=3)
    add_history(db_session, node, "d2", duration=300, days_ago=2)
    add_history(db_session, node, "d3", duration=3400, days_ago=1)

    entry = client.get("/api/stats/insights").json()["duration"]["longest_nodes"][0]

    assert entry["at_risk"] is True
    assert entry["max_seconds"] == 3400


def test_a_node_with_no_group_has_no_window_to_compare_against(client, db_session):
    node = add_node(db_session, "ungrouped", "192.168.1.101")
    add_history(db_session, node, "d1", duration=99999, days_ago=1)

    entry = client.get("/api/stats/insights").json()["duration"]["longest_nodes"][0]

    assert entry["window_minutes"] is None
    assert entry["window_usage"] is None
    assert entry["at_risk"] is False


# --- /api/stats/insights: capacity ------------------------------------------

def test_capacity_ranks_which_nodes_drive_growth(client, db_session):
    big = add_node(db_session, "big", "192.168.1.101")
    small = add_node(db_session, "small", "192.168.1.102")
    add_history(db_session, big, "b1", dedup=900, days_ago=1)
    add_history(db_session, small, "s1", dedup=100, days_ago=1)

    consumers = client.get("/api/stats/insights").json()["capacity"]["top_consumers"]

    assert [c["hostname"] for c in consumers] == ["big", "small"]
    assert consumers[0]["share"] == 0.9


def test_inflow_is_averaged_over_the_window_not_over_active_days(client, db_session):
    node = add_node(db_session, "node-1", "192.168.1.101")
    add_history(db_session, node, "a", dedup=7_000_000, days_ago=1)

    capacity = client.get("/api/stats/insights?days=7").json()["capacity"]

    assert capacity["daily_inflow_bytes"] == pytest.approx(1_000_000)


def test_a_repository_that_is_not_growing_has_no_forecast(client, db_session):
    add_node(db_session, "node-1", "192.168.1.101")

    capacity = client.get("/api/stats/insights").json()["capacity"]

    assert capacity["daily_inflow_bytes"] is None
    assert capacity["days_until_full"] is None
    assert capacity["projected_full_date"] is None


def test_the_window_is_bounded(client, db_session):
    assert client.get("/api/stats/insights?days=0").status_code == 422
    assert client.get("/api/stats/insights?days=400").status_code == 422
