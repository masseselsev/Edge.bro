"""Shared machinery does not live inside a router.

Two rules, both with a specific incident behind them.

**Auth guards come from `auth`, never from another router.** They used to live
in `routers/users.py`, so every other router imported its security from a
sibling. `routers/network.py` then wrapped that import in
`except ImportError: def require_admin(): pass` — meaning any unrelated import
error inside the user router silently disabled authorization for every VPN and
WiFi write endpoint. The fallback is gone; this keeps the shape that invited it
from coming back.

**`log_to_task` comes from `core.task_log`, not from `tasks`.** Importing
`tasks` pulls in Celery, the beat schedule and every task module, so importing
a one-line logging helper from there closes a cycle. Nine modules worked around
it by deferring the import into the function body.
"""
import ast
import pathlib

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCLUDED_PARTS = {"venv", "tests", "__pycache__", "alembic"}

AUTH_NAMES = {
    "get_current_auth",
    "require_admin",
    "require_admin_plus_or_superadmin",
    "require_kiosk_or_admin",
    "require_superadmin",
    "require_user",
}


def _python_sources():
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def _imports(tree: ast.AST):
    """Every `from X import a, b` in the module, at any nesting depth."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node, node.module, {alias.name for alias in node.names}


def _parsed_sources():
    for path in _python_sources():
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue


def test_the_scan_actually_finds_imports():
    """A guard on the guard."""
    total = sum(len(list(_imports(tree))) for _, tree in _parsed_sources())
    assert total >= 100, f"expected many from-imports across the backend, saw {total}"


def test_auth_guards_are_never_imported_from_a_router():
    offenders = []
    for path, tree in _parsed_sources():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        # routers/users.py re-exports them on purpose, for its own endpoints.
        if relative == "routers/users.py":
            continue
        for node, module, names in _imports(tree):
            if module.startswith("routers.") and names & AUTH_NAMES:
                offenders.append(f"{relative}:{node.lineno} imports {sorted(names & AUTH_NAMES)} from {module}")

    assert not offenders, (
        "Import auth guards from `auth`, not from a router. Importing security "
        "from a sibling router is what made the ImportError fallback in "
        "network.py look reasonable:\n  " + "\n  ".join(offenders)
    )


def test_log_to_task_is_not_imported_from_the_tasks_package():
    offenders = []
    for path, tree in _parsed_sources():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == "tasks/__init__.py":
            continue
        for node, module, names in _imports(tree):
            if module == "tasks" and "log_to_task" in names:
                offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "Import log_to_task from core.task_log. Importing it from `tasks` "
        "drags in Celery and every task module, which is why these were "
        "deferred into function bodies:\n  " + "\n  ".join(offenders)
    )


def test_the_network_router_declares_no_authorization_of_its_own():
    """Its guard belongs to whichever app mounts it.

    The orchestrator wraps it in `require_admin` at include time (main.py);
    the kiosk image mounts the same file with no guard because it is a
    single-user appliance with no accounts. A guard hardcoded in the router
    would have to be conditional on which of the two is running, and the last
    attempt at that condition was a bare `except ImportError`.
    """
    for name in ("network.py", "network_dhcp.py", "network_wg.py"):
        path = BACKEND_ROOT / "routers" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # Checked on the AST rather than the text: the module carries a
        # comment naming require_admin to explain where its guard comes from,
        # and that comment is the documentation, not a violation.
        referenced = {
            getattr(node, "id", None) or getattr(node, "attr", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        assert not (referenced & AUTH_NAMES), (
            f"{name} references a guard {sorted(referenced & AUTH_NAMES)}; the "
            f"app that mounts it decides"
        )

        # And it must not grow its own stand-in either. `except ImportError`
        # alone is fine — the module has a legitimate one for the optional
        # redis client; defining a guard inside one is not.
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("require_")
        }
        assert not defined, f"{name} defines its own guard(s): {sorted(defined)}"
