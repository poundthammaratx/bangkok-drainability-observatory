"""Measurement persistence with deterministic deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from bdo.enums import QualityFlag, join_flags
from bdo.models import Measurement
from bdo.schemas import NormalizedMeasurement
from bdo.util.hashing import measurement_dedup_key


@dataclass
class InsertStats:
    inserted: int = 0
    skipped_duplicate: int = 0
    inserted_ids: list[int] = field(default_factory=list)


def current_observations(
    session: Session,
    *,
    source_ids: list[int] | None = None,
    station_ids: list[int] | None = None,
    variables: list[str] | None = None,
) -> list[Measurement]:
    """One row per (source_id, station_id, variable): the canonical *current* observation.

    Selected by the greatest ``measurement_at``, tie-broken by the greatest ``retrieved_at`` — the
    natural-key + latest-retrieved-revision rule from docs/PERSISTENT_ARCHIVE.md §Deduplication.
    Never by database id, and never an average/merge across revisions: a source correction at the
    same ``measurement_at`` is a second row with a later ``retrieved_at``, and this is exactly the
    row that wins.

    One indexed SQL query (a window function), not a full-table pandas scan — the v0.3
    current-state resolver (``bdo.analytics.current_state``) and the Live Situation page use this
    instead of re-implementing the "latest per series" rule in Python.
    """
    rn = (
        func.row_number()
        .over(
            partition_by=(Measurement.source_id, Measurement.station_id, Measurement.variable),
            order_by=(Measurement.measurement_at.desc().nullslast(), Measurement.retrieved_at.desc()),
        )
        .label("rn")
    )
    base = select(Measurement, rn)
    if source_ids:
        base = base.where(Measurement.source_id.in_(source_ids))
    if station_ids:
        base = base.where(Measurement.station_id.in_(station_ids))
    if variables:
        base = base.where(Measurement.variable.in_(variables))
    sub = base.subquery()
    ranked = aliased(Measurement, sub)
    q = select(ranked).where(sub.c.rn == 1)
    return list(session.scalars(q))


def existing_keys(session: Session, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        found.update(session.scalars(select(Measurement.dedup_key).where(Measurement.dedup_key.in_(chunk))))
    return found


def insert_measurements(
    session: Session,
    source_id: int,
    items: list[tuple[NormalizedMeasurement, int | None, int | None]],
    retrieved_at: datetime,
    ingest_run_id: int | None,
    parser_version: str | None,
) -> InsertStats:
    """Insert ``(normalized, station_id, raw_snapshot_id)`` tuples, skipping duplicates.

    Duplicates are detected against the database *and* within the batch, by
    (source, station, variable, measurement_at, value) — never by retrieval time.
    """
    stats = InsertStats()
    keyed = []
    for nm, station_id, snap_id in items:
        key = measurement_dedup_key(
            source_id, station_id, nm.variable, nm.measurement_at, nm.value_num, nm.value_text,
            nm.external_record_id,
        )
        keyed.append((key, nm, station_id, snap_id))

    seen = existing_keys(session, [k for k, *_ in keyed])
    for key, nm, station_id, snap_id in keyed:
        if key in seen:
            stats.skipped_duplicate += 1
            continue
        seen.add(key)
        flags = set(nm.quality_flags)
        if nm.measurement_at is None:
            flags.add(QualityFlag.MEASUREMENT_TIME_UNKNOWN.value)
        m = Measurement(
            source_id=source_id,
            station_id=station_id,
            external_record_id=nm.external_record_id,
            variable=nm.variable,
            value_num=nm.value_num,
            value_text=nm.value_text,
            unit=nm.unit,
            measurement_at=nm.measurement_at,
            source_timestamp_raw=nm.source_timestamp_raw,
            retrieved_at=nm.retrieved_at or retrieved_at,
            evidence_class=nm.evidence_class,
            quality_flag=join_flags(flags),
            raw_snapshot_id=snap_id,
            ingest_run_id=ingest_run_id,
            parser_version=parser_version,
            notes=nm.notes,
            dedup_key=key,
        )
        session.add(m)
        stats.inserted += 1
    session.flush()
    return stats
