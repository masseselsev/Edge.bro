"""Central management of OpenSSH authorized_keys files.

Every entry edge-bro writes carries an identifying tag in the key comment
field. That tag is what makes automated cleanup safe: an entry without one
was not written by us, and nothing in this codebase removes it automatically.

All matching is by key fingerprint rather than substring, so a key is still
recognised after its comment changes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

TAG_PREFIX = "edge-bro"
ORCHESTRATOR_TAG = f"{TAG_PREFIX}-orchestrator"
SELFGRANT_TAG = f"{TAG_PREFIX}-orchestrator-selfgrant"

#: The orchestrator's own authorized_keys, shared across containers via the
#: edge-bro_ssh-keys volume.
ORCHESTRATOR_AUTHORIZED_KEYS = "/root/.ssh/authorized_keys"

#: The key the orchestrator authenticates to nodes with. Same volume, and the
#: same identity the backup path already uses — monitoring deliberately adds
#: no second credential to the fleet.
ORCHESTRATOR_PRIVATE_KEY = "/root/.ssh/id_ed25519"

# Must stay byte-identical to the option string already deployed on the fleet.
BORG_SERVE_OPTIONS = (
    'command="borg serve --restrict-to-path /data/borg/fleet",'
    "no-port-forwarding,no-X11-forwarding,no-pty"
)

KEY_TYPES = frozenset({
    "ssh-rsa",
    "ssh-dss",
    "ssh-ed25519",
    "sk-ssh-ed25519@openssh.com",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com",
})


def node_tag(node_id: int) -> str:
    return f"{TAG_PREFIX}-node-{node_id}"


def kiosk_tag(kiosk_id: str) -> str:
    return f"{TAG_PREFIX}-kiosk-{kiosk_id}"


@dataclass(frozen=True)
class AuthorizedKey:
    """One parsed line of an authorized_keys file."""

    options: Optional[str]
    keytype: str
    blob: str
    comment: Optional[str]
    raw: str

    @property
    def fingerprint(self) -> str:
        return _fingerprint_blob(self.blob)

    @property
    def tag(self) -> Optional[str]:
        """The edge-bro tag, if this entry carries one."""
        if self.comment and self.comment.startswith(f"{TAG_PREFIX}-"):
            return self.comment
        return None


def _fingerprint_blob(blob: str) -> str:
    """SHA256 fingerprint in the same format ssh-keygen -lf prints."""
    raw = base64.b64decode(blob, validate=True)
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _split_leading_options(line: str) -> tuple[Optional[str], str]:
    """Separate an options field from the rest of the line.

    The options field is comma-separated and may contain double-quoted values
    holding spaces, as the borg forced command does, so we cannot simply split
    on whitespace.
    """
    first = line.split(None, 1)[0]
    if first in KEY_TYPES:
        return None, line

    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char.isspace() and not in_quotes:
            return line[:index], line[index:].lstrip()
    return None, line


def parse_line(line: str) -> Optional[AuthorizedKey]:
    """Parse one authorized_keys line. Returns None for blanks and comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    options, rest = _split_leading_options(stripped)
    parts = rest.split(None, 2)
    if len(parts) < 2:
        return None

    keytype, blob = parts[0], parts[1]
    if keytype not in KEY_TYPES:
        return None
    try:
        base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return None

    comment = parts[2] if len(parts) > 2 else None
    return AuthorizedKey(
        options=options, keytype=keytype, blob=blob, comment=comment, raw=stripped
    )


def fingerprint(pubkey: str) -> str:
    """Fingerprint a public key string. Raises ValueError if unparseable."""
    entry = parse_line(pubkey)
    if entry is None:
        raise ValueError("not a valid SSH public key")
    return entry.fingerprint


class Action(str, Enum):
    ADDED = "added"
    SKIPPED = "skipped-already-present"
    REWRITTEN = "rewritten"
    REMOVED = "removed"
    NOT_FOUND = "not-found"


def list_entries(path: str) -> list[AuthorizedKey]:
    """Parse every key line in a file. Missing file yields an empty list."""
    if not os.path.exists(path):
        return []
    with open(path, "r") as handle:
        parsed = (parse_line(line) for line in handle)
        return [entry for entry in parsed if entry is not None]


def backup_file(path: str) -> Optional[str]:
    """Copy the file aside before it is modified. Returns the backup path."""
    if not os.path.exists(path):
        return None
    destination = f"{path}.bak.{int(time.time())}"
    shutil.copy2(path, destination)
    return destination


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r") as handle:
        return handle.read().splitlines()


def _write_lines(path: str, lines: list[str]) -> None:
    """Rewrite the file, taking a backup first and restoring 0600."""
    backup_file(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as handle:
        for line in lines:
            handle.write(line.rstrip("\n") + "\n")
    os.chmod(path, 0o600)


def _render(keytype: str, blob: str, options: Optional[str], tag: Optional[str]) -> str:
    parts = [options] if options else []
    parts.extend([keytype, blob])
    if tag:
        parts.append(tag)
    return " ".join(parts)


def authorize(
    path: str,
    pubkey: str,
    options: Optional[str] = None,
    tag: Optional[str] = None,
) -> Action:
    """Ensure `pubkey` is present exactly once, in tagged form.

    Idempotent by fingerprint. An existing entry for the same key that differs
    from the desired form (untagged legacy entries, changed options) is
    rewritten in place rather than duplicated.
    """
    incoming = parse_line(pubkey)
    if incoming is None:
        raise ValueError("not a valid SSH public key")

    desired = _render(incoming.keytype, incoming.blob, options, tag)
    target_fp = incoming.fingerprint

    lines = _read_lines(path)
    output: list[str] = []
    action = Action.ADDED
    seen = False

    for line in lines:
        entry = parse_line(line)
        if entry is not None and entry.fingerprint == target_fp:
            if seen:
                # A duplicate of a key we have already emitted; drop it.
                continue
            seen = True
            if entry.raw == desired:
                action = Action.SKIPPED
            else:
                action = Action.REWRITTEN
            output.append(desired)
            continue
        output.append(line)

    if not seen:
        output.append(desired)

    if action is not Action.SKIPPED:
        _write_lines(path, output)

    logger.info(
        "authorized_keys %s: %s key %s tag=%s", path, action.value, target_fp, tag
    )
    return action


def revoke(path: str, key_or_fingerprint: str) -> Action:
    """Remove every entry matching a public key or a bare SHA256 fingerprint."""
    candidate = key_or_fingerprint.strip()
    if candidate.startswith("SHA256:"):
        target_fp = candidate
    else:
        entry = parse_line(candidate)
        if entry is None:
            raise ValueError("not a valid SSH public key or fingerprint")
        target_fp = entry.fingerprint

    lines = _read_lines(path)
    output = []
    removed = 0
    for line in lines:
        parsed = parse_line(line)
        if parsed is not None and parsed.fingerprint == target_fp:
            removed += 1
            continue
        output.append(line)

    if not removed:
        logger.info(
            "authorized_keys %s: %s for key %s", path, Action.NOT_FOUND.value, target_fp
        )
        return Action.NOT_FOUND

    _write_lines(path, output)
    logger.info(
        "authorized_keys %s: %s %d entrie(s) for key %s",
        path, Action.REMOVED.value, removed, target_fp,
    )
    return Action.REMOVED


class Classification(str, Enum):
    #: Tagged by us and still referenced by a live record. Keep.
    OURS_MATCHED = "OURS_MATCHED"
    #: Tagged by us, no longer referenced. The only class eligible for
    #: automatic deletion.
    OURS_ORPHANED = "OURS_ORPHANED"
    #: Carries the borg forced command but no tag. Almost certainly ours, but
    #: a human could have written it, so automation leaves it alone.
    OURS_LEGACY = "OURS_LEGACY"
    #: Not identifiable as ours. Never deleted by automation.
    UNKNOWN = "UNKNOWN"


def classify(
    entry: AuthorizedKey, known_fingerprints: set[str]
) -> tuple[Classification, str]:
    """Decide what an authorized_keys entry is, and why.

    The tag is the only thing that can make an entry eligible for automatic
    removal. Everything else is reported and left in place.
    """
    tag = entry.tag
    if tag:
        if entry.fingerprint in known_fingerprints:
            return Classification.OURS_MATCHED, f"tagged {tag}, matches a live record"
        return Classification.OURS_ORPHANED, f"tagged {tag}, matches no live record"

    if entry.options and "borg serve --restrict-to-path" in entry.options:
        return (
            Classification.OURS_LEGACY,
            "carries the borg forced command but no tag; "
            "will be re-tagged on next bootstrap",
        )

    return Classification.UNKNOWN, "no edge-bro marker; not written by this system"
