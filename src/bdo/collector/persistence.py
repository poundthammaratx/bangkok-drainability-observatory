"""Turns one ``LiveSourceState`` into persisted rows.

Reuses the existing, tested write paths end to end — nothing here reimplements dedup, station
upsert, or raw-archive logic:

* raw payloads  -> ``bdo.repository.snapshots.save_snapshot_inline`` (inline/DB-backed; the
  collector may run on a different machine from the viewer, see docs/PERSISTENT_ARCHIVE.md)
* stations      -> ``bdo.repository.stations.upsert`` (same upsert the local CKAN adapter uses)
* measurements  -> ``bdo.repository.measurements.insert_measurements`` (the fingerprint-based
  dedup/revision logic from v0.1 — a source correction at the same ``measurement_at`` is kept as
  a second row, never silently discarded; see docs/PERSISTENT_ARCHIVE.md §Deduplication)
* source health -> ``bdo.repository.source_health.record`` (new in v0.3)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from bdo.enums import EvidenceClass, NodeType
from bdo.live.base import LiveSourceState
from bdo.models import Source
from bdo.repository import measurements as meas_repo
from bdo.repository import snapshots as snap_repo
from bdo.repository import source_health as health_repo
from bdo.repository import stations as station_repo
from bdo.schemas import NormalizedMeasurement, NormalizedStation, RawPayload
from bdo.util.time import utcnow


@dataclass
class PersistStats:
    snapshots_saved: int = 0
    stations_upserted: int = 0
    records_retrieved: int = 0
    records_inserted: int = 0
    records_skipped: int = 0


def persist_state(
    session: Session,
    source: Source,
    state: LiveSourceState,
    *,
    collector_run_id: int | None,
    latency_ms: float | None = None,
) -> PersistStats:
    stats = PersistStats(records_retrieved=len(state.measurements))
    retrieved_at = state.fetch_finished_at or utcnow()

    # 1. raw provenance — one row per payload the adapter already fetched (see RawPayloadCapture)
    for capture in state.raw_payloads:
        snap_repo.save_snapshot_inline(
            session, source,
            RawPayload(content=capture.content, content_type=capture.content_type, label=capture.label,
                      request_url=capture.request_url, http_status=capture.http_status, extension="json"),
            retrieved_at=retrieved_at, ingest_run_id=collector_run_id,
            parser_version=f"live/{source.source_key}",
        )
        stats.snapshots_saved += 1
    session.commit()  # raw is durable before any measurement write, same invariant as bdo.ingestion.runner

    # 2. stations — upsert whatever topology the live reading itself carries (name, coordinates,
    #    district, operator). For FloodBangkok/ThaiWater this is the *only* place those networks'
    #    stations get a persisted row at all — they are not in the packaged BKK reference registry.
    station_ids: dict[str, int] = {}
    seen_stations: set[str] = set()
    for m in state.measurements:
        if not m.external_station_id or m.external_station_id in seen_stations:
            continue
        seen_stations.add(m.external_station_id)
        try:
            node_type = NodeType(m.node_type)
        except ValueError:
            node_type = NodeType.other
        coord_evidence = EvidenceClass(m.evidence_class) if m.latitude is not None else None
        ns = NormalizedStation(
            external_station_id=m.external_station_id, name=m.station_name or m.external_station_id,
            node_type=node_type, latitude=m.latitude, longitude=m.longitude, district=m.district,
            operator=m.operator, coordinate_evidence_class=coord_evidence,
            notes="persisted by the v0.3 collector from a live read-through reading",
        )
        st = station_repo.upsert(session, source.id, ns)
        station_ids[m.external_station_id] = st.id
    stats.stations_upserted = len(station_ids)
    session.commit()

    # 3. measurements — convert to the same NormalizedMeasurement the ingestion pipeline uses, then
    #    hand off to the existing fingerprint-based dedup/insert (no new logic, see module docstring)
    items = []
    for m in state.measurements:
        station_id = station_ids.get(m.external_station_id) if m.external_station_id else None
        nm = NormalizedMeasurement(
            variable=m.variable, value_num=m.value_num, value_text=m.value_text, unit=m.unit,
            measurement_at=m.measurement_at, evidence_class=EvidenceClass(m.evidence_class),
            station_external_id=m.external_station_id, quality_flags=set(m.quality_flags), notes=m.notes,
        )
        items.append((nm, station_id, None))
    insert_stats = meas_repo.insert_measurements(session, source.id, items, retrieved_at, collector_run_id,
                                                 f"live/{source.source_key}")
    stats.records_inserted = insert_stats.inserted
    stats.records_skipped = insert_stats.skipped_duplicate
    session.commit()

    # 4. source health history — always recorded, success or failure, so availability itself
    #    becomes queryable history (milestone §13)
    health_repo.record(session, source, state, collector_run_id=collector_run_id, latency_ms=latency_ms)
    session.commit()

    return stats
