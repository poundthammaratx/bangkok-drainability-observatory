"""Measurement persistence with deterministic deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.enums import QualityFlag, join_flags
from bdo.models import Measurement
from bdo.schemas import NormalizedMeasurement
from bdo.util.hashing import measurement_dedup_key


@dataclass
class InsertStats:
    inserted: int = 0
    skipped_duplicate: int = 0
    inserted_ids: list[int] = field(default_factory=list)


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
