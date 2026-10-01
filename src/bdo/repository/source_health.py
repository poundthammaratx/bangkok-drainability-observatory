"""Source health history (v0.3) — persisted per collector run, never from the Streamlit UI.

``Source.last_health_message`` / ``last_healthcheck_at`` (v0.1/v0.2) only ever hold the latest
result. This module makes availability itself a historical, queryable record.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.live.base import LiveSourceState
from bdo.models import Source, SourceHealthHistory


def record(
    session: Session,
    source: Source,
    state: LiveSourceState,
    *,
    collector_run_id: int | None = None,
    latency_ms: float | None = None,
) -> SourceHealthHistory:
    row = SourceHealthHistory(
        source_id=source.id,
        collector_run_id=collector_run_id,
        checked_at=state.fetch_finished_at or state.fetch_started_at,
        health=state.health.value,
        http_status=state.http_status,
        latest_measurement_at=state.source_measurement_at,
        freshness_label=state.freshness.value,
        record_count=state.record_count,
        latency_ms=latency_ms,
        error=state.error,
        endpoint=state.endpoint,
    )
    session.add(row)
    session.flush()
    return row


def history_for(
    session: Session, source_id: int, *, start: datetime | None = None, end: datetime | None = None,
    limit: int = 500,
) -> list[SourceHealthHistory]:
    q = select(SourceHealthHistory).where(SourceHealthHistory.source_id == source_id)
    if start is not None:
        q = q.where(SourceHealthHistory.checked_at >= start)
    if end is not None:
        q = q.where(SourceHealthHistory.checked_at <= end)
    q = q.order_by(SourceHealthHistory.checked_at.desc()).limit(limit)
    return list(session.scalars(q))


def latest_for_all_sources(session: Session) -> dict[int, SourceHealthHistory]:
    """Most recent health row per source_id — one indexed query, not a full table scan."""
    from sqlalchemy import func

    sub = (
        select(
            SourceHealthHistory.source_id,
            func.max(SourceHealthHistory.checked_at).label("max_checked_at"),
        )
        .group_by(SourceHealthHistory.source_id)
        .subquery()
    )
    rows = session.scalars(
        select(SourceHealthHistory).join(
            sub,
            (SourceHealthHistory.source_id == sub.c.source_id)
            & (SourceHealthHistory.checked_at == sub.c.max_checked_at),
        )
    )
    out: dict[int, SourceHealthHistory] = {}
    for row in rows:
        out[row.source_id] = row  # ties (same checked_at) keep the last one seen; rare and harmless
    return out
