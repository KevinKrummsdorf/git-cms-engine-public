"""SQLAlchemy engine and session bootstrap service for the git-cms-engine module.

Handles database engine initialization, connection pooling, and request-scoped
session lifecycle management.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings

# Initialize database engine with connection validation checks
engine = create_engine(settings.cms_database_url, pool_pre_ping=True, future=True)

# Define session factory
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """Provides a database session context manager for individual requests.

    Yields:
        Generator[Session, None, None]: The request-scoped SQLAlchemy Session instance.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["engine", "SessionLocal", "get_db"]
