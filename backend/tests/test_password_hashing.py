"""Passwords longer than bcrypt's input limit must not break the login path.

bcrypt reads at most 72 bytes and ignores the rest. Up to 4.x the library did
that silently; 5.0 raises ValueError and tells the caller to truncate itself.
The upgrade arrived on its own, because the backend image installed whatever
was newest at build time, and it turned a long password into two failures at
once: an unhandled 500 when setting one, and — because `verify_password`
swallows every exception and returns False — a silent, unlogged inability to
log in with one that already existed.

These pin the handling so a future bcrypt cannot reintroduce either half.
"""
import bcrypt
import pytest

from auth import BCRYPT_MAX_BYTES, get_password_hash, verify_password


#: Longer than the limit in bytes while staying pure ASCII, so the byte count
#: and the character count are the same and the test is about one thing.
LONG_ASCII = "a" * 200

#: Cyrillic is two bytes per character in UTF-8, so this is 150 bytes of
#: password in 75 characters -- the case where truncating by character count
#: instead of byte count would quietly disagree with bcrypt.
#:
#: Written as an escape so this file stays pure ASCII: test_source_language.py
#: sweeps the tree for non-English text and cannot tell a test fixture from a
#: string someone forgot to translate.
LONG_CYRILLIC = "\u0447" * 75


def test_the_limit_is_the_one_bcrypt_actually_enforces():
    """A guard on the constant: if bcrypt's limit ever moved, the rest of this
    file would still pass while testing the wrong number."""
    with pytest.raises(ValueError):
        bcrypt.hashpw(b"x" * (BCRYPT_MAX_BYTES + 1), bcrypt.gensalt())

    bcrypt.hashpw(b"x" * BCRYPT_MAX_BYTES, bcrypt.gensalt())


@pytest.mark.parametrize("password", [LONG_ASCII, LONG_CYRILLIC], ids=["ascii", "cyrillic"])
def test_a_long_password_can_be_hashed(password):
    """The 500 half. Creating a user or changing a password must not raise."""
    assert get_password_hash(password)


@pytest.mark.parametrize("password", [LONG_ASCII, LONG_CYRILLIC], ids=["ascii", "cyrillic"])
def test_a_long_password_verifies_against_its_own_hash(password):
    """The lockout half, and the one that matters more: hashing and verifying
    have to clip identically or the user is locked out of their own account."""
    assert verify_password(password, get_password_hash(password))


def test_a_wrong_password_is_still_rejected_at_any_length():
    """Truncation must not turn every long password into the same password."""
    hashed = get_password_hash(LONG_ASCII)

    assert not verify_password("b" * 200, hashed)
    assert not verify_password("", hashed)
    assert not verify_password("a", hashed)


def test_passwords_differing_only_past_the_limit_are_not_distinguished():
    """The honest converse, stated so nobody reads the clipping as security.

    bcrypt ignores everything past 72 bytes, so these two really are the same
    password to it. Asserting it here means the behaviour is documented rather
    than discovered.
    """
    base = "a" * BCRYPT_MAX_BYTES

    assert verify_password(base + "-something-else", get_password_hash(base))


def test_a_multibyte_character_is_never_split():
    """Clipping mid-sequence would yield invalid UTF-8, and a truncation point
    that depends on the encoding would not reproduce across calls."""
    hashed = get_password_hash(LONG_CYRILLIC)

    # Verifying twice exercises the clip on the verify path as well as the hash
    # path; an unstable truncation would pass once and fail the second time.
    assert verify_password(LONG_CYRILLIC, hashed)
    assert verify_password(LONG_CYRILLIC, hashed)


def test_a_normal_password_is_untouched():
    """The overwhelmingly common case, so the clipping cannot regress it."""
    assert verify_password("correct horse battery staple",
                           get_password_hash("correct horse battery staple"))
    assert not verify_password("Correct horse battery staple",
                               get_password_hash("correct horse battery staple"))


def test_an_existing_hash_from_before_the_fix_still_verifies():
    """Hashes already in the database were produced by bcrypt's own silent
    truncation, so the clip has to agree with what bcrypt used to do."""
    # What bcrypt <5 stored for LONG_ASCII: it truncated to 72 bytes itself.
    legacy = bcrypt.hashpw(LONG_ASCII.encode()[:BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode()

    assert verify_password(LONG_ASCII, legacy)
