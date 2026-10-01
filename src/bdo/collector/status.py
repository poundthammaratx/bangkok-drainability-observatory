"""Read-only query helpers over collector runs and source health history.

Used by ``bdo collector-status`` and the Live Situation page's observability panels — never by a
write path.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class SourceStatusComparison:
    """Deliberately separate vocabularies, never collapsed into one value — see
    docs/COLLECTOR_ARCHITECTURE.md's "registry status vs runtime health" section.

    ``registry_status`` is ``bdo.enums.SourceStatus`` — configured/administrative state from
    ``config/sources.yaml`` (``Source.status``), only ever changed by re-seeding or an explicit
    admin action, never by a successful collector run. ``latest_collector_health`` is
    ``bdo.live.base.LiveHealth`` as last observed by the collector (``SourceHealthHistory``) — the
    actual, current, runtime read-through result. A source can be registry ``UNAVAILABLE`` while
    its most recent collector run was ``HEALTHY`` (the registry entry is simply stale), or the
    reverse; presenting only one of the two would hide that distinction.
    """

    source_key: str
    registry_status: str
    latest_collector_health: str | None
    checked_at: object | None  # datetime | None; left loosely typed to avoid importing datetime here


def registry_vs_runtime_health(session: Session) -> list[SourceStatusComparison]:
    """One row per registered source, registry state and runtime health side by side."""
    health_by_source_id = health_repo.latest_for_all_sources(session)
    out: list[SourceStatusComparison] = []
    for src in session.scalars(select(Source).order_by(Source.source_key)):
        row = health_by_source_id.get(src.id)
        out.append(SourceStatusComparison(
            source_key=src.source_key,
            registry_status=src.status.value,
            latest_collector_health=row.health if row else None,
            checked_at=row.checked_at if row else None,
        ))
    return out
