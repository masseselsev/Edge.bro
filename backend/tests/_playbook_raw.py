"""Extracting one `raw:` shell block from an Ansible playbook for testing.

Ansible's `raw:` tasks run on the node itself, often before Python is even
installed there, so they can't be unit tested as Python. This loads the exact
text Ansible would run (Jinja is not templated at YAML-parse time, so PyYAML
hands back the `{{ ... }}` tokens untouched) and substitutes them the same way
Ansible's templating would, so the test exercises the real shell logic rather
than a hand-copied approximation of it.
"""
import os
import re

import yaml

PLAYBOOKS_DIR = os.path.join(os.path.dirname(__file__), "..", "playbooks")


def load_raw_task(playbook_name: str, task_name: str) -> str:
    path = os.path.join(PLAYBOOKS_DIR, playbook_name)
    with open(path) as f:
        plays = yaml.safe_load(f)
    for play in plays:
        for task in play.get("tasks", []):
            if task.get("name") == task_name:
                return task["raw"]
    raise AssertionError(f"task {task_name!r} not found in {playbook_name}")


def render(template: str, **values) -> str:
    """Simple `{{ var }}` substitution — every token this codebase's
    playbooks actually use is a bare variable or a `| default(...)` filter,
    never full Jinja logic, so this does not need a real Jinja engine."""
    out = template
    for key, value in values.items():
        out = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", str(value), out)
        out = re.sub(
            r"\{\{\s*" + re.escape(key) + r"\s*\|\s*default\([^)]*\)\s*\}\}",
            str(value),
            out,
        )
    return out
