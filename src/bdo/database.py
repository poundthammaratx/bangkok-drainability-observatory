"""Engine / session management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bdo.config import Settings, get_settings
from bdo.models import Base

_engines: dict[str, Engine] = {}


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url
    if url not in _engines:
        kwargs = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()
        _engines[url] = engine
    return _engines[url]


def init_db(settings: Settings | None = None) -> Engine:
    """Create all tables (idempotent)."""
    settings = settings or get_settings()
    if settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        db_path = settings.database_url[len("sqlite:///"):]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    settings.ensure_dirs()
    return engine


def archive_reachable(settings: Settings | None = None, timeout_seconds: float = 3.0) -> bool:
    """One cheap ``SELECT 1`` — never raises. Used by archive-dependent UI (Live Situation,
    Event Archive) to degrade gracefully instead of crashing when the persistent archive
    (PostgreSQL in production) is temporarily unreachable — milestone §17H / §18. A fresh,
    short-lived connection is used deliberately rather than the pooled engine, so a stuck/stale
    pooled connection cannot report "reachable" when the database has actually gone away.
    """
    import sqlalchemy as sa

    settings = settings or get_settings()
    try:
        engine = sa.create_engine(settings.database_url, connect_args=(
            {"connect_timeout": int(timeout_seconds)} if not settings.database_url.startswith("sqlite") else {}
        ))
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(settings), expire_on_commit=False, future=True)


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    session = session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
