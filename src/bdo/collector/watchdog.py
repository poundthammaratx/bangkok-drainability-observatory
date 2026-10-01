"""Collector watchdog — data-acquisition health only, never hydraulic condition.

``bdo watchdog`` answers one question per source: *is this source's collector still actually
running, recently enough to trust?* It is independent of ``LiveHealth`` (which judges one fetch's
own HTTP/data freshness) and independent of ``Source.status`` (the configured registry state) —
see docs/COLLECTOR_ARCHITECTURE.md's "registry status vs runtime health" section. This module only
asks whether *collection itself* is happening on schedule; it never infers anything about flooding,
water level, or any other hydraulic condition from a stale or missing run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.enums import IngestStatus
from bdo.models import IngestRun, Source
from bdo.repository import source_health as health_repo
from bdo.util.time import utcnow

HEALTHY = "HEALTHY"
STALE = "STALE"
NEVER_RUN = "NEVER_RUN"
FAILED = "FAILED"


@dataclass(frozen=True)
class WatchdogReport:
    source_key: str
    status: str  # HEALTHY | STALE | NEVER_RUN | FAILED
    threshold_minutes: float
    last_success_at: datetime | None = None
    age_minutes: float | None = None
    latest_health: str | None = None
    latest_measurement_at: datetime | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == HEALTHY

    def line(self) -> str:
        parts = [f"WATCHDOG {self.status}", f"source={self.source_key}"]
        parts.append(f"last_success={self.last_success_at.isoformat() if self.last_success_at else 'none'}")
        parts.append(f"age_minutes={self.age_minutes:.1f}" if self.age_minutes is not None else "age_minutes=none")
        parts.append(f"threshold_minutes={self.threshold_minutes:g}")
        if self.latest_health is not None:
            parts.append(f"latest_health={self.latest_health}")
        if self.latest_measurement_at is not None:
            parts.append(f"latest_measurement_at={self.latest_measurement_at.isoformat()}")
        if self.message:
            parts.append(f"message={self.message}")
        return "\n".join(parts)


def check_source(session: Session, source_key: str, *, max_age_minutes: float = 30.0) -> WatchdogReport:
    """Inspect persisted collector history for one source. Never raises for "no history yet" —
    that is ``NEVER_RUN``, a normal (if actionable) result, not an error. Raises ``KeyError`` only
    if ``source_key`` is not a registered source at all (a genuine misconfiguration)."""
    source = session.scalar(select(Source).where(Source.source_key == source_key))
    if source is None:
        raise KeyError(f"source '{source_key}' not in the registry")

    runs = list(session.scalars(
        select(IngestRun)
        .where(IngestRun.source_id == source.id, IngestRun.mode == "collector")
        .order_by(IngestRun.started_at.desc())
        .limit(200)
    ))
    last_success = next((r for r in runs if r.status is IngestStatus.SUCCESS), None)
    last_run = runs[0] if runs else None

    latest_health_row = health_repo.latest_for_all_sources(session).get(source.id)
    latest_health = latest_health_row.health if latest_health_row else None
    latest_measurement_at = latest_health_row.latest_measurement_at if latest_health_row else None

    if last_success is None:
        return WatchdogReport(
            source_key=source_key, status=NEVER_RUN, threshold_minutes=max_age_minutes,
            latest_health=latest_health, latest_measurement_at=latest_measurement_at,
            message="no successful collector run exists for this source",
        )

    age_minutes = (utcnow() - last_success.started_at).total_seconds() / 60
    stale = age_minutes > max_age_minutes

    if last_run is not None and last_run.status is IngestStatus.FAILED and last_run.id != last_success.id and stale:
        return WatchdogReport(
            source_key=source_key, status=FAILED, threshold_minutes=max_age_minutes,
            last_success_at=last_success.started_at, age_minutes=age_minutes,
            latest_health=latest_health, latest_measurement_at=latest_measurement_at,
            message=f"most recent attempt failed: {last_run.error_message or 'no detail recorded'}",
        )

    if stale:
        return WatchdogReport(
            source_key=source_key, status=STALE, threshold_minutes=max_age_minutes,
            last_success_at=last_success.started_at, age_minutes=age_minutes,
            latest_health=latest_health, latest_measurement_at=latest_measurement_at,
            message=f"last successful run is older than the {max_age_minutes:g}-minute threshold",
        )

    return WatchdogReport(
        source_key=source_key, status=HEALTHY, threshold_minutes=max_age_minutes,
        last_success_at=last_success.started_at, age_minutes=age_minutes,
        latest_health=latest_health, latest_measurement_at=latest_measurement_at,
    )
