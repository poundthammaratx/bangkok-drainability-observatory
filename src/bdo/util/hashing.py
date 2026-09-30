"""Hashing helpers for payload integrity and deterministic deduplication keys."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime

from bdo.util.time import ensure_utc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_value(value_num: float | None, value_text: str | None) -> str:
    """Canonical, representation-independent value string (1.20 == 1.2 == 1.2000000001e0 at 10 s.f.)."""
    if value_num is not None:
        if isinstance(value_num, float) and math.isnan(value_num):
            return "num:NaN"
        return "num:" + format(float(value_num), ".10g")
    if value_text is not None:
        return "txt:" + " ".join(str(value_text).split())
    return "null"


def measurement_dedup_key(
    source_id: int,
    station_id: int | None,
    variable: str,
    measurement_at: datetime | None,
    value_num: float | None,
    value_text: str | None,
    external_record_id: str | None = None,
) -> str:
    """Deterministic identity of a measurement.

    Identity = (source, station, variable, measurement_at, value). Retrieval time is deliberately
    NOT part of the key: re-retrieving the same reading is not a new reading.

    When measurement_at is unknown, the source's own record id is added (if any) so that distinct
    undated records with coincidentally equal values are not collapsed.
    """
    ts = ensure_utc(measurement_at, "UTC").isoformat() if measurement_at else "UNKNOWN"
    parts = [
        str(source_id),
        "" if station_id is None else str(station_id),
        variable.strip(),
        ts,
        canonical_value(value_num, value_text),
    ]
    if measurement_at is None:
        parts.append("rec:" + (external_record_id or ""))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
