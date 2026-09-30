"""Startup bootstrap for a fresh, empty database (Streamlit Community Cloud has no persistent
runtime filesystem: every deploy/reboot can start from an empty SQLite file).

Registers the configured sources and loads the packaged, repository-tracked SEED_*.csv
demonstration records so the app is usable immediately, without shell access to run
scripts/seed_sources.py. Runs only once against an empty database (checked by source count),
never fetches from the network, and is safe to call under PUBLIC_DEPLOYMENT=true — it loads
static baseline data, not externally-submitted input.

TODO (post-alpha): move durable archival to external PostgreSQL/PostGIS; do not rely on the
Streamlit Cloud runtime filesystem for anything that must persist.
"""

from __future__ import annotations

from sqlalchemy import func, select

from bdo.config import Settings
from bdo.database import init_db, session_scope
from bdo.models import Source
from bdo.repository import sources as source_repo


def ensure_seeded(settings: Settings) -> None:
    init_db(settings)
    with session_scope(settings) as session:
        if session.scalar(select(func.count()).select_from(Source)):
            return  # already registered in a prior run against this database file
        for cfg in settings.sources:
            source_repo.upsert_from_config(session, cfg)

    from bdo.ingestion.runner import run_source

    for cfg in settings.sources:
        seed_files = sorted((settings.manual_dir / cfg.source_key).glob("SEED_*.csv"))
        if seed_files:
            run_source(settings, cfg.source_key, manual=True, paths=seed_files, mode="seed")
