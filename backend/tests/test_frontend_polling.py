"""App.tsx must not grow its own polling loops again.

It had seven: bandwidth every 3s, watchdog every 4s, network status every 7s,
kiosk status every 8s, health and pending kiosks every 10s, orchestrator
reachability every 15s. Each was a separate hand-written copy of fetch ->
check res.ok -> parse -> setState -> setInterval -> clearInterval, and they had
drifted apart. Some fetched immediately on mount and some waited a full
interval; some logged failures and some swallowed them; none guarded against a
response arriving after the component had stopped caring, so a slow reply could
overwrite a newer one.

`hooks/usePolledResource.ts` is now the only implementation. This is checked
from the Python suite because the frontend has no test runner — see
test_frontend_shared_helpers.py for the same limitation and the same reasoning:
these assert on source text, and a copy reappearing is exactly the kind of
regression source text catches.

Scoped to App.tsx deliberately. Other components still hand-roll polling and
converting them is separate work; a repo-wide ban asserted here would either
fail today or have to carry an allowlist that nobody maintains.
"""
import pathlib
import re

import pytest

FRONTEND_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
APP = FRONTEND_SRC / "App.tsx"
HOOK = FRONTEND_SRC / "hooks" / "usePolledResource.ts"

pytestmark = pytest.mark.skipif(
    not FRONTEND_SRC.exists(), reason="frontend sources not present"
)


def test_the_shared_hook_exists():
    """A guard on the guard: the rest is vacuous if the hook was deleted."""
    assert HOOK.is_file(), f"{HOOK} is missing; App.tsx has nothing to poll with"


def test_app_does_not_hand_roll_polling():
    source = APP.read_text(encoding="utf-8")

    offenders = [
        f"App.tsx:{number}"
        for number, line in enumerate(source.splitlines(), 1)
        if "setInterval" in line
    ]

    assert not offenders, (
        "App.tsx is polling by hand again. Use usePolledResource(url, ms) — "
        "it handles the immediate fetch, the cleanup, and dropping responses "
        "that arrive after the component moved on:\n  " + "\n  ".join(offenders)
    )


def test_app_polls_through_the_hook():
    """The inverse of the check above: proves the loops were moved, not deleted."""
    source = APP.read_text(encoding="utf-8")

    assert "usePolledResource" in source, "App.tsx no longer polls anything at all"

    # Each call names its endpoint and its interval as the first two arguments.
    calls = re.findall(r"usePolledResource[^(]*\(\s*'([^']+)'\s*,\s*(\d+)", source)
    endpoints = {url for url, _ in calls}

    # The three App owns directly. Kiosk status and pending kiosks moved into
    # useKioskPairing and usePendingKiosks respectively, so they are not here.
    for expected in ("/api/network/bandwidth", "/api/health", "/api/network/status"):
        assert expected in endpoints, (
            f"{expected} is no longer polled from App.tsx; if that is "
            f"deliberate, update this list, and if it is not, the panel that "
            f"reads it has silently stopped refreshing."
        )


def test_polling_intervals_are_not_accidentally_aggressive():
    """A stray zero turns a 3s poll into 300ms and nobody notices locally.

    At 2000 nodes the orchestrator is the busy component, and these run in
    every open browser tab.
    """
    for path in (APP, FRONTEND_SRC / "hooks" / "useKioskPairing.ts", FRONTEND_SRC / "hooks" / "usePendingKiosks.ts"):
        source = path.read_text(encoding="utf-8")
        for url, interval in re.findall(r"usePolledResource[^(]*\(\s*'([^']+)'\s*,\s*(\d+)", source):
            assert int(interval) >= 1000, (
                f"{path.name} polls {url} every {interval}ms"
            )
