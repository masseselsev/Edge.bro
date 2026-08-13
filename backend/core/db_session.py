"""Bounded database sessions for work that is mostly not database work.

A Celery task here typically touches the database twice — once to find out
what to do, once to record what happened — with minutes or hours of
subprocess in between. The obvious shape, one session opened at the top of
the task and closed at the bottom, is wrong in two ways at once:

* **The connection is checked out for the whole task.** A pooled connection
  parked behind a `borg create` is a connection no other task can have, and
  it is the connection most likely to be dead by the time it is used again —
  the far end times out idle sessions long before a multi-hour transfer ends.
* **The transaction stays open.** SQLAlchemy opens one on the first query and
  expires every loaded object on commit, so reading `node.hostname` after a
  commit silently issues a refresh SELECT and opens a *new* transaction. An
  `idle in transaction` session holds row locks the whole time it idles; on
  2026-08-12 one of them sat on `settings` for a day and sixteen hours and
  made the database unmigratable. Nothing alerted — it surfaced only because
  someone happened to run an ALTER TABLE.

`session_scope` makes the session's lifetime a block you can see, so the
subprocess work is visibly outside it:

    with session_scope() as db:
        node = db.query(Node).get(node_id)
        host, port = node.ip_address, node.ssh_port

    result = run_the_long_thing(host, port)      # no connection held

    with session_scope() as db:
        db.add(Record(node_id=node_id, outcome=result))

Note what the first block does: it takes *plain values* out, not the ORM
object. Objects do not survive the block — the exit commits, which expires
them, so touching an attribute afterwards raises `DetachedInstanceError`
instead of quietly reconnecting. That is the point. Re-fetch by id in the
second block rather than carrying the instance across.

`tests/test_session_hygiene.py` enforces both halves of this: sessions are
closed in a `finally`, and no subprocess is spawned inside a scope.
"""
from contextlib import contextmanager

import database


@contextmanager
def session_scope():
    """Yield a session, commit on success, roll back on error, always close.

    Committing on the way out even for read-only blocks is deliberate: a
    SELECT opens a transaction too, and leaving it open is the failure this
    module exists to prevent.

    The factory is looked up on the module at call time rather than imported
    by name, so a test that points `database.SessionLocal` at a SQLite session
    redirects every scope in the process.
    """
    db = database.SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
