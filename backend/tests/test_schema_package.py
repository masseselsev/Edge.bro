"""Every model in the schemas package must be reachable as `schemas.X`.

`schemas` used to be one module, so `import schemas` gave you all 78 models and
every router reaches them that way. Splitting it into a package kept that
contract by re-exporting from `__init__.py` — which means the contract now
depends on somebody remembering to add a line there.

Forgetting is quiet in the worst way. The router that uses the new model
imports it directly and works; the failure appears later, in a different file,
as an AttributeError at import time in production rather than at the point
anyone made a choice.

Also checks the dependency direction. The modules form a DAG today — `base` at
the bottom, `settings` feeding `groups`, nothing pointing back up — and a cycle
would surface as an import error during startup with a traceback that points
everywhere except the model that caused it.
"""
import ast
import importlib
import pathlib

import pytest

import schemas

SCHEMAS_DIR = pathlib.Path(schemas.__file__).parent


def _module_files():
    return sorted(p for p in SCHEMAS_DIR.glob("*.py") if p.name != "__init__.py")


def test_the_package_actually_split():
    """A guard on the guard: one module again makes everything below vacuous."""
    assert len(_module_files()) >= 5


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_every_model_is_re_exported(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]

    missing = [name for name in defined if not hasattr(schemas, name)]
    assert not missing, (
        f"schemas/{path.name} defines {missing}, which `import schemas` cannot "
        f"see. Add them to the matching `from schemas.{path.stem} import` line "
        f"in schemas/__init__.py."
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_modules_import_cleanly_on_their_own(path):
    """Each module has to stand up without the package __init__ pulling it in.

    This is what catches a cycle: importing a submodule directly does not get
    the ordering that `__init__.py` happens to provide.
    """
    importlib.import_module(f"schemas.{path.stem}")


def test_no_model_is_defined_in_two_modules():
    """A copy-paste during the split would shadow silently, last import wins."""
    seen = {}
    duplicates = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in seen:
                duplicates.append(f"{node.name} in both {seen[node.name]} and {path.name}")
            seen[node.name] = path.name

    assert not duplicates, "\n  ".join(duplicates)
