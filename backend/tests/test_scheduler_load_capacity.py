"""Whether the load calendar promises capacity the storage can deliver.

`/api/groups/scheduler-load` is what an operator plans a fleet's schedule
against: it reports, per group, whether the busiest day's transfer hours fit
inside the execution window. The verdict is `busiest_hours <= window_hours *
concurrency`.

That multiplication is only honest if the backups really can run in parallel.
Borg holds a repository's lock for the whole of `borg create`, so backups that
land on one shard run strictly one at a time — a group permitting five gets
one, and the calendar was reporting five times the capacity that exists. The
plan looks fine, the window overruns, and nothing in the projection hinted at
it.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from core import repo_paths
from routers.groups import get_scheduler_load

TEST_DATABASE_URL = "sqlite:///./test_scheduler_load_capacity_db.db"


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
        if os.path.exists("./test_scheduler_load_capacity_db.db"):
            os.remove("./test_scheduler_load_capacity_db.db")


def _fleet(db, node_count=40, concurrency=5):
    db.add(models.Settings())
    group = models.BackupGroup(
        name="nightly", interval="weekly", target_week=1,
        start_time="02:00", end_time="05:00", timezone="UTC",
        concurrency_limit=concurrency, randomize_days=False,
    )
    db.add(group)
    db.commit()
    db.refresh(group)

    for i in range(node_count):
        db.add(models.Node(
            hostname=f"n{i:03d}", ip_address=f"10.0.0.{i + 1}",
            status="READY", group_id=group.id,
        ))
    db.commit()
    return group


def _fit(db):
    report = get_scheduler_load(db=db)
    assert report["group_fit"], "no group in the projection to check"
    return report["group_fit"][0]


def test_one_repository_means_one_writer_however_many_the_group_permits(
    db_session, monkeypatch
):
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    _fleet(db_session, concurrency=5)

    fit = _fit(db_session)
    assert fit["concurrency"] == 1, (
        "the calendar promised parallel backups that one repository lock "
        "cannot deliver"
    )
    assert fit["capacity_hours"] == pytest.approx(fit["window_hours"])


def test_capacity_grows_with_the_shards(db_session, monkeypatch):
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 5)
    _fleet(db_session, concurrency=5)

    fit = _fit(db_session)
    assert fit["concurrency"] == 5
    assert fit["capacity_hours"] == pytest.approx(fit["window_hours"] * 5)


def test_the_group_limit_still_wins_when_it_is_the_smaller_one(
    db_session, monkeypatch
):
    """Shards raise the ceiling; they are not a reason to exceed what the
    operator allowed for a group's uplink."""
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 5)
    _fleet(db_session, concurrency=2)

    assert _fit(db_session)["concurrency"] == 2


def test_a_day_that_cannot_fit_is_reported_as_not_fitting(db_session, monkeypatch):
    """The point of the cap: the verdict has to change, not just the number.

    Enough nodes that the day's transfer exceeds a single serialised window,
    with a group that would have been told it had five times the room.
    """
    monkeypatch.setattr(repo_paths, "SHARD_COUNT", 1)
    group = _fleet(db_session, node_count=400, concurrency=5)

    fit = _fit(db_session)
    if fit["est_hours"] > fit["window_hours"]:
        assert not fit["fits"], (
            f"{fit['est_hours']}h of transfer reported as fitting a "
            f"{fit['window_hours']}h serialised window"
        )
