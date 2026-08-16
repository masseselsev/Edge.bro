"""The USB-Kiosk template rebuild has to be driven by something that runs
repeatedly, not once at startup.

The bug these cover: `payload_hash` includes /opt/frontend_build, a volume the
frontend container refills from its own image every time it starts, and
compose starts the frontend only after the backend reports healthy. The
startup hook therefore always hashed the *previous* release's dashboard
bundle. On a release whose only payload change was the dashboard - which is
most of them - it concluded "unchanged" seconds before the new bundle landed,
and nothing looked again. The status endpoint recomputes the hash on every
poll, so the card read OUTDATED indefinitely while no rebuild was pending,
and the template stayed a release behind until something unrelated restarted
the backend.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base

TEST_DATABASE_URL = "sqlite:///./test_kiosk_template_check_db.db"


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
        engine.dispose()
        if os.path.exists("./test_kiosk_template_check_db.db"):
            os.remove("./test_kiosk_template_check_db.db")


class _Cache:
    """A cache directory with the files the check looks for."""

    def __init__(self, tmp_path, base=True, client=True, downloading=False):
        self.dir = tmp_path
        self.base = tmp_path / "base.iso"
        self.base_tmp = tmp_path / "base.iso.tmp"
        self.client = tmp_path / "technician_client_v1.iso"
        if base:
            self.base.write_bytes(b"base")
        if client:
            self.client.write_bytes(b"client")
        if downloading:
            self.base_tmp.write_bytes(b"partial")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Point iso_tasks at a scratch cache and record what it would dispatch."""
    import iso_tasks

    triggered = []
    marker = {"value": None}

    monkeypatch.setattr(iso_tasks, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(iso_tasks, "BASE_ISO_PATH", str(tmp_path / "base.iso"))
    monkeypatch.setattr(iso_tasks, "BASE_ISO_PATH_TMP", str(tmp_path / "base.iso.tmp"))
    monkeypatch.setattr(
        iso_tasks, "trigger_base_iso_rebuild", lambda db: triggered.append(db)
    )
    # Redis is not part of what these assert; the marker is held in memory so
    # the behaviour under test stays visible.
    monkeypatch.setattr(iso_tasks, "_failed_build_hash", lambda: marker["value"])
    monkeypatch.setattr(
        iso_tasks, "_set_failed_build_hash", lambda v: marker.update(value=v)
    )

    def set_hashes(current, stored):
        import payload_hash

        monkeypatch.setattr(payload_hash, "compute_payload_hash", lambda: current)
        monkeypatch.setattr(payload_hash, "read_stored_hash", lambda: stored)

    return type(
        "Harness",
        (),
        {
            "tmp_path": tmp_path,
            "triggered": triggered,
            "marker": marker,
            "set_hashes": staticmethod(set_hashes),
        },
    )()


def test_matching_sources_build_nothing(harness, db_session):
    import iso_tasks

    _Cache(harness.tmp_path)
    harness.set_hashes("abc123", "abc123")

    reason = iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert harness.triggered == []
    assert "up to date" in reason


def test_changed_sources_trigger_a_rebuild(harness, db_session):
    """The case the startup-only check kept missing.

    The template was built from bundle `old`; the dashboard has since been
    replaced with `new`. Nothing restarted the backend, so if this does not
    fire, nothing ever does.
    """
    import iso_tasks

    _Cache(harness.tmp_path)
    harness.set_hashes("new_bundle_hash", "old_bundle_hash")

    reason = iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert len(harness.triggered) == 1, "a stale template was left unbuilt"
    assert "build triggered" in reason


def test_a_never_built_template_is_built(harness, db_session):
    import iso_tasks

    _Cache(harness.tmp_path, client=False)
    harness.set_hashes("anything", None)

    reason = iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert len(harness.triggered) == 1
    assert "template missing" in reason


def test_nothing_happens_without_a_base_image(harness, db_session):
    import iso_tasks

    _Cache(harness.tmp_path, base=False, client=False)
    harness.set_hashes("anything", None)

    assert harness.triggered == []
    iso_tasks.rebuild_kiosk_template_if_stale(db_session)
    assert harness.triggered == []


def test_a_half_downloaded_base_image_is_left_alone(harness, db_session):
    """Building on a partial download wastes ten minutes and fails.

    The download triggers its own rebuild when it finishes, so there is
    nothing to do but wait.
    """
    import iso_tasks

    _Cache(harness.tmp_path, downloading=True)
    harness.set_hashes("new", "old")

    reason = iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert harness.triggered == []
    assert "downloading" in reason


def test_a_failed_build_is_not_retried_every_tick(harness, db_session):
    """Ten minutes of xorriso and several gigabytes of scratch space, every
    ten minutes, for sources already known not to build."""
    import iso_tasks

    _Cache(harness.tmp_path)
    harness.set_hashes("broken_sources", "old")
    harness.marker["value"] = "broken_sources"

    reason = iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert harness.triggered == []
    assert "not retrying" in reason


def test_a_further_source_change_resumes_retrying(harness, db_session):
    """The guard is on the exact sources that failed, not on failure itself -
    otherwise fixing the cause would not be enough to get a build."""
    import iso_tasks

    _Cache(harness.tmp_path)
    harness.set_hashes("sources_after_the_fix", "old")
    harness.marker["value"] = "broken_sources"

    iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert len(harness.triggered) == 1


def test_the_failure_marker_is_retired_once_sources_match(harness, db_session):
    import iso_tasks

    _Cache(harness.tmp_path)
    harness.set_hashes("same", "same")
    harness.marker["value"] = "same"

    iso_tasks.rebuild_kiosk_template_if_stale(db_session)

    assert harness.marker["value"] is None


def test_the_check_is_on_the_beat_schedule():
    """Without a schedule entry the whole mechanism is dead code, and the
    symptom is silence rather than an error."""
    import tasks

    entry = tasks.celery_app.conf.beat_schedule.get("kiosk-template-check-task")
    assert entry is not None, "the periodic template check is not scheduled"
    assert entry["task"] == "tasks.kiosk_template_check_task"
    assert entry["schedule"] <= 900, "too slow to pick up an upgrade"


def test_the_check_runs_off_the_iso_queue():
    """It must not queue behind the ten-minute build it exists to start."""
    from celery_app import celery_app, QUEUE_ISO

    routes = celery_app.conf.task_routes
    assert "tasks.kiosk_template_check_task" not in routes or (
        routes["tasks.kiosk_template_check_task"]["queue"] != QUEUE_ISO
    )
    assert celery_app.conf.task_default_queue != QUEUE_ISO
