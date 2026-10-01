"""Read-only query helpers over collector runs and source health history.

Used by ``bdo collector-status`` and the Live Situation page's observability panels — never by a
write path.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.models import IngestRun, Source, SourceHealthHistory
from bdo.repository import source_health as health_repo


def recent_collector_runs(session: Session, limit: int = 50) -> list[IngestRun]:
    q = (select(IngestRun).where(IngestRun.mode == "collector")
         .order_by(IngestRun.id.desc()).limit(limit))
    return list(session.scalars(q))


def latest_health_by_source(session: Session) -> dict[str, SourceHealthHistory]:
    """``source_key -> most recent SourceHealthHistory row``, one indexed query."""
    sources = {s.id: s.source_key for s in session.scalars(select(Source))}
    by_source_id = health_repo.latest_for_all_sources(session)
    return {sources[sid]: row for sid, row in by_source_id.items() if sid in sources}
