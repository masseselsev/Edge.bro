"""The clock helper, and the sweep that keeps `datetime.utcnow()` from returning.

`datetime.utcnow()` is deprecated and scheduled for removal, so the calls had
to move. The replacement the deprecation notice suggests --
`datetime.now(timezone.utc)` -- is aware, and every timestamp column in this
schema is naive, so adopting it literally would have replaced a warning with
`TypeError: can't subtract offset-naive and offset-aware datetimes` in code
like `utcnow() - row.created_at`. Mostly inside nightly tasks, where nobody is
watching.

`core.clock.utcnow` therefore returns naive UTC: the deprecation is gone and
the semantics are unchanged. These tests pin both halves of that, and the
sweep at the bottom is what stops the next person from "fixing" a single call
site back to the aware version and reintroducing the mismatch one file at a
time.
"""
import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from core.clock import utcnow

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Only this module may name the deprecated call, and only to explain itself.
SANCTIONED = {"core/clock.py", "tests/test_clock.py"}


def test_returns_a_naive_datetime():
    """The whole point: it must be comparable with what the database returns."""
    assert utcnow().tzinfo is None


def test_the_value_is_utc_not_local():
    """A naive datetime carries no evidence of its zone, so the only way to be
    wrong here is silently. Pinned against an aware reference."""
    reference = datetime.now(timezone.utc).replace(tzinfo=None)

    assert abs(utcnow() - reference) < timedelta(seconds=5)


def test_it_can_be_subtracted_from_a_naive_database_timestamp():
    """The exact expression that `datetime.now(timezone.utc)` would break, and
    the reason this helper exists rather than a plain search and replace."""
    from_db = datetime(2026, 1, 1, 12, 0, 0)  # naive, as SQLAlchemy hands it back

    age = utcnow() - from_db

    assert age.total_seconds() > 0


def test_mixing_in_an_aware_value_is_still_an_error():
    """States the hazard rather than assuming everyone knows it: if this ever
    starts passing, the helper has quietly become aware and every naive
    comparison in the codebase is now a latent TypeError."""
    with pytest.raises(TypeError):
        utcnow() - datetime.now(timezone.utc)


def test_it_advances():
    first = utcnow()
    second = utcnow()

    assert second >= first


def _source_files():
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(part in {"venv", "__pycache__", "alembic"} for part in path.parts):
            continue
        yield path


def test_the_sweep_actually_reads_files():
    """A guard on the guard -- a walk matching nothing would pass silently."""
    assert len(list(_source_files())) >= 50


def test_no_source_calls_datetime_utcnow():
    """Parsed rather than grepped, so a mention inside a comment or docstring
    explaining the migration is not mistaken for a call."""
    offenders = []
    for path in _source_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in SANCTIONED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "utcnow":
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "datetime.utcnow() is deprecated and removal is scheduled. Use "
        "`from core.clock import utcnow` -- it returns naive UTC, which is "
        f"what the database columns hold. Found at: {offenders}"
    )
