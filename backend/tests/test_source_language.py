"""Source code is English. User-facing strings go through i18n.

Two different rules that look like one:

* **Comments, docstrings and identifiers are English**, everywhere, with no
  exceptions. Someone debugging a provision at 2am should not need a second
  language to read the code.
* **Text a user sees is never written inline.** It lives in
  the `frontend/src/i18n/` dictionaries under a key, so all three languages
  change together and none of them can silently drift.

Enforced as a Cyrillic sweep because that is what the violations looked like:
ru/uk sentences inlined in a ternary, and a 110-line ru/uk progress table
embedded in `ansible_utils.py` that made the backend pick the operator's
language while streaming a playbook log.
"""
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

#: Where source lives. Everything else in the repo — build output, vendored
#: dependencies, the virtualenv — is not ours to police.
SEARCH_ROOTS = ("backend", "frontend/src", "payload_client")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".yml", ".yaml"}
EXCLUDED_PARTS = {"venv", "node_modules", "__pycache__", "dist", "build", ".mypy_cache"}

#: The two places non-English text is correct.
#:
#: The i18n dictionaries are the point of the whole exercise -- one file per
#: language since the bundle split, and en.ts is listed too so a stray
#: untranslated string there is not excused. `LanguageSelector`
#: lists each language's endonym — a picker that said "Russian" in English to
#: someone who cannot read English would be useless.
SANCTIONED = {
    "frontend/src/i18n/en.ts",
    "frontend/src/i18n/ru.ts",
    "frontend/src/i18n/uk.ts",
    "frontend/src/components/LanguageSelector.tsx",
}

# Written as escapes so this file stays pure ASCII and the sweep can
# include itself rather than needing an exemption.
CYRILLIC = re.compile("[\\u0400-\\u04FF]")

#: Skipped where the repository is not checked out whole -- the backend image
#: is built from `./backend` alone, so a run inside the container sees none of
#: the trees below and would fail on having nothing to scan rather than on
#: finding anything wrong.
#:
#: Guarding the module and not the individual tests is deliberate:
#: `test_the_sweep_actually_reads_files` exists to catch a walk that silently
#: matches nothing, so making *it* tolerate an empty result would remove the
#: only thing standing between this file and passing vacuously.
pytestmark = pytest.mark.skipif(
    not all((REPO_ROOT / root).exists() for root in SEARCH_ROOTS),
    reason="repository not checked out whole (running from the backend image?)",
)


def _source_files():
    for root in SEARCH_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            yield path


def test_the_sweep_actually_reads_files():
    """A guard on the guard: a walk that matched nothing would pass silently."""
    files = list(_source_files())
    assert len(files) >= 100, f"expected to scan the codebase, saw {len(files)} files"


def test_the_sweep_would_notice_cyrillic():
    """And a guard on the pattern, since the whole test is one regex."""
    assert CYRILLIC.search("\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0430")
    assert not CYRILLIC.search("Installing dependencies...")


def test_no_source_file_contains_non_english_text():
    offenders = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in SANCTIONED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if CYRILLIC.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()[:90]}")

    assert not offenders, (
        "Non-English text outside the sanctioned files. If it is a comment, "
        "write it in English. If a user reads it, add a key to "
        "a frontend/src/i18n/ dictionary and render it with t():\n  "
        + "\n  ".join(offenders)
    )
