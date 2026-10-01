"""Alembic migrations (v0.3) — applied against a real (temporary) SQLite file.

Not an in-memory DB: Alembic's offline/online modes and the CLI both need a real path, and this
proves the exact thing a deployer will do — ``alembic upgrade head`` against a fresh database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

alembic = pytest.importorskip("alembic", reason="alembic is a deployment-time dependency; "
                                                 "see docs/DATABASE_DEPLOYMENT.md")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_path: Path) -> Config:
    """``migrations/env.py`` resolves the URL from ``bdo.config.load_settings()`` — the caller sets
    ``BDO_DATABASE_URL`` (via monkeypatch) before invoking any ``alembic.command`` against this."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "migration_test.sqlite"


def test_migration_upgrade_and_downgrade_round_trip(db_path, monkeypatch):
    monkeypatch.setenv("BDO_DATABASE_URL", f"sqlite:///{db_path}")
    cfg = _alembic_config(db_path)

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    expected = {"sources", "stations", "measurements", "raw_snapshots", "source_health_history",
               "ingest_runs", "field_observations", "derived_metrics", "alembic_version"}
    assert expected <= tables
    engine.dispose()

    command.downgrade(cfg, "base")
    engine = create_engine(f"sqlite:///{db_path}")
    tables_after = set(inspect(engine).get_table_names())
    assert tables_after == {"alembic_version"}
    engine.dispose()


def test_baseline_schema_matches_orm_models(db_path, monkeypatch):
    """The migrated schema's raw_snapshots table allows the v0.3 nullable payload_path /
    payload_text columns the collector depends on (see bdo.repository.snapshots.save_snapshot_inline)."""
    monkeypatch.setenv("BDO_DATABASE_URL", f"sqlite:///{db_path}")
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("raw_snapshots")}
    assert cols["payload_path"]["nullable"] is True
    assert "payload_text" in cols
    engine.dispose()
