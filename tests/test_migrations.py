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


def test_no_duplicate_constraint_or_index_names_on_postgresql():
    """Regression test for a real Supabase (PostgreSQL 17) deployment failure:

        psycopg.errors.DuplicateObject: check constraint "source_status" already exists

    Root cause: ``bdo.models._enum()`` declares enum columns with
    ``sa.Enum(..., create_constraint=True)``, which already attaches a same-named CHECK
    constraint to the column. Alembic autogenerate additionally emitted an *explicit*
    ``CheckConstraint`` with that same name inside ``op.create_table(...)`` for every enum
    column — two constraints sharing one name in a single ``CREATE TABLE``.

    SQLite does not enforce unique constraint names within a table, so both were created
    silently and the SQLite round-trip test above never caught it. PostgreSQL enforces
    uniqueness (one row per ``(conrelid, conname)`` in ``pg_constraint``) and rejected the
    second declaration outright on first real deployment.

    This renders the *actual* DDL Alembic would send to PostgreSQL (offline/``--sql`` mode —
    no live database or even the ``psycopg`` driver package required, since dialect selection
    for DDL compilation never opens a DBAPI connection) and fails if any table in any revision
    under migrations/versions/ declares two constraints sharing a name, or two indexes share a
    name (index names are schema-wide in PostgreSQL).
    """
    import importlib.util
    from collections import defaultdict
    from unittest.mock import patch

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    versions_dir = ROOT / "migrations" / "versions"
    revision_files = sorted(versions_dir.glob("*.py"))
    assert revision_files, "expected at least one Alembic revision to check"

    table_constraint_names: dict[str, list[str]] = defaultdict(list)
    index_names: list[str] = []

    original_create_table = Operations.create_table
    original_create_index = Operations.create_index

    def recording_create_table(self, table_name, *elements, **kw):
        for element in elements:
            name = getattr(element, "name", None)
            if name:
                table_constraint_names[table_name].append(name)
        return original_create_table(self, table_name, *elements, **kw)

    def recording_create_index(self, index_name, table_name, columns, **kw):
        if index_name:
            index_names.append(index_name)
        return original_create_index(self, index_name, table_name, columns, **kw)

    ctx = MigrationContext.configure(
        connection=None, dialect_name="postgresql", opts={"as_sql": True}
    )

    with patch.object(Operations, "create_table", recording_create_table), \
         patch.object(Operations, "create_index", recording_create_index), \
         Operations.context(ctx):
        for revision_file in revision_files:
            spec = importlib.util.spec_from_file_location(
                f"_migration_check_{revision_file.stem}", revision_file
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()

    duplicates_by_table = {
        table: sorted({n for n in names if names.count(n) > 1})
        for table, names in table_constraint_names.items()
        if len(names) != len(set(names))
    }
    assert not duplicates_by_table, (
        f"duplicate constraint names within a CREATE TABLE (PostgreSQL rejects this): "
        f"{duplicates_by_table}"
    )

    duplicate_indexes = sorted({n for n in index_names if index_names.count(n) > 1})
    assert not duplicate_indexes, f"duplicate index names across migrations: {duplicate_indexes}"


# Alembic's own bookkeeping table is ``alembic_version(version_num VARCHAR(32))`` — this is
# Alembic's hardcoded default (not something bdo's schema defines), so a revision id longer than
# this can never be stamped on *any* deployment using that default, regardless of backend.
_ALEMBIC_VERSION_NUM_LENGTH = 32


def test_revision_ids_fit_alembic_version_column():
    """Regression test for a real failed production deployment:

        psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)

    The revision id ``0002_measurement_source_timestamp_raw`` (37 characters) didn't fit the
    deployed ``alembic_version.version_num`` column (``VARCHAR(32)``, Alembic's own default width).
    PostgreSQL's transactional DDL rolled the failed ``UPDATE alembic_version ...`` back before any
    schema change landed, so the failure was caught cleanly — but the SQLite round-trip test never
    catches it, because SQLite's ``TEXT`` affinity has no length limit to violate. Every revision
    id in the repository must fit, not just the one that already failed once.
    """
    from alembic.script import ScriptDirectory

    cfg = _alembic_config(Path("unused"))
    script_dir = ScriptDirectory.from_config(cfg)
    revisions = list(script_dir.walk_revisions())
    assert revisions, "expected at least one Alembic revision to check"

    oversized = {r.revision: len(r.revision) for r in revisions
                 if len(r.revision) > _ALEMBIC_VERSION_NUM_LENGTH}
    assert not oversized, (
        f"revision id(s) exceed alembic_version.version_num's VARCHAR({_ALEMBIC_VERSION_NUM_LENGTH}): "
        f"{oversized}"
    )


def test_revision_ids_unique_and_graph_resolves_to_one_head():
    """No duplicate revision ids, and the down_revision chain forms one valid, single-headed graph
    (no orphaned branch, no broken parent reference) — ``ScriptDirectory.get_heads()`` raises on a
    broken graph, and returns every independent head otherwise."""
    from alembic.script import ScriptDirectory

    cfg = _alembic_config(Path("unused"))
    script_dir = ScriptDirectory.from_config(cfg)
    revisions = list(script_dir.walk_revisions())

    ids = [r.revision for r in revisions]
    assert len(ids) == len(set(ids)), f"duplicate revision ids: {sorted(ids)}"

    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected exactly one migration head, found: {heads}"
    assert heads[0] == "0002_source_ts_raw"

    head_rev = script_dir.get_revision(heads[0])
    assert head_rev.down_revision == "0001_baseline_schema"
