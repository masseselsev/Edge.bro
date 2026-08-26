"""Authentication and authorization for the whole API.

This lived in `routers/users.py`, which meant every other router imported its
security guards from a sibling router. That is what produced the worst bug in
the codebase: `routers/network.py` wrapped the import in
`except ImportError: def require_admin(): pass`, so any unrelated import error
anywhere in the user router silently turned authorization off for every VPN
and WiFi write endpoint. Guards belong somewhere nothing else depends on.

The principals are two unrelated model classes. A **User** is an operator with
a password and a JWT. A **Kiosk** is a technician's USB restore stick holding
a static token. They are not related by inheritance and — this is the part
that bites — their primary keys come from independent sequences, so User 7 and
Kiosk 7 both exist. Any route that resolves a row by `auth.id` must therefore
say which kind it wants; `require_kiosk_or_admin` deliberately does not.
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Union

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from sqlalchemy.orm import Session

from database import get_db
import models
from core.clock import utcnow

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 hours

#: bcrypt hashes at most 72 bytes of input and ignores the rest. Up to bcrypt
#: 4.x the library truncated silently; 5.0 raises ValueError instead and tells
#: the caller to truncate deliberately. Raising is the better default — silent
#: truncation means two different long passwords can share a hash — but it has
#: to be handled somewhere, and the only honest place is here, where both
#: hashing and verification can agree on the same rule.
#:
#: Truncating rather than rejecting keeps existing long passwords working. They
#: were already only 72 bytes of security; rejecting them now would lock out
#: whoever set one, with no way to reach the password-change form.
BCRYPT_MAX_BYTES = 72


def _bcrypt_input(password: str) -> bytes:
    """UTF-8 bytes of a password, clipped to what bcrypt actually reads.

    Clipped on a character boundary: cutting mid-sequence would produce invalid
    UTF-8, and — worse — a password whose truncation point shifts with its own
    encoding would not hash to the same value twice.
    """
    encoded = password.encode('utf-8')
    if len(encoded) <= BCRYPT_MAX_BYTES:
        return encoded
    return encoded[:BCRYPT_MAX_BYTES].decode('utf-8', 'ignore').encode('utf-8')


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(_bcrypt_input(password), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(plain_password), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    expire = utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)


# --- Dependency Guards ---

def resolve_auth_token(token: str, db: Session) -> Union[models.User, models.Kiosk]:
    """Everything `get_current_auth` does once it has a token string, in a form
    that does not care whether that string came off a Request or a WebSocket.
    See `get_current_auth`'s docstring for the token-resolution rules this
    implements — this function starts after the token has already been found.
    """
    try:
        # Check if it's a valid JWT admin token
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        user = db.query(models.User).filter(models.User.username == username).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user
    except jwt.PyJWTError:
        # Check if it's an approved kiosk token (simple hex key)
        kiosk = db.query(models.Kiosk).filter(
            models.Kiosk.auth_token == token,
            models.Kiosk.status == "APPROVED"
        ).first()
        if kiosk:
            return kiosk

        # Check if it matches the offline restore token.
        #
        # No file means no offline client has been issued, so nothing
        # authenticates here. This used to fall back to a hardcoded literal,
        # which made that literal a fleet-wide password: it is accepted from a
        # `?token=` query parameter, and the Kiosk it returns can read and
        # download the contents of any node's archives.
        try:
            from iso_tasks import CACHE_DIR, TEMPLATE_BUILD
            token_path = os.path.join(CACHE_DIR, "auth_token.txt")
            if os.path.exists(token_path):
                with open(token_path, "r") as f:
                    expected_token = f.read().strip()
                # Upgrades inherit a poisoned file: every version before this
                # one wrote the template sentinel here on each base-image
                # rebuild, so the word "TEMPLATE" is sitting in it on existing
                # installs and would keep working as a password. Refused
                # outright rather than left to a cleanup step someone has to
                # remember.
                if expected_token.upper() == TEMPLATE_BUILD.upper():
                    expected_token = ""
                # Compared without regard to case because the operator types it
                # off a printed label, and in constant time because this is a
                # bare secret comparison reachable before authentication.
                if expected_token and secrets.compare_digest(
                    token.strip().upper(), expected_token.upper()
                ):
                    return models.Kiosk(name="Offline Restore Client", status="APPROVED", auth_token=token)
        except Exception:
            pass

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session or token")


def get_current_auth(request: Request = None, db: Session = Depends(get_db)) -> Union[models.User, models.Kiosk]:
    """Resolve the caller to a User or a Kiosk, or raise 401.

    Everything else in this file builds on this, so its surprises are worth
    stating outright:

    * **Three token sources**, in order: `Authorization: Bearer`, the
      `admin_session` cookie, then a `?token=` query parameter. The query
      parameter exists for the kiosk's log viewer, which opens URLs in a
      context that cannot set headers — it also means tokens can land in
      access logs and browser history, which is why the orchestrator's own UI
      never uses it.
    * **The `PyJWTError` branch is the normal path, not the error path.** A
      kiosk token is a plain hex string and never parses as a JWT, so failing
      to decode is how we discover we are looking at one. A genuinely
      corrupt admin JWT takes the same branch and falls through to the 401 at
      the bottom.
    * **The offline restore token comes from a file, or not at all.** A
      technician's stick authenticates against the token the orchestrator
      wrote when it built that client ISO. It is compared case-insensitively
      because the operator types it by hand off a label, and in constant time
      because the comparison is reachable before authentication. If no ISO has
      been issued there is no file and nothing authenticates by this route.
    * **The Kiosk it returns in that case is not persisted.** It exists only
      for the duration of the request and has no primary key, so anything
      reading `auth.id` gets None rather than someone else's row.

    Callers must not use the return value to look up a row by id — see the
    module docstring on colliding User and Kiosk keys — unless they went
    through `require_user`, `require_admin` or another guard that pins the
    type.

    See `get_current_auth_ws` for the WebSocket-route equivalent — same rules,
    a different place to find the token.
    """
    token = None
    if request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        else:
            token = request.cookies.get("admin_session")
            if not token:
                token = request.query_params.get("token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return resolve_auth_token(token, db)


def get_current_auth_ws(websocket: WebSocket, db: Session = Depends(get_db)) -> Union[models.User, models.Kiosk]:
    """`get_current_auth`'s WebSocket-route equivalent.

    A `WebSocket` exposes `.headers`, `.cookies` and `.query_params` the same
    way a `Request` does (both are Starlette `HTTPConnection`s), so the same
    three-source precedence applies — in practice this always resolves via
    the `admin_session` cookie, since the browser attaches it automatically
    on a same-origin WS handshake and the orchestrator's own UI never sends a
    `?token=`.

    Raises `WebSocketException`, not `HTTPException`. FastAPI 0.141's
    websocket routes do not translate an `HTTPException` raised from a
    dependency into a handshake rejection at all — the connection hangs
    forever instead, confirmed empirically before writing this. A
    `WebSocketException` is what actually closes the handshake with a
    code the client sees as `WebSocketDisconnect`.
    """
    token = None
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    else:
        token = websocket.cookies.get("admin_session")
        if not token:
            token = websocket.query_params.get("token")

    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")

    try:
        return resolve_auth_token(token, db)
    except HTTPException as exc:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc.detail))


def require_admin_ws(auth = Depends(get_current_auth_ws)) -> models.User:
    """`require_admin`'s WebSocket-route equivalent. See `get_current_auth_ws`
    for why this raises `WebSocketException` rather than `HTTPException`."""
    if not isinstance(auth, models.User):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Administrator permissions required",
        )
    return auth


def require_admin(auth = Depends(get_current_auth)) -> models.User:
    if not isinstance(auth, models.User):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator permissions required"
        )
    return auth


def require_user(auth = Depends(get_current_auth)) -> models.User:
    """Like require_admin, but named for routes whose only requirement is
    "acting principal is a User row", not admin-specific permissions — e.g.
    a caller's own notification preferences. Kiosk and User primary keys are
    independent sequences and can collide, so any route that resolves a User
    row by `current_auth.id` must depend on this (or require_admin) rather
    than bare get_current_auth, or a kiosk token can act on an unrelated
    user's account.
    """
    if not isinstance(auth, models.User):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account required"
        )
    return auth


def require_superadmin(auth = Depends(get_current_auth)) -> models.User:
    if not isinstance(auth, models.User) or not auth.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-administrator permissions required"
        )
    return auth


def require_admin_plus_or_superadmin(auth = Depends(get_current_auth)) -> models.User:
    if not isinstance(auth, models.User) or (not auth.is_superadmin and not auth.is_admin_plus):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin+ or Super-administrator permissions required"
        )
    return auth


def require_kiosk_or_admin(auth = Depends(get_current_auth)) -> Union[models.User, models.Kiosk]:
    """Any authenticated principal, of either kind.

    The bare `return auth` is the whole implementation and is deliberate: this
    is for read endpoints a technician's restore stick legitimately needs
    (node lists, task logs, archive contents) as much as an operator does.

    Because it can return either kind, a route depending on this must never
    resolve a row by `auth.id`. User and Kiosk ids come from independent
    sequences and collide. Use `require_user` where the caller's own account
    is the subject.
    """
    return auth
