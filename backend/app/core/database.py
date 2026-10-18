from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Create database engine
_is_sqlite = settings.SQLALCHEMY_DATABASE_URI.startswith("sqlite")
engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)

# Session factory for DB transactions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declarative base class for SQLAlchemy models
Base = declarative_base()

def get_db() -> Generator:
    """
    Dependency generator for database sessions.
    Yields a session and ensures it is closed after request lifecycle.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
