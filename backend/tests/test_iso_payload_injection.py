"""The kiosk ISO ships a hand-picked subset of the backend. Keep it closed.

`iso_tasks.build_client_iso_task` copies a fixed list of files from `core/` and
`routers/` into the offline payload. If one of those files starts importing a
sibling that is not on the list, the copy is incomplete — and nothing says so.
The payload client wraps its imports in a bare `except`, so the kiosk boots
normally and one feature is simply absent.

That has already happened once: `network.py` was split into `network_dhcp.py`
and `network_wg.py`, the list was not updated, and every `/api/network/*` route
on the kiosk 404'd until someone noticed. The failure surfaces in front of a
customer's dead server, with a technician holding a USB stick, which is the
worst possible place to discover it.

So the closure is checked here instead: walk the module-scope imports of every
injected file, and fail if any of them names a first-party module that is not
itself injected.
"""
import ast
import os

import pytest

import iso_tasks

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: package name -> (source directory, the tuple in iso_tasks listing what ships)
INJECTED = {
    "core": ("core", iso_tasks.INJECTED_CORE_MODULES),
    "routers": ("routers", iso_tasks.INJECTED_ROUTER_MODULES),
}


def _module_scope_imports(path):
    """Every `import x` / `from x import y` at module scope, as dotted strings.

    Module scope only. A function-local import inside a code path the kiosk
    never reaches is not a shipping requirement, and treating it as one would
    drag half the backend into the ISO.
    """
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import inside a package we ship flat; resolve it as
                # a sibling of the importing module.
                continue
            if node.module:
                # `from core import guest_config` names the submodule in the
                # alias list, not in node.module.
                names.append(node.module)
                names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _injected_files():
    for package, (directory, filenames) in INJECTED.items():
        for filename in filenames:
            yield package, filename, os.path.join(BACKEND_DIR, directory, filename)


def test_every_injected_file_exists():
    """A typo in the list is a silently missing file, not a build error."""
    for _package, filename, path in _injected_files():
        assert os.path.isfile(path), f"{path} is listed for ISO injection but does not exist"


@pytest.mark.parametrize(
    "package,filename",
    [(p, f) for p, f, _ in _injected_files()],
    ids=[f"{p}/{f}" for p, f, _ in _injected_files()],
)
def test_injected_modules_only_import_other_injected_modules(package, filename):
    path = os.path.join(BACKEND_DIR, INJECTED[package][0], filename)

    for imported in _module_scope_imports(path):
        head = imported.split(".")[0]
        if head not in INJECTED:
            # Standard library, third-party, or a flat backend module like
            # `database` that the payload client provides itself.
            continue

        parts = imported.split(".")
        if len(parts) < 2:
            # Bare `import core` — imports no submodule, ships nothing.
            continue

        submodule = f"{parts[1]}.py"
        shipped = INJECTED[head][1]
        assert submodule in shipped, (
            f"{package}/{filename} imports {imported} at module scope, but "
            f"{head}/{submodule} is not in iso_tasks.INJECTED_"
            f"{head.upper()}_MODULES. The kiosk would ship a broken payload "
            f"and swallow the ImportError."
        )


def test_the_injection_loop_uses_the_declared_lists():
    """Guard the guard: a literal path in build_client_iso_task bypasses all this.

    The check above is worth nothing if someone adds `shutil.copy2` with a
    hardcoded `/app/core/something.py` next to the loop.
    """
    with open(os.path.join(BACKEND_DIR, "iso_tasks.py"), "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    hardcoded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        # A complete path only. The f-strings in the injection loop contribute
        # the bare prefix "/app/core/", which is the correct way to do it.
        if value.endswith(".py") and (
            value.startswith("/app/core/") or value.startswith("/app/routers/")
        ):
            hardcoded.append(value)

    assert hardcoded == [], (
        f"iso_tasks.py copies {hardcoded} by literal path. Add the file to "
        f"INJECTED_CORE_MODULES / INJECTED_ROUTER_MODULES instead, so the "
        f"import-closure check above can see it."
    )
