"""Pydantic transfer objects between adapters, the runner and importers.

These are the validation boundary: anything that reaches the ORM has passed through here.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bdo.enums import (
    EvidenceClass,
    NodeType,
    Passability,
    SourceStatus,
    Trend,
    ValidationStatus,
)
from bdo.util.time import parse_timestamp, utcnow


def _require_aware(v: datetime | None) -> datetime | None:
    if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
        raise ValueError("timestamp must be timezone-aware")
    return v


class SourceHealth(BaseModel):
    status: SourceStatus
    message: str
    checked_at: datetime = Field(default_factory=utcnow)
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def fetchable(self) -> bool:
        return self.status in (SourceStatus.ACTIVE, SourceStatus.DEGRADED)


class RawPayload(BaseModel):
    """One retrieved byte-exact payload (an HTTP response page, a CSV file, ...)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: bytes
    content_type: str
    label: str = "payload"
    request_url: str | None = None
    http_status: int | None = None
    source_measurement_at: datetime | None = None
    notes: str | None = None
    extension: str = "bin"


class RawFetchResult(BaseModel):
    source_key: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    payloads: list[RawPayload] = Field(default_factory=list)
    # Adapter-private context needed by normalize() (e.g. resource config); never persisted as data.
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retrieved_at")
    @classmethod
    def _aware(cls, v):
        return _require_aware(v)


class NormalizedStation(BaseModel):
    external_station_id: str
    name: str
    node_type: NodeType = NodeType.other
    latitude: float | None = None
    longitude: float | None = None
    district: str | None = None
    operator: str | None = None
    state_variable: str | None = None
    validation_status: ValidationStatus = ValidationStatus.UNVERIFIED
    source_type_code: str | None = None
    coordinate_evidence_class: EvidenceClass | None = None
    notes: str | None = None
    payload_index: int = 0

    @model_validator(mode="after")
    def _coords(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be present or both absent")
        if self.latitude is not None:
            if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
                raise ValueError("coordinates out of WGS84 range")
            if self.coordinate_evidence_class is None:
                raise ValueError("coordinates require coordinate_evidence_class")
        return self


class NormalizedMeasurement(BaseModel):
    variable: str
    evidence_class: EvidenceClass
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    measurement_at: datetime | None = None
    # Only set when the retrieval time differs from the fetch time (e.g. a human transcription
    # that states when the transcriber viewed the original source).
    retrieved_at: datetime | None = None
    station_external_id: str | None = None
    external_record_id: str | None = None
    quality_flags: set[str] = Field(default_factory=set)
    notes: str | None = None
    payload_index: int = 0

    @field_validator("measurement_at", "retrieved_at")
    @classmethod
    def _aware(cls, v):
        return _require_aware(v)

    @field_validator("variable")
    @classmethod
    def _var(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("variable must be non-empty")
        return v

    @field_validator("value_num")
    @classmethod
    def _finite(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("value_num must be finite")
        return v

    @model_validator(mode="after")
    def _has_value(self):
        if self.value_num is None and (self.value_text is None or str(self.value_text).strip() == ""):
            raise ValueError("measurement needs value_num or value_text")
        return self


class NormalizationResult(BaseModel):
    measurements: list[NormalizedMeasurement] = Field(default_factory=list)
    stations: list[NormalizedStation] = Field(default_factory=list)
    record_errors: list[str] = Field(default_factory=list)   # per-record failures (kept, reported)
    records_seen: int = 0


# ---------------------------------------------------------------------------------------------
# Field observation CSV row
# ---------------------------------------------------------------------------------------------

FIELD_OBSERVATION_COLUMNS = [
    "observation_id", "observed_at", "location_name", "latitude", "longitude", "district",
    "evidence_class", "phenomenon", "water_depth_cm", "passability", "trend", "photo_ref",
    "notes", "source_url",
]


def _blank_to_none(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v.strip() if isinstance(v, str) else v


class FieldObservationRow(BaseModel):
    """One row of a field-observation CSV. ``observed_at`` without offset is read as Asia/Bangkok."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str | None = None
    observed_at: datetime
    location_name: str
    latitude: float | None = None
    longitude: float | None = None
    district: str | None = None
    evidence_class: EvidenceClass
    phenomenon: str
    water_depth_cm: float | None = Field(default=None, ge=0, le=1000)
    passability: Passability | None = None
    trend: Trend | None = None
    photo_ref: str | None = None
    notes: str | None = None
    source_url: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blanks(cls, v):
        return _blank_to_none(v)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _ts(cls, v):
        if v is None:
            raise ValueError("observed_at is required")
        return parse_timestamp(v, naive_zone="Asia/Bangkok")

    @field_validator("location_name", "phenomenon")
    @classmethod
    def _nonempty(cls, v):
        if not v:
            raise ValueError("required")
        return v

    @model_validator(mode="after")
    def _coords(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must both be present or both absent")
        if self.latitude is not None and not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError("coordinates out of range")
        return self


class ManualMeasurementRow(BaseModel):
    """One row of a manual measurement transcription CSV (data/manual/<source_key>/*.csv)."""

    model_config = ConfigDict(extra="forbid")

    external_station_id: str | None = None
    station_name: str | None = None
    node_type: NodeType | None = None
    district: str | None = None
    variable: str
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    measurement_at: datetime | None = None
    measurement_timezone: str | None = None
    retrieved_at: datetime | None = None
    evidence_class: EvidenceClass
    quality_flag: str | None = None
    external_record_id: str | None = None
    source_url: str | None = None
    notes: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blanks(cls, v):
        return _blank_to_none(v)

    @model_validator(mode="before")
    @classmethod
    def _parse_times(cls, data):
        if isinstance(data, dict):
            data = {k: _blank_to_none(v) for k, v in data.items()}
            zone = data.get("measurement_timezone")
            for key in ("measurement_at", "retrieved_at"):
                if data.get(key) is not None and not isinstance(data[key], datetime):
                    # naive -> declared zone (column) ; if none declared the parse raises.
                    data[key] = parse_timestamp(data[key], naive_zone=zone)
        return data
