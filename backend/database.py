import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:securepassword@localhost:5432/borg_orchestrator")

engine = create_engine(DATABASE_URL)
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
    def emit(self, record):
        if getattr(record, "_logged_to_db", False):
            return
        try:
            record._logged_to_db = True
        except Exception:
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
        
        try:
            db = SessionLocal()
            from models import SystemLog
            log_entry = SystemLog(
                level=record.levelname,
                message=self.format(record)
            )
            db.add(log_entry)
            db.commit()
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

    # Attach handler to specific loggers to ensure their logs are captured
    loggers_to_attach = [
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
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


