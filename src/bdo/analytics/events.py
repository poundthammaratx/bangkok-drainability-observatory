"""Recent-change events — derived only from recorded facts, never a causal explanation.

Milestone §17C Zone 5 ("Recent Changes / Event Strip"): "Only create events from recorded facts.
Do not generate causal explanations." Two sources of fact, both already persisted for other
reasons:

* ``SourceHealthHistory`` transitions — a source's health changed between two collector runs.
* FloodBangkok ``device_status`` transitions recorded in ``Measurement.notes`` — a station's
  reported state changed between two persisted readings.

No other inference is performed: an event is emitted only when two consecutive, timestamped,
already-persisted records disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.models import Measurement, Source, Station
from bdo.repository import source_health as health_repo


@dataclass(frozen=True)
class ChangeEvent:
    at: datetime
    kind: str  # "source_health" | "station_status"
    text: str


def source_health_transitions(session: Session, start: datetime, end: datetime) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    for src in session.scalars(select(Source)):
        rows = sorted(health_repo.history_for(session, src.id, start=start, end=end, limit=500),
                      key=lambda r: r.checked_at)
        prev = None
        for r in rows:
            if prev is not None and r.health != prev.health:
                events.append(ChangeEvent(
                    at=r.checked_at, kind="source_health",
                    text=f"{src.source_key} became {r.health} (was {prev.health})",
                ))
            prev = r
    return events


def station_status_transitions(session: Session, start: datetime, end: datetime) -> list[ChangeEvent]:
    """Device-status changes recorded in ``Measurement.notes`` (currently populated by the
    FloodBangkok collector only; see ``bdo.live.adapters.floodbangkok_live``)."""
    rows = session.execute(
        select(Measurement.station_id, Measurement.retrieved_at, Measurement.notes,
               Station.name, Station.external_station_id)
        .join(Station, Measurement.station_id == Station.id)
        .where(Measurement.notes.like("%device_status=%"))
        .where(Measurement.retrieved_at >= start, Measurement.retrieved_at <= end)
        .order_by(Measurement.station_id, Measurement.retrieved_at)
    ).all()

    events: list[ChangeEvent] = []
    prev_status: dict[int, str] = {}
    for station_id, retrieved_at, notes, name, ext_id in rows:
        status = None
        if notes and "device_status=" in notes:
            status = notes.split("device_status=", 1)[1].split(";")[0].strip()
        if status is None:
            continue
        if station_id in prev_status and status != prev_status[station_id]:
            events.append(ChangeEvent(
                at=retrieved_at, kind="station_status",
                text=f"{name or ext_id} changed {prev_status[station_id]} -> {status}",
            ))
        prev_status[station_id] = status
    return events


def recent_events(session: Session, start: datetime, end: datetime, limit: int = 100) -> list[ChangeEvent]:
    events = source_health_transitions(session, start, end) + station_status_transitions(session, start, end)
    events.sort(key=lambda e: e.at, reverse=True)
    return events[:limit]
