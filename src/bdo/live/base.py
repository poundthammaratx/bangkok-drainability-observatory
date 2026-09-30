"""Live read-through data types and shared helpers.

Everything in ``bdo.live`` is transient: a GET against a public official endpoint, held in memory
for at most its configured TTL (see ``bdo.live.cache``), never archived and never written to the
database. This is deliberate — see docs/LIVE_DATA_ARCHITECTURE.md — and is why these objects are
plain dataclasses rather than SQLAlchemy models: nothing here is meant to survive the process.

PUBLIC_DEPLOYMENT never disables this module: read-only HTTP GET against official sources is
allowed even in the public read-only alpha (only database/file writes are blocked — see
``bdo.util.access``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import httpx

from bdo.analytics.freshness import classify_age
from bdo.config import FreshnessThresholds, Settings
from bdo.enums import FreshnessLabel
from bdo.util.time import utcnow


class LiveHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveMeasurement:
    """One transient, read-through value. Never persisted; see ``bdo.schemas.NormalizedMeasurement``
    for the equivalent object that *does* get archived by the ingestion pipeline."""

    source_key: str
    external_station_id: str | None
    station_name: str | None
    node_type: str
    variable: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    measurement_at: datetime | None  # aware UTC; None = unknown (never back-filled)
    evidence_class: str
    quality_flags: frozenset[str] = field(default_factory=frozenset)
    latitude: float | None = None
    longitude: float | None = None
    district: str | None = None
    operator: str | None = None
    notes: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class LiveSourceState:
    """Per-source read-through result. See docs/LIVE_DATA_ARCHITECTURE.md §Health enum."""

    source_key: str
    endpoint: str | None
    fetch_started_at: datetime
    fetch_finished_at: datetime | None
    http_status: int | None
    health: LiveHealth
    record_count: int
    source_measurement_at: datetime | None   # freshest measurement_at actually seen, if any
    freshness: FreshnessLabel
    error: str | None = None
    measurements: tuple[LiveMeasurement, ...] = ()
    # Adapter-specific extras that don't fit a single measurement (e.g. FloodBangkok's
    # working/failed sensor counts, TMD's product list). Never used for alerting — display only.
    context: dict = field(default_factory=dict)

    @property
    def latest_measurement_at(self) -> datetime | None:
        return self.source_measurement_at

    @property
    def age_minutes(self) -> float | None:
        if self.source_measurement_at is None:
            return None
        return (utcnow() - self.source_measurement_at).total_seconds() / 60


def classify_live_health(
    *, http_ok: bool, record_count: int, freshest: datetime | None,
    thresholds: FreshnessThresholds, had_parse_errors: bool = False,
) -> tuple[LiveHealth, FreshnessLabel]:
    """Never HEALTHY on HTTP 200 alone — the data timestamp is always considered too."""
    if not http_ok:
        return LiveHealth.UNAVAILABLE, FreshnessLabel.UNKNOWN
    if record_count == 0:
        return (LiveHealth.DEGRADED if had_parse_errors else LiveHealth.UNKNOWN), FreshnessLabel.UNKNOWN
    label = classify_age(None if freshest is None else utcnow() - freshest, thresholds)
    if label is FreshnessLabel.UNKNOWN:
        return LiveHealth.UNKNOWN, label
    if label in (FreshnessLabel.LIVE, FreshnessLabel.RECENT):
        return (LiveHealth.DEGRADED if had_parse_errors else LiveHealth.HEALTHY), label
    return LiveHealth.STALE, label  # STALE or VERY_STALE measurement age


def make_client(settings: Settings) -> httpx.Client:
    """A short-lived httpx client for one read-through fetch; callers close it (``with``)."""
    return httpx.Client(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.http_user_agent},
        follow_redirects=True,
        trust_env=settings.http_trust_env,
    )


def unavailable_state(source_key: str, endpoint: str | None, started: datetime, error: str) -> LiveSourceState:
    return LiveSourceState(
        source_key=source_key, endpoint=endpoint, fetch_started_at=started, fetch_finished_at=utcnow(),
        http_status=None, health=LiveHealth.UNAVAILABLE, record_count=0, source_measurement_at=None,
        freshness=FreshnessLabel.UNKNOWN, error=error[:500],
    )
