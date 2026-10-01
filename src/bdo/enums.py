"""Controlled vocabularies.

All enums are ``str`` subclasses so they serialise to their literal names in the database,
in CSV exports and in JSON provenance records. Persisted as VARCHAR + CHECK constraint
(portable to PostgreSQL without native enum migrations).
"""

from __future__ import annotations

from enum import Enum


class EvidenceClass(str, Enum):
    """Epistemic class of a record. Never converted silently from one class to another."""

    OBSERVED = "OBSERVED"                          # seen/measured directly by the research team
    OFFICIAL_REPORTED = "OFFICIAL_REPORTED"        # published by a responsible agency
    THIRD_PARTY_REPORTED = "THIRD_PARTY_REPORTED"  # media, citizens, non-responsible parties
    MODELLED = "MODELLED"                          # output of a model
    ASSUMED = "ASSUMED"                            # researcher assumption / placeholder


class SourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    MANUAL = "MANUAL"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class RuntimeRole(str, Enum):
    """Who this process is, for the v0.3 security/runtime-role separation (see
    docs/DATABASE_DEPLOYMENT.md). Orthogonal to ``Settings.public_deployment``, which both
    predates this enum and remains independently authoritative for "no writes" — see
    ``bdo.util.access.assert_writes_allowed``.
    """

    VIEWER = "VIEWER"            # public Streamlit: DB reads + live GET only, never writes
    COLLECTOR = "COLLECTOR"      # scheduled/CLI collection: official GET + archive writes
    DEVELOPMENT = "DEVELOPMENT"  # local researcher use: full read/write, as in v0.1/v0.2


class IngestStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"   # raw preserved, some records failed normalisation/validation
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"   # source MANUAL/UNAVAILABLE; nothing fetched


class FreshnessLabel(str, Enum):
    LIVE = "LIVE"
    RECENT = "RECENT"
    STALE = "STALE"
    VERY_STALE = "VERY_STALE"
    UNKNOWN = "UNKNOWN"


class NodeType(str, Enum):
    rain_gauge = "rain_gauge"
    water_level_station = "water_level_station"
    river_gauge = "river_gauge"
    road_flood_sensor = "road_flood_sensor"
    road_flood_sensor_network = "road_flood_sensor_network"
    canal = "canal"
    drain = "drain"
    sewer = "sewer"
    pump_station = "pump_station"
    gate = "gate"
    retention = "retention"
    dam = "dam"
    barrage = "barrage"
    tunnel = "tunnel"
    river_boundary = "river_boundary"
    surface_low_point = "surface_low_point"
    road_node = "road_node"
    transit_node = "transit_node"
    other = "other"


class ValidationStatus(str, Enum):
    """Validation state of a station/system-node record (not of its measurements)."""

    UNVERIFIED = "UNVERIFIED"                  # as published, not cross-checked
    TYPE_INFERRED = "TYPE_INFERRED"            # node_type inferred (e.g. from code prefix)
    VERIFIED_DOCUMENT = "VERIFIED_DOCUMENT"    # checked against an official document
    VERIFIED_FIELD = "VERIFIED_FIELD"          # checked in the field
    DEPRECATED = "DEPRECATED"


class Passability(str, Enum):
    normal = "normal"
    pedestrian_only = "pedestrian_only"
    motorcycle_only = "motorcycle_only"
    high_clearance_only = "high_clearance_only"
    impassable = "impassable"
    unknown = "unknown"


class Trend(str, Enum):
    rising = "rising"
    stable = "stable"
    falling = "falling"
    dry = "dry"
    unknown = "unknown"


class QualityFlag(str, Enum):
    """Machine-readable quality flags. A record may carry several (stored ';'-joined)."""

    SEED_DEMONSTRATION = "SEED_DEMONSTRATION"
    TZ_DECLARED_BY_CONFIG = "TZ_DECLARED_BY_CONFIG"          # naive source timestamp, zone from config
    UNIT_DECLARED_UNVERIFIED = "UNIT_DECLARED_UNVERIFIED"    # unit from config, not stated in source
    DATE_DM_AMBIGUOUS = "DATE_DM_AMBIGUOUS"                  # day<=12: day/month could be transposed
    DATE_REPAIRED_DM_SWAP = "DATE_REPAIRED_DM_SWAP"          # transposition repaired; original in notes
    DATE_NON_MONOTONIC = "DATE_NON_MONOTONIC"                # breaks expected sequence; not repaired
    DATE_TIME_FIELD_CONFLICT = "DATE_TIME_FIELD_CONFLICT"    # separate time field disagrees with timestamp
    MEASUREMENT_TIME_UNKNOWN = "MEASUREMENT_TIME_UNKNOWN"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    MANUAL_TRANSCRIPTION = "MANUAL_TRANSCRIPTION"
    AGGREGATE_COUNT = "AGGREGATE_COUNT"                      # network-level count, not a point value


def join_flags(flags: list[str] | set[str] | None) -> str | None:
    if not flags:
        return None
    return ";".join(sorted({str(getattr(f, "value", f)) for f in flags}))


def split_flags(flag_str: str | None) -> set[str]:
    if not flag_str:
        return set()
    return {f for f in flag_str.split(";") if f}
