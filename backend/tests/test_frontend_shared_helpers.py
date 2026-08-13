"""Frontend helpers that had drifted into several disagreeing copies.

Checked from the Python suite because the frontend has no test runner. That is
a real limitation — these assert on source text, not behaviour — but the
failure mode they guard against is a copy reappearing, which source text does
catch.

`formatBytes` had seven implementations. Four never clamped the unit index, so
a value past the end of their unit list rendered as `undefined`: the archive
browser's list stopped at GB and showed "1.02 undefined" for a terabyte.
`toLocaleString()` on a bare server timestamp had twelve call sites, each
displaying naive UTC as if it were local time.
"""
import pathlib
import re

import pytest

FRONTEND_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"

pytestmark = pytest.mark.skipif(
    not FRONTEND_SRC.exists(), reason="frontend sources not present"
)


def _tsx_sources():
    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if "node_modules" in path.parts:
            continue
        yield path


def test_the_scan_actually_reads_the_frontend():
    """A guard on the guard."""
    assert len(list(_tsx_sources())) >= 20


def test_byte_formatting_is_not_reimplemented():
    """The tell is the unit table; every copy declared its own."""
    shared = (FRONTEND_SRC / "components" / "formatBytes.ts").resolve()
    pattern = re.compile(r"\[\s*'B(?:/s)?'\s*,")

    offenders = []
    for path in _tsx_sources():
        if path.resolve() == shared:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(FRONTEND_SRC)}:{number}")

    assert not offenders, (
        "These declare their own byte-unit table instead of importing from "
        "components/formatBytes.ts, which is how four of the seven previous "
        "copies came to render 'undefined' past their largest unit:\n  "
        + "\n  ".join(offenders)
    )


def test_server_timestamps_are_not_rendered_raw():
    """`new Date(serverValue).toLocaleString()` reads naive UTC as local time.

    components/dateUtils.ts exists precisely because the API returns naive
    UTC; calling toLocaleString on the raw value shifts every timestamp by the
    viewer's offset without any sign that it happened.
    """
    pattern = re.compile(r"new Date\([^)]*\)\.toLocale(?:String|DateString|TimeString)\(")
    date_utils = (FRONTEND_SRC / "components" / "dateUtils.ts").resolve()

    offenders = []
    for path in _tsx_sources():
        if path.resolve() == date_utils:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(FRONTEND_SRC)}:{number}: {line.strip()[:80]}")

    assert not offenders, (
        "Use parseServerDate/formatDate from components/dateUtils.ts — the "
        "backend returns naive UTC and these render it as local time:\n  "
        + "\n  ".join(offenders)
    )
