"""SQLAlchemy database engine, session factory and base declarative class.

Uses PostgreSQL in Docker (DATABASE_URL env var). The session is provided
as a FastAPI dependency via get_db(). Thread-safe; uses NullPool for
compatibility with async lifespan patterns.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

db_url = settings.database_url
if db_url.startswith("postgresql://"):
    try:
        import psycopg_binary  # noqa
    except ImportError:
        try:
            import psycopg2  # noqa
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        except ImportError:
            pass

engine_kwargs = {"echo": False}
if "sqlite" in db_url:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    })

engine = create_engine(db_url, **engine_kwargs)

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
