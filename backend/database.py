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
    # Avoid duplicate handlers
    for h in root.handlers:
        if isinstance(h, DBLoggingHandler):
            return
    handler = DBLoggingHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)
