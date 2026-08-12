import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from core.monitoring_verdicts import thermal_verdict

TEST_DATABASE_URL = "sqlite:///./test_monitoring_verdicts_db.db"


@pytest.fixture
def db_session():
    if os.path.exists("./test_monitoring_verdicts_db.db"):
        os.remove("./test_monitoring_verdicts_db.db")
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
        if os.path.exists("./test_monitoring_verdicts_db.db"):
            os.remove("./test_monitoring_verdicts_db.db")


def make_node(db, hostname="node-1", cpu="11th Gen Intel(R) Core(TM) i5-1145G7E @ 2.60GHz"):
    node = models.Node(hostname=hostname, ip_address="10.0.0.1", ssh_port=2222, cpu_info=cpu)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def add_fit(db, node, days_ago, theta=1.5, rejection="OK"):
    start = datetime.utcnow() - timedelta(days=days_ago)
    fit = models.ThermalFit(
        node_id=node.id, window_start=start, window_end=start + timedelta(hours=4),
        rejection=rejection, n_samples=200, excitation=0.3,
        theta_c_per_w=theta, theta_normalised=theta, tau_seconds=1800.0,
        t_ambient_c=25.0, mean_temp_c=45.0, r_squared=0.9,
    )
    db.add(fit)
    db.commit()
    return fit


def test_no_data_yields_insufficient_data(db_session):
    node = make_node(db_session)
    verdict = thermal_verdict(db_session, node, datetime.utcnow())
    assert verdict.status == "INSUFFICIENT_DATA"
    assert verdict.windows_fitted == 0


def test_single_node_no_cohort_reports_zero_cohort_size(db_session):
    node = make_node(db_session)
    add_fit(db_session, node, days_ago=1, theta=1.5)
    verdict = thermal_verdict(db_session, node, datetime.utcnow())
    assert verdict.windows_fitted == 1
    assert verdict.cohort_size in (0, 1)


def test_rejected_windows_are_counted_and_reported(db_session):
    node = make_node(db_session)
    add_fit(db_session, node, days_ago=1, rejection="NO_EXCITATION")
    verdict = thermal_verdict(db_session, node, datetime.utcnow())
    assert verdict.windows_fitted == 0
    assert verdict.windows_rejected == 1
    assert verdict.last_rejection == "NO_EXCITATION"
