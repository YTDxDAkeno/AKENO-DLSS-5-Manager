"""Database engine and sessions. PostgreSQL in production; SQLite for development and the quick test run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from akeno_server.config import get_config

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite(conn, _record):
            conn.isolation_level = None          # transactions are begun below, not by the sqlite3 module
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):
            # One writer at a time from the first statement on: SQLite then behaves like PostgreSQL's row locks
            # for redemption races (development and tests; production uses PostgreSQL).
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return engine
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        _engine = make_engine(get_config().database_url)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def use_engine(new: Engine) -> None:
    """Tests: run everything against this engine."""
    global _engine, _factory
    _engine = new
    _factory = sessionmaker(new, expire_on_commit=False)


def session() -> Session:
    engine()
    assert _factory is not None
    return _factory()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back if the request fails."""
    db = session()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def transaction() -> Iterator[Session]:
    db = session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"
