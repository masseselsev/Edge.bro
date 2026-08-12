"""Every database session must be closed in a finally.

This is a static check rather than a runtime one because the failure it
guards against is invisible until it is expensive. A session opened with
`SessionLocal()` and closed at the end of the function body leaks whenever
anything in between raises — and SQLAlchemy opens a transaction on the first
query, so the leaked connection sits `idle in transaction` holding row locks
on whatever it touched.

Found in production on 2026-08-12: a worker session had been idle in
transaction for **1 day 16 hours** holding a lock on `settings`, left behind
by `global_daily_prune`. The next `ALTER TABLE settings` waited behind it and
timed out, which is to say the leak had quietly made the database
unmigratable. Nothing alerted; it surfaced only because a migration happened
to be run.

Long-lived tasks make this worse rather than better: bootstrap reads Settings
and then runs Ansible for minutes, so the window between opening the
transaction and any failure is enormous.
"""
import ast
import pathlib

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Directories that are not shipped code. Simulators and load-test harnesses
#: are developer tools run by hand, never on the worker.
EXCLUDED_PARTS = {"venv", "tests", "__pycache__", "alembic"}
EXCLUDED_FILES = {
    "scheduler_simulator.py",
    "scheduler_large_scale_test.py",
}


def _python_sources():
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.name in EXCLUDED_FILES:
            continue
        yield path


def _opens_a_session(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name == "SessionLocal":
                return True
    return False


def _closes_in_finally(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for statement in node.finalbody:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call) and getattr(inner.func, "attr", None) == "close":
                    return True
    return False


def _yields_the_session(func: ast.AST) -> bool:
    """FastAPI dependencies hand the session out and close it after the yield.

    `get_db` is the canonical shape: it already has try/finally, but a
    generator dependency is a legitimate pattern either way and is not what
    this check is looking for.
    """
    return any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(func))


def _functions_opening_sessions():
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _opens_a_session(node):
                continue
            yield path.relative_to(BACKEND_ROOT), node


def test_the_scan_actually_finds_sessions():
    """A guard on the guard: if the AST walk silently matched nothing, every
    assertion below would pass while checking exactly zero code."""
    found = list(_functions_opening_sessions())
    assert len(found) >= 10, f"expected the codebase to open many sessions, saw {len(found)}"


def test_every_session_is_closed_in_a_finally():
    offenders = [
        f"{path}:{func.lineno} {func.name}()"
        for path, func in _functions_opening_sessions()
        if not _closes_in_finally(func) and not _yields_the_session(func)
    ]

    assert not offenders, (
        "These functions open a database session without closing it in a "
        "finally. An exception before the close leaves the connection idle in "
        "transaction holding row locks, which eventually blocks migrations:\n  "
        + "\n  ".join(offenders)
    )
