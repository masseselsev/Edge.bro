"""Locks down which endpoints are reachable without authentication.

Resolved through FastAPI's real dependency tree rather than by reading the
route signatures, because several routers apply their guard once at the
APIRouter level (and propagate it to sub-routers they include). A signature
scan misses those and reports protected routes as open.

The point of the allowlist is that adding an unauthenticated endpoint has to
be a deliberate edit to this file, reviewed on its own terms.
"""
import pytest

from main import app

AUTH_DEPENDENCIES = {
    "require_admin",
    "require_user",
    "require_superadmin",
    "require_admin_plus_or_superadmin",
    "require_kiosk_or_admin",
    "get_current_auth",
    "verify_kiosk",
}

# (method, path) pairs that are unauthenticated on purpose.
INTENTIONALLY_PUBLIC = {
    # Credentials are what this one issues.
    ("POST", "/api/auth/login"),
    # Kiosk enrollment: a kiosk has no credentials until these complete.
    ("POST", "/api/kiosks/handshake"),
    ("POST", "/api/kiosks/enroll"),
    ("POST", "/api/kiosks/auto-handshake"),
    ("POST", "/api/kiosks/request-activation"),
    # Kiosk self-update. Serves the offline client payload; a kiosk fetches
    # these before it has a session. Exposure is the client source, on a
    # trusted network.
    ("GET", "/api/kiosks/payload-hash"),
    ("GET", "/api/kiosks/payload-archive"),
    # A freshly restored node reports back in before it has been re-enrolled.
    ("POST", "/api/nodes/checkin-restored"),
    # Build identifier, used by the frontend to pick kiosk vs orchestrator UI.
    ("GET", "/api/version"),
}


def _auth_guards(route):
    """Every auth dependency reachable from this route, at any depth."""
    found, stack = set(), [route.dependant]
    while stack:
        dep = stack.pop()
        name = getattr(dep.call, "__name__", None)
        if name:
            found.add(name)
        stack.extend(dep.dependencies)
    return found & AUTH_DEPENDENCIES


def _unauthenticated_routes():
    out = set()
    for route in app.routes:
        if not hasattr(route, "dependant"):
            continue
        if _auth_guards(route):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.add((method, route.path))
    return out


def test_no_unexpected_unauthenticated_endpoints():
    unexpected = _unauthenticated_routes() - INTENTIONALLY_PUBLIC
    assert not unexpected, (
        "These endpoints are reachable without authentication and are not in "
        f"the allowlist: {sorted(unexpected)}. If that is deliberate, add them "
        "to INTENTIONALLY_PUBLIC with a comment explaining why."
    )


def test_allowlist_has_no_stale_entries():
    stale = INTENTIONALLY_PUBLIC - _unauthenticated_routes()
    assert not stale, (
        f"These are listed as intentionally public but now carry auth (or no "
        f"longer exist): {sorted(stale)}. Remove them from the allowlist."
    )


@pytest.mark.parametrize("path", [
    "/api/monitoring/preferences",
    "/api/notifications/preferences",
])
def test_preference_routes_require_a_user_not_just_any_principal(path):
    """Kiosk and User ids come from independent sequences and can collide.

    Any route that resolves a User row from the authenticated principal must
    depend on require_user (or require_admin), never bare get_current_auth,
    or a kiosk token can act on an unrelated user's account.
    """
    for route in app.routes:
        if getattr(route, "path", None) == path:
            guards = _auth_guards(route)
            assert "require_user" in guards or "require_admin" in guards, (
                f"{path} guards={sorted(guards)} — needs require_user"
            )


def test_network_routes_are_admin_only_on_the_orchestrator():
    """The router itself declares no auth so the kiosk can mount it bare.

    That makes the orchestrator's guard a property of the mount in main.py,
    which is exactly the kind of thing that gets dropped in a refactor.
    """
    network = [r for r in app.routes
               if hasattr(r, "dependant") and r.path.startswith("/api/network/")]
    assert network, "no /api/network/* routes found — did the mount move?"
    for route in network:
        assert "require_admin" in _auth_guards(route), (
            f"{route.path} is not admin-guarded; check the include_router "
            "dependencies in main.py"
        )
