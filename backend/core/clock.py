"""The one way this codebase asks for the current time.

`datetime.utcnow()` is deprecated as of Python 3.12 and scheduled for removal,
so the ~80 calls to it had to go somewhere. The obvious replacement is the one
the deprecation notice suggests -- and it is the wrong one here.

`datetime.now(timezone.utc)` returns an **aware** datetime. Every timestamp
column in `models.py` is a plain `DateTime`, so everything read back from the
database is **naive**. Mixing the two raises:

    TypeError: can't subtract offset-naive and offset-aware datetimes

and the code is full of `utcnow() - row.created_at`. Swapping the calls one
for one would have converted a deprecation warning into a runtime failure --
mostly inside nightly Celery tasks, where nobody is watching.

So this returns naive UTC: the same value `utcnow()` produced, obtained the
way that is not deprecated. Behaviour is unchanged by design; only the call
that the interpreter objects to is gone.

Moving the columns to `DateTime(timezone=True)` and letting these be aware is
the better end state, but it is a migration plus an audit of every comparison
and every API response, not a rename. `test_no_source_calls_datetime_utcnow`
keeps the direct calls from creeping back in the meantime.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, without a tzinfo -- matching what the database holds."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
