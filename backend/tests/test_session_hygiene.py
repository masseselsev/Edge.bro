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

The second check here is about that window rather than the leak. Closing in a
finally bounds the damage of an exception; it does nothing about a session
held open, on purpose, across an hours-long `borg create`. So a function may
not both open a session and spawn a subprocess. When it needs both, the
database work goes in a `core.db_session.session_scope()` block — whose extent
is visible in the source — and the subprocess work goes outside it.
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


#: Everything that hands control to another process for an unbounded time.
#: `run_ansible_playbook` is included because it is a playbook run — minutes —
#: wearing a function call's clothing.
SUBPROCESS_CALLS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("os", "system"),
}
#: Functions that are a subprocess in all but name. Listed explicitly because
#: the AST cannot see through a call — `harvest_io.harvest` is four SSH round
#: trips and `format_and_restore` is a whole bare-metal reinstall, and neither
#: looks like anything from here.
SUBPROCESS_HELPERS = {
    "run_ansible_playbook",
    "run_command_with_logging",
    "harvest",
    "format_and_restore",
    "check_hasp_status_on_node",
}


def _subprocess_calls(node: ast.AST):
    """Every subprocess spawn anywhere under `node`, as (line, rendered name)."""
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        target = inner.func
        attr = getattr(target, "attr", None)
        module = getattr(getattr(target, "value", None), "id", None)
        if (module, attr) in SUBPROCESS_CALLS:
            yield inner.lineno, f"{module}.{attr}"
        elif getattr(target, "id", None) in SUBPROCESS_HELPERS:
            yield inner.lineno, target.id
        elif attr in SUBPROCESS_HELPERS:
            yield inner.lineno, attr


def _session_scope_blocks(tree: ast.AST):
    """Every `with session_scope() as db:` statement in the module."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
            if name == "session_scope":
                yield node
                break


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


def test_the_subprocess_scan_actually_finds_subprocesses():
    """A guard on the guard, as above: an AST walk that matches nothing would
    make both assertions below vacuous."""
    found = 0
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        found += len(list(_subprocess_calls(tree)))
    assert found >= 20, f"expected the codebase to spawn many subprocesses, saw {found}"


def test_no_function_both_opens_a_session_and_spawns_a_subprocess():
    """One function cannot hold a connection and shell out at the same time.

    The rule is structural rather than flow-sensitive on purpose: proving from
    the AST that the close really does precede the `Popen` on every path is
    exactly the kind of reasoning that gets it wrong. Splitting the two
    concerns into a `session_scope()` block and everything outside it is
    something you can see by looking.
    """
    offenders = []
    for path, func in _functions_opening_sessions():
        spawns = sorted({name for _, name in _subprocess_calls(func)})
        if spawns:
            offenders.append(f"{path}:{func.lineno} {func.name}() spawns {', '.join(spawns)}")

    assert not offenders, (
        "These functions open a database session and also spawn a subprocess, "
        "so the connection is checked out — and likely idle in transaction — "
        "for as long as the child runs. Move the database work into a "
        "`core.db_session.session_scope()` block and the subprocess outside "
        "it:\n  " + "\n  ".join(offenders)
    )


def test_no_subprocess_runs_inside_a_session_scope():
    """The companion to the rule above: `session_scope` must stay narrow.

    Without this, the previous test is trivially satisfiable by wrapping the
    whole task body in one enormous `with` block, which is the original bug
    with better syntax.
    """
    offenders = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for block in _session_scope_blocks(tree):
            for lineno, name in _subprocess_calls(block):
                offenders.append(f"{path}:{lineno} {name} inside a session_scope block")

    assert not offenders, (
        "A subprocess is spawned while a session_scope is open, which holds "
        "the connection for the child's lifetime. Close the scope first:\n  "
        + "\n  ".join(sorted(offenders))
    )
