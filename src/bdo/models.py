"""ORM domain model (SQLAlchemy 2.x).

Portability notes (SQLite -> PostgreSQL/PostGIS):
* Timestamps use ``UTCDateTime``: stored as UTC; on PostgreSQL this maps to TIMESTAMPTZ.
* Enums are VARCHAR + CHECK constraints (no native enum types to migrate).
* Coordinates are plain lat/lon floats (WGS84). A PostGIS geometry column can be added later
  and back-filled from these without touching the domain model.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bdo.enums import (
    EvidenceClass,
    IngestStatus,
    Passability,
    SourceStatus,
    Trend,
    ValidationStatus,
)
from bdo.util.time import UTC, ensure_utc, utcnow


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime on every backend. Rejects naive datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected by UTCDateTime; attach a zone first")
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return ensure_utc(value)


def _enum(e, name: str):
    return Enum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda x: [m.value for m in x],
        length=32,
    )


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    agency: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    data_domain: Mapped[str | None] = mapped_column(String(512))
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    typical_freshness_minutes: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus, "source_status"), nullable=False)
    default_evidence_class: Mapped[EvidenceClass] = mapped_column(
        _enum(EvidenceClass, "source_evidence_class"), nullable=False
    )
    adapter: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    last_healthcheck_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_health_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    stations: Mapped[list["Station"]] = relationship(back_populates="source")


class Station(Base):
    """A station or, more generally, a node in the drainage system graph."""

    __tablename__ = "stations"
    __table_args__ = (UniqueConstraint("source_id", "external_station_id", name="uq_station_source_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    external_station_id: Mapped[str | None] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    district: Mapped[str | None] = mapped_column(String(128))
    catchment: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[str | None] = mapped_column(String(256))
    state_variable: Mapped[str | None] = mapped_column(String(128))
    actuator_or_capacity: Mapped[str | None] = mapped_column(String(256))
    upstream_nodes_text: Mapped[str | None] = mapped_column(Text)
    downstream_nodes_text: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[ValidationStatus] = mapped_column(
        _enum(ValidationStatus, "validation_status"), nullable=False, default=ValidationStatus.UNVERIFIED
    )
    source_type_code: Mapped[str | None] = mapped_column(String(64))   # verbatim source classification
    coordinate_evidence_class: Mapped[EvidenceClass | None] = mapped_column(
        _enum(EvidenceClass, "coord_evidence_class")
    )
    raw_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)

    source: Mapped[Source | None] = relationship(back_populates="stations")


# Alias used in docs/UI: every station is a system node.
SystemNode = Station


class IngestRun(Base):
    """One run of the write pipeline — local manual/CKAN ingestion (mode ``automatic`` /
    ``manual`` / ``seed``) or, since v0.3, a collector invocation (mode ``collector``; see
    ``src/bdo/collector/runner.py``). The milestone's "collector_runs" concept is deliberately
    *not* a separate table — this one already records exactly that shape of information."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="automatic")  # automatic|manual|seed|collector
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[IngestStatus] = mapped_column(_enum(IngestStatus, "ingest_status"), nullable=False)
    records_retrieved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stations_upserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshots_saved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(64))

    source: Mapped[Source] = relationship()


class SourceHealthHistory(Base):
    """One health observation over time (v0.3). ``Source.last_health_message`` /
    ``last_healthcheck_at`` only ever hold the *latest* result; this table makes availability
    itself queryable historically — "when did a source stop reporting", "how long was it down",
    "was the data already stale before the endpoint failed". Populated by the collector
    (``src/bdo/collector/persistence.py``) once per run; the Streamlit UI never writes here.
    """

    __tablename__ = "source_health_history"
    __table_args__ = (Index("ix_health_history_source_checked", "source_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    collector_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_runs.id"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    # bdo.live.base.LiveHealth vocabulary (HEALTHY/DEGRADED/STALE/UNAVAILABLE/UNKNOWN) — a
    # different, deliberately separate vocabulary from bdo.enums.SourceStatus (the ingestion
    # adapter's own ACTIVE/MANUAL/UNAVAILABLE/DEGRADED/UNKNOWN), stored as plain text so the two
    # never silently collapse into each other.
    health: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latest_measurement_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    freshness_label: Mapped[str | None] = mapped_column(String(16))
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(64))

    source: Mapped[Source] = relationship()


class RawSnapshot(Base):
    """Metadata of an immutable raw payload.

    Two storage modes, chosen by whichever repository function writes the row:

    * **File-backed** (``bdo.repository.snapshots.save_snapshot``, the v0.1/v0.2 path): bytes go
      to ``data/raw/<source_key>/YYYY/MM/DD/`` and ``payload_path`` records where. Used by the
      local CLI ingestion pipeline, where the writer and the Streamlit viewer share a filesystem.
    * **Inline** (``bdo.repository.snapshots.save_snapshot_inline``, added in v0.3 for the
      collector): small text/JSON bodies go straight into ``payload_text``, so a collector running
      on a separate machine from the viewer (e.g. GitHub Actions writing to a remote Postgres that
      Streamlit Cloud reads) doesn't need a shared filesystem at all. Binary or oversized payloads
      (radar images) are recorded as metadata-only provenance — hash and size, no body — rather
      than stored in the database; see docs/PERSISTENT_ARCHIVE.md's object-storage-reference TODO.

    Exactly one of ``payload_path`` / ``payload_text`` is set, or neither (metadata-only); never
    both. ``payload_hash`` is always the SHA-256 of the original bytes either way.
    """

    __tablename__ = "raw_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source_measurement_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_path: Mapped[str | None] = mapped_column(String(1024), unique=True)
    payload_text: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    request_url: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    ingest_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_runs.id"))
    label: Mapped[str | None] = mapped_column(String(256))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    source: Mapped[Source] = relationship()


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_measurement_dedup"),
        Index("ix_measurement_station_var_time", "station_id", "variable", "measurement_at"),
        Index("ix_measurement_time", "measurement_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"))
    external_record_id: Mapped[str | None] = mapped_column(String(256))
    variable: Mapped[str] = mapped_column(String(128), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    # Time the source says the value represents. NULL = unknown (never back-filled with retrieved_at).
    measurement_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The literal timestamp as published by the source (e.g. ThaiWater's naive "2026-10-01 17:39"),
    # preserved verbatim alongside the normalized, timezone-aware measurement_at above (v0.3
    # production hardening — added in migration 0002). NULL when the source/adapter has no discrete
    # raw timestamp field, or for rows persisted before this column existed; never back-filled or
    # fabricated either way.
    source_timestamp_raw: Mapped[str | None] = mapped_column(Text)
    # Time our system (or a human transcriber) retrieved it.
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    evidence_class: Mapped[EvidenceClass] = mapped_column(
        _enum(EvidenceClass, "measurement_evidence_class"), nullable=False
    )
    quality_flag: Mapped[str | None] = mapped_column(String(512))
    raw_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    ingest_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_runs.id"))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    source: Mapped[Source] = relationship()
    station: Mapped[Station | None] = relationship()
    raw_snapshot: Mapped[RawSnapshot | None] = relationship()


class FieldObservation(Base):
    __tablename__ = "field_observations"
    __table_args__ = (UniqueConstraint("external_observation_id", name="uq_field_obs_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_observation_id: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    location_name: Mapped[str] = mapped_column(String(512), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    district: Mapped[str | None] = mapped_column(String(128))
    evidence_class: Mapped[EvidenceClass] = mapped_column(
        _enum(EvidenceClass, "fieldobs_evidence_class"), nullable=False
    )
    phenomenon: Mapped[str] = mapped_column(String(128), nullable=False)
    water_depth_cm: Mapped[float | None] = mapped_column(Float)
    passability: Mapped[Passability | None] = mapped_column(_enum(Passability, "passability"))
    trend: Mapped[Trend | None] = mapped_column(_enum(Trend, "trend"))
    photo_ref: Mapped[str | None] = mapped_column(String(1024))
    notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    raw_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class DerivedMetric(Base):
    """Schema placeholder for Phase 3–4 quantities (T_recover, V_residual, R_drain, dh_dt).

    v0.1 never writes to this table.
    """

    __tablename__ = "derived_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"))
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[EvidenceClass] = mapped_column(
        _enum(EvidenceClass, "metric_evidence_class"), nullable=False, default=EvidenceClass.MODELLED
    )
    input_provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
