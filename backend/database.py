import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:securepassword@localhost:5432/borg_orchestrator")

# Pool sizing has to account for how many processes exist, not just how busy
# one of them is. Celery's prefork model gives every worker child its own pool
# after the fork, so the ceiling is
#   (API workers + backup children + periodic children) x (pool_size + overflow)
# against Postgres's max_connections, which defaults to 100.
#
# Each Celery child runs one task at a time (prefetch is 1 and the children are
# single-threaded), so a worker child needs one or two connections, not ten —
# a large per-process pool here buys nothing and risks exhausting the server.
# The API process is the one that benefits from a pool, and it is a single
# process. So: modest pool, generous overflow for the API's concurrency.
#
# pool_pre_ping matters more than any of the sizes. Without it a connection
# that died while idle — a database restart, a network blip, an idle timeout
# on a pooler — is handed out and fails on first use, and tasks here hold
# sessions across multi-minute ansible runs and multi-hour borg transfers,
# which is exactly when connections go stale.
#
# SQLite, used by the test suite, takes none of these arguments.
_engine_kwargs = {}
if not DATABASE_URL.startswith("sqlite"):
    _engine_kwargs = {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
        "pool_pre_ping": True,
        # Recycle below any typical server-side idle timeout so we retire
        # connections before the far end does.
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "1800")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
    }

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """
    Dependency to obtain the SQLAlchemy database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


import logging

class DBLoggingHandler(logging.Handler):
    """Mirrors log records into the system_logs table for the Logs tab.

    Writing logs to the database being logged is inherently circular, and the
    three defenses below are three different ways that circle closes. They
    overlap on purpose — each one alone has a hole:

    1. **The `_logged_to_db` stamp** stops one record being written twice when
       the handler is attached to both a logger and its parent, which it is:
       `setup_db_logging` attaches to the root *and* to `uvicorn`, `celery` and
       friends, so a celery record would otherwise arrive here twice.
    2. **The logger-name prefixes** stop the loop proper. Writing a row makes
       SQLAlchemy log the INSERT, which arrives here, which writes a row. The
       stamp does not help: that is a genuinely new record each time. urllib3
       and redis are here for the same reason via the broker.
    3. **The message-text check** is the backstop for a record that reaches
       here under some other logger name — a wrapper, a library that
       re-emits, an application log line that happens to quote the statement.
       Cruder than matching on the logger, and last for that reason.

    A missing defense does not degrade gracefully. It produces an unbounded
    recursion that fills the disk with log rows describing the writing of log
    rows.
    """

    def emit(self, record):
        if getattr(record, "_logged_to_db", False):
            return
        try:
            record._logged_to_db = True
        except Exception:
            # Some records are not writable (a proxy, a frozen instance). One
            # duplicate row is better than losing the record entirely.
            pass

        name = record.name.lower()
        # Prevent infinite logging loop on SQL queries
        if (
            name.startswith("sqlalchemy") or
            name.startswith("urllib3") or
            name.startswith("redis") or
            "insert into system_logs" in record.getMessage().lower()
        ):
            return
        
        # The close must sit in a finally. It used to follow the commit inside
        # the try, so any failure to write the log — which is precisely when
        # the database is unhappy — left the session open forever with a
        # transaction attached. A leaking log handler leaks hardest exactly
        # when things are already going wrong.
        db = None
        try:
            db = SessionLocal()
            from models import SystemLog
            log_entry = SystemLog(
                level=record.levelname,
                message=self.format(record)
            )
            db.add(log_entry)
            db.commit()
        except Exception:
            pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass


def setup_db_logging():
    root = logging.getLogger()
    
    # We want a single handler instance to be shared
    handler = None
    for h in root.handlers:
        if isinstance(h, DBLoggingHandler):
            handler = h
            break
            
    if handler is None:
        handler = DBLoggingHandler()
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Attach handler to specific loggers to ensure their logs are captured.
    #
    # `uvicorn.access` is deliberately absent. Every HTTP request emits an
    # access line, and this handler turns each record into its own session,
    # INSERT and COMMIT — so with the UI polling several endpoints on timers,
    # a single open browser was writing hundreds of rows a minute to
    # system_logs purely to record that it had polled. Request logging belongs
    # in the container's stdout, which already has it.
    loggers_to_attach = [
        "uvicorn",
        "uvicorn.error",
        "fastapi",
        "celery",
        "celery.task",
        "celery.worker"
    ]
    for name in loggers_to_attach:
        l = logging.getLogger(name)
        if not any(isinstance(h, DBLoggingHandler) for h in l.handlers):
            l.addHandler(handler)


def log_user_action(db, username: str, action: str, details: str = None, request = None):
    """
    Records a user action to the audit_logs database table.
    """
    ip_address = None
    if request and hasattr(request, "client") and request.client:
        host = request.client.host
        if isinstance(host, str):
            ip_address = host
    try:
        from models import AuditLog
        log_entry = AuditLog(
            username=username,
            action=action,
            details=details,
            ip_address=ip_address
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        import sys
        print(f"Failed to log user action: {e}", file=sys.stderr)


