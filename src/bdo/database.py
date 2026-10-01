"""Engine / session management."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bdo.config import Settings, get_settings
from bdo.enums import RuntimeRole
from bdo.models import Base

_engines: dict[str, Engine] = {}


class CollectorBackendMisconfigured(RuntimeError):
    """Raised by ``guard_collector_backend`` — see its docstring."""


@dataclass(frozen=True)
class BackendIdentity:
    """A credential-safe summary of which database a process is actually talking to — see
    ``guard_collector_backend`` and the "CLI backend identity" section of
    docs/DATABASE_DEPLOYMENT.md. Never carries a password, username, host, or full connection
    string; only the backend *kind* and a coarse locality label.
    """

    backend: str            # "PostgreSQL" | "SQLite" | "<Other>"
    archive_label: str      # "ENABLED" | "LOCAL"
    runtime_role: str       # RuntimeRole.value
    database_target: str    # "remote PostgreSQL" | "local file" | "in-memory" | "remote database"

    def render(self) -> str:
        return (
            f"Backend: {self.backend}\n"
            f"Archive: {self.archive_label}\n"
            f"Runtime role: {self.runtime_role}\n"
            f"Database target: {self.database_target}"
        )


def _backend_name(url: str) -> str:
    scheme = url.split("://", 1)[0].split("+", 1)[0]
    return {"sqlite": "SQLite", "postgresql": "PostgreSQL"}.get(scheme, scheme.capitalize() or "Unknown")


def _database_target(url: str) -> str:
    if url.startswith("sqlite"):
        return "in-memory" if ":memory:" in url else "local file"
    if url.split("://", 1)[0].split("+", 1)[0] == "postgresql":
        return "remote PostgreSQL"
    return "remote database"


def backend_identity(settings: Settings | None = None) -> BackendIdentity:
    """Sanitized identity of ``settings.database_url`` — safe to print in CLI output or logs.

    This exists because an absent/misconfigured ``BDO_DATABASE_URL`` previously let CLI commands
    silently fall through to the local SQLite default, which was once briefly mistaken for the
    production PostgreSQL historian (803 stations / 1389 measurements — a local dev file, not the
    archive). Every operational command prints this before its own output.
    """
    settings = settings or get_settings()
    return BackendIdentity(
        backend=_backend_name(settings.database_url),
        archive_label="ENABLED" if settings.archive_enabled else "LOCAL",
        runtime_role=settings.runtime_role.value,
        database_target=_database_target(settings.database_url),
    )


def guard_collector_backend(settings: Settings) -> None:
    """Fail fast if a COLLECTOR run with the archive flag on would silently write to SQLite.

    A production collector (``BDO_RUNTIME_ROLE=COLLECTOR``, ``BDO_ARCHIVE_ENABLED=true``) that
    resolves to a SQLite ``database_url`` almost certainly means ``BDO_DATABASE_URL`` is absent or
    wrong — the production Postgres archive was intended. Rather than quietly collecting into a
    throwaway local file, this raises immediately. The only way past it is the explicit
    ``BDO_ALLOW_SQLITE_COLLECTOR=true`` development override (``Settings.allow_sqlite_collector``),
    for deliberately testing the collector against SQLite.
    """
    if (
        settings.runtime_role is RuntimeRole.COLLECTOR
        and settings.archive_enabled
        and settings.database_url.startswith("sqlite")
        and not settings.allow_sqlite_collector
    ):
        raise CollectorBackendMisconfigured(
            "BDO_RUNTIME_ROLE=COLLECTOR with BDO_ARCHIVE_ENABLED=true resolved to a SQLite "
            "database — this is almost certainly a missing/incorrect BDO_DATABASE_URL, not an "
            "intentional local run. Set BDO_DATABASE_URL to the production PostgreSQL archive, or "
            "set BDO_ALLOW_SQLITE_COLLECTOR=true to explicitly override for local development."
        )


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
