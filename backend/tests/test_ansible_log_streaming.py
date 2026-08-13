"""run_ansible_playbook must never overwrite a task_id's prior log output.

Reprovisioning drives two playbooks under one task_id (bootstrap.yml, then
deploy_monitoring.yml). Each call's log_accumulator starts empty, so writing
it straight to the DB used to erase whatever the previous playbook had
already logged, and the mid-run progress bar would vanish until the new
playbook produced its own [PROGRESS] line. See ansible_utils.py's
log_prefix handling.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import ansible_utils
import models
from database import Base

TEST_DB_PATH = "./test_ansible_log_streaming_db.db"


@pytest.fixture
def db_session():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db, TestingSessionLocal
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)


def _fake_process(lines, returncode=0):
    proc = MagicMock()
    remaining = list(lines)

    def readline():
        return remaining.pop(0) if remaining else ""

    def poll():
        return None if remaining else returncode

    proc.stdout.readline.side_effect = readline
    proc.poll.side_effect = poll
    proc.wait.return_value = returncode
    return proc


def _run_playbook(task_id, playbook_name, lines):
    with patch("subprocess.Popen", return_value=_fake_process(lines)):
        return ansible_utils.run_ansible_playbook(
            task_id=task_id,
            playbook_name=playbook_name,
            host_ip="1.2.3.4",
            ssh_port=22,
            extra_vars={},
            ssh_key_path="/tmp/fake_key",
        )


def test_log_output_accumulates_across_playbook_runs_sharing_a_task_id(monkeypatch, db_session):
    db, session_local = db_session
    monkeypatch.setattr("database.SessionLocal", session_local)
    db.add(models.TaskLog(id="shared-task", task_type="BOOTSTRAP", status="RUNNING", log_output=""))
    db.commit()

    _run_playbook("shared-task", "bootstrap.yml", [
        "TASK [Verify OS type and version compatibility] ****\n",
        "ok: [1.2.3.4]\n",
        "PLAY RECAP ****\n",
        "1.2.3.4 : ok=1 changed=0\n",
    ])
    _run_playbook("shared-task", "deploy_monitoring.yml", [
        "TASK [Show the capability report] ****\n",
        "ok: [1.2.3.4]\n",
        "PLAY RECAP ****\n",
        "1.2.3.4 : ok=1 changed=0\n",
    ])

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "shared-task").first()
    assert "Verify OS type and version compatibility" in log.log_output
    assert "Show the capability report" in log.log_output


def test_play_recap_progress_line_is_not_emitted_for_unrecognised_playbooks(monkeypatch, db_session):
    db, session_local = db_session
    monkeypatch.setattr("database.SessionLocal", session_local)
    db.add(models.TaskLog(id="progress-task", task_type="BOOTSTRAP", status="RUNNING", log_output=""))
    db.commit()

    _run_playbook("progress-task", "custom_unknown.yml", [
        "TASK [Some unknown custom task] ****\n",
        "ok: [1.2.3.4]\n",
        "PLAY RECAP ****\n",
        "1.2.3.4 : ok=1 changed=0\n",
    ])

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "progress-task").first()
    assert "[PROGRESS]" not in log.log_output


def test_play_recap_progress_line_is_emitted_for_monitoring(monkeypatch, db_session):
    db, session_local = db_session
    monkeypatch.setattr("database.SessionLocal", session_local)
    db.add(models.TaskLog(id="monitoring-progress-task", task_type="BOOTSTRAP", status="RUNNING", log_output=""))
    db.commit()

    _run_playbook("monitoring-progress-task", "deploy_monitoring.yml", [
        "TASK [Show the capability report] ****\n",
        "ok: [1.2.3.4]\n",
        "PLAY RECAP ****\n",
        "1.2.3.4 : ok=1 changed=0\n",
    ])

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "monitoring-progress-task").first()
    # A translation key, not a sentence: the language is chosen when the log is
    # rendered, not when it is written. See ansible_utils.PROGRESS_TASKS.
    assert "[PROGRESS] 95:monitoring_show_capability" in log.log_output
    assert "[PROGRESS] 100:monitoring_complete" in log.log_output


def test_play_recap_progress_line_is_still_emitted_for_bootstrap(monkeypatch, db_session):
    db, session_local = db_session
    monkeypatch.setattr("database.SessionLocal", session_local)
    db.add(models.TaskLog(id="bootstrap-progress-task", task_type="BOOTSTRAP", status="RUNNING", log_output=""))
    db.commit()

    _run_playbook("bootstrap-progress-task", "bootstrap.yml", [
        "PLAY RECAP ****\n",
        "1.2.3.4 : ok=1 changed=0\n",
    ])

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "bootstrap-progress-task").first()
    assert "[PROGRESS] 100:bootstrap_complete" in log.log_output


def test_prepare_progress_fires_at_all(monkeypatch, db_session):
    """prepare.yml used to emit no progress whatsoever.

    Its lookup table was referenced but never defined, so every prepare run
    raised NameError into a bare `except Exception: pass` — a silent, permanent
    dead progress bar with nothing in the log to say so.
    """
    db, session_local = db_session
    monkeypatch.setattr("database.SessionLocal", session_local)
    db.add(models.TaskLog(id="prepare-progress-task", task_type="PREPARE", status="RUNNING", log_output=""))
    db.commit()

    _run_playbook("prepare-progress-task", "prepare.yml", [
        "TASK [Backup remote fstab] ****\n",
        "ok: [1.2.3.4]\n",
        "TASK [Update GRUB bootloader configuration] ****\n",
        "changed: [1.2.3.4]\n",
        "PLAY RECAP ****\n",
    ])

    log = db.query(models.TaskLog).filter(models.TaskLog.id == "prepare-progress-task").first()
    assert "[PROGRESS] 10:prepare_backup_fstab" in log.log_output
    assert "[PROGRESS] 90:prepare_updating_grub" in log.log_output
    assert "[PROGRESS] 100:prepare_complete" in log.log_output


def test_every_progress_key_has_an_english_translation():
    """A key with no entry in translations.ts renders as the raw key.

    The fallback that lets the kiosk's plain-English lines through is the same
    fallback that would quietly show an operator "monitoring_drivetemp", so the
    keys and the translation file have to be checked against each other.
    """
    import pathlib
    import re

    import ansible_utils

    expected = {
        f"{kind}_{trans_key}"
        for kind, table in ansible_utils.PROGRESS_TASKS.items()
        for _, trans_key in table.values()
    } | {f"{kind}_complete" for kind in ansible_utils.PROGRESS_TASKS}

    translations = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "frontend" / "src" / "i18n" / "translations.ts"
    ).read_text(encoding="utf-8")
    defined = set(re.findall(r"^\s{4}(\w+):", translations, re.M))

    missing = sorted(expected - defined)
    assert not missing, (
        "These progress keys are written into task logs but have no entry in "
        f"frontend/src/i18n/translations.ts: {missing}"
    )
