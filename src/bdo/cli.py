"""Command-line interface (Typer). Also exposed as ``bdo`` after ``pip install -e .``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from bdo.config import load_settings
from bdo.database import init_db, session_scope
from bdo.util.logging import configure_logging

app = typer.Typer(add_completion=False, help="Bangkok Drainability Observatory — research data CLI")


def _settings():
    s = load_settings()
    configure_logging(s.log_level, s.log_file)
    return s


@app.command("init-db")
def init_db_cmd():
    """Create the SQLite schema and data directories (idempotent)."""
    s = _settings()
    init_db(s)
    typer.echo(f"database ready: {s.database_url}")
    typer.echo(f"data directory: {s.data_dir}")


@app.command("seed")
def seed_cmd(demo: bool = typer.Option(True, "--demo/--no-demo", help="also load SEED_* demonstration records")):
    """Register sources from config/sources.yaml and (by default) load the SEED demonstration records."""
    from bdo.ingestion.runner import run_source
    from bdo.repository import sources as source_repo

    s = _settings()
    init_db(s)
    with session_scope(s) as session:
        for cfg in s.sources:
            source_repo.upsert_from_config(session, cfg)
    typer.echo(f"registered {len(s.sources)} source(s)")
    if not demo:
        return
    for cfg in s.sources:
        seed_files = sorted((s.manual_dir / cfg.source_key).glob("SEED_*.csv"))
        if seed_files:
            summary = run_source(s, cfg.source_key, manual=True, paths=seed_files, mode="seed")
            typer.echo(summary.line())
            for e in summary.errors[:10]:
                typer.echo(f"   ! {e}")


@app.command("ingest")
def ingest_cmd(
    source: Optional[str] = typer.Option(None, "--source", "-s", help="source_key from sources.yaml"),
    all_: bool = typer.Option(False, "--all", help="run every source with enabled: true"),
    manual: bool = typer.Option(False, "--manual", help="ingest data/manual/<source>/*.csv instead of the automatic adapter"),
    file: list[Path] = typer.Option(None, "--file", "-f", help="explicit manual CSV file(s); implies --manual"),
):
    """Run the ingestion pipeline (healthcheck → fetch → archive → normalize → dedup → insert)."""
    from bdo.ingestion.runner import run_all, run_source
    from bdo.util.access import assert_writes_allowed

    s = _settings()
    assert_writes_allowed(s, "ingestion")
    init_db(s)
    if all_ == bool(source):
        raise typer.BadParameter("give exactly one of --source or --all")
    if all_:
        summaries = run_all(s)
        if not summaries:
            typer.echo("no sources have enabled: true")
    else:
        summaries = [run_source(s, source, manual=manual or bool(file), paths=list(file) if file else None)]
    for sm in summaries:
        typer.echo(sm.line())
        for e in sm.errors[:10]:
            typer.echo(f"   ! {e}")
        if len(sm.errors) > 10:
            typer.echo(f"   ! … {len(sm.errors) - 10} more in data/processed/ingest_errors/run_{sm.run_id}.txt")


@app.command("discover")
def discover_cmd(source: str = typer.Argument("bkk_open_data_dds"), query: Optional[str] = typer.Option(None, "--q")):
    """CKAN package_search: list datasets and resource IDs (to add to sources.yaml). Read-only."""
    from bdo.ingestion.adapters.bkk_ckan import CKANAdapter

    s = _settings()
    cfg = s.source(source)
    ad = CKANAdapter(cfg, s)
    q = query or (cfg.ckan or {}).get("discovery_query", "")
    res = ad.package_search(q, rows=100)
    typer.echo(f"{res.get('count')} dataset(s) for q={q!r} via {ad.active_base}")
    for pkg in res.get("results", []):
        typer.echo(f"- {pkg.get('name')}: {pkg.get('title')}")
        for r in pkg.get("resources", []):
            typer.echo(f"    resource_id={r.get('id')} format={r.get('format')} datastore={r.get('datastore_active')} "
                       f"name={r.get('name')}")
    ad.close()


@app.command("import-observations")
def import_observations_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    strict: bool = typer.Option(False, "--strict", help="insert nothing if any row is invalid"),
):
    """Validate and import a field-observation CSV (see data/manual/field_observations_TEMPLATE.csv)."""
    from bdo.repository.observations import import_csv

    s = _settings()
    init_db(s)
    with session_scope(s) as session:
        from bdo.repository import sources as source_repo
        for cfg in s.sources:
            if cfg.source_key == "research_field_observations":
                source_repo.upsert_from_config(session, cfg)
    with session_scope(s) as session:
        rep = import_csv(session, s, csv_path, strict=strict)
    typer.echo(f"file: {rep.file}")
    typer.echo(f"rows={rep.rows_total} inserted={rep.inserted} duplicates={rep.skipped_duplicate} rejected={rep.rejected} "
               f"raw_snapshot_id={rep.raw_snapshot_id}")
    for w in rep.warnings:
        typer.echo(f"   warning: {w}")
    for e in rep.errors[:20]:
        typer.echo(f"   ! row {e['row']} ({e['observation_id']}): {e['error']}")
    if rep.error_report_path:
        typer.echo(f"error report: {rep.error_report_path}")
    if not rep.ok:
        raise typer.Exit(code=1)


@app.command("status")
def status_cmd():
    """Print source registry status and table counts."""
    from sqlalchemy import func, select

    from bdo.models import FieldObservation, IngestRun, Measurement, RawSnapshot, Source, Station

    s = _settings()
    init_db(s)
    with session_scope(s) as session:
        counts = {m.__tablename__: session.scalar(select(func.count()).select_from(m))
                  for m in (Source, Station, Measurement, RawSnapshot, FieldObservation, IngestRun)}
        typer.echo(json.dumps(counts, indent=2))
        for src in session.scalars(select(Source).order_by(Source.id)):
            typer.echo(f"{src.source_key:28s} {src.status.value:12s} {src.last_health_message or ''}"[:160])


if __name__ == "__main__":
    app()
