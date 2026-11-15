"""SQLAlchemy database engine, session factory and base declarative class.

Uses PostgreSQL in Docker (DATABASE_URL env var). The session is provided
as a FastAPI dependency via get_db(). Supports SQLite for local runs/tests.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

def _normalize_url(url: str) -> str:
    """Pin the psycopg2 driver (the one in requirements.txt). SQLAlchemy 2.1 makes
    a bare ``postgresql://`` URL default to psycopg v3, which is not installed."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_url = _normalize_url(settings.database_url)
if _url.startswith("sqlite"):
    # SQLite does not support pool_size/max_overflow
    engine = create_engine(_url, connect_args={"check_same_thread": False}, echo=False)
else:
    engine = create_engine(
        _url,
        pool_pre_ping=True,       # detect stale connections
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    Called on API startup when running locally; in production use Alembic migrations."""
    from . import models  # noqa: F401 – ensures models are registered
    Base.metadata.create_all(bind=engine)
