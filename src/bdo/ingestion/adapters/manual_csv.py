"""Manual transcription adapter.

Reads researcher-transcribed CSV files from ``data/manual/<source_key>/*.csv`` (or explicit
paths) and pushes them through the same runner as automatic sources, so manual data get the same
provenance: the CSV is archived byte-exact as a RawSnapshot, every row is validated, and
measurements are deduplicated.

CSV columns (see ``ManualMeasurementRow``)::

    external_station_id, station_name, node_type, district, variable, value_num, value_text, unit,
    measurement_at, measurement_timezone, retrieved_at, evidence_class, quality_flag,
    external_record_id, source_url, notes

Lines starting with ``#`` are provenance comments (kept in the raw archive, ignored by the parser).
``measurement_at`` / ``retrieved_at`` without an offset REQUIRE ``measurement_timezone``.
``retrieved_at`` is the time the transcriber viewed the original source; if blank, the file
ingest time is used and the row is flagged.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from pydantic import ValidationError

from bdo.enums import QualityFlag, SourceStatus
from bdo.ingestion.base import SourceAdapter
from bdo.schemas import (
    ManualMeasurementRow,
    NormalizationResult,
    NormalizedMeasurement,
    NormalizedStation,
    RawFetchResult,
    RawPayload,
    SourceHealth,
)
from bdo.util.time import utcnow


class ManualCSVAdapter(SourceAdapter):
    parser_version = "manual_csv/0.1"

    def __init__(self, *a, paths: list[str | Path] | None = None, **kw):
        super().__init__(*a, **kw)
        self._paths = [Path(p) for p in paths] if paths else None

    @property
    def manual_dir(self) -> Path:
        return self.settings.manual_dir / self.cfg.source_key

    def files(self) -> list[Path]:
        if self._paths is not None:
            return self._paths
        if not self.manual_dir.exists():
            return []
        return sorted(p for p in self.manual_dir.glob("*.csv") if p.is_file())

    def healthcheck(self) -> SourceHealth:
        files = self.files()
        missing = [str(p) for p in files if not p.exists()]
        if missing:
            return SourceHealth(status=SourceStatus.DEGRADED, message=f"missing files: {missing}")
        # A manual source is fetchable whenever files exist; its registry status stays MANUAL.
        return SourceHealth(status=SourceStatus.ACTIVE if files else SourceStatus.MANUAL,
                            message=f"{len(files)} manual file(s) in {self.manual_dir if self._paths is None else 'explicit paths'}")

    def fetch(self) -> RawFetchResult:
        retrieved_at = utcnow()
        payloads = []
        for p in self.files():
            payloads.append(RawPayload(
                content=p.read_bytes(), content_type="text/csv", label=p.stem, extension="csv",
                request_url=f"file://{p.resolve()}", notes="manual transcription file", ))
        return RawFetchResult(source_key=self.cfg.source_key, retrieved_at=retrieved_at, payloads=payloads,
                              context={"archive_once": True})

    def normalize(self, raw: RawFetchResult) -> NormalizationResult:
        result = NormalizationResult()
        stations: dict[str, NormalizedStation] = {}
        for idx, payload in enumerate(raw.payloads):
            text = payload.content.decode("utf-8-sig")
            lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
            reader = csv.DictReader(io.StringIO("\n".join(lines)))
            for line_no, row in enumerate(reader, start=1):
                result.records_seen += 1
                row = {k.strip(): v for k, v in row.items() if k}
                try:
                    r = ManualMeasurementRow(**row)
                except (ValidationError, ValueError) as exc:
                    result.record_errors.append(f"{payload.label} data-row {line_no}: {exc}".replace("\n", " ")[:600])
                    continue
                flags = {QualityFlag.MANUAL_TRANSCRIPTION.value}
                if r.quality_flag:
                    flags |= {f.strip() for f in r.quality_flag.split(";") if f.strip()}
                notes = [n for n in (r.notes, f"source_url: {r.source_url}" if r.source_url else None) if n]
                if r.retrieved_at is None:
                    notes.append("transcriber retrieval time not recorded; file ingest time used")
                if r.external_station_id and r.external_station_id not in stations:
                    stations[r.external_station_id] = NormalizedStation(
                        external_station_id=r.external_station_id,
                        name=r.station_name or r.external_station_id,
                        node_type=r.node_type or "other",
                        district=r.district,
                        notes="declared in manual transcription file",
                        payload_index=idx,
                    )
                try:
                    result.measurements.append(NormalizedMeasurement(
                        variable=r.variable, evidence_class=r.evidence_class, value_num=r.value_num,
                        value_text=r.value_text, unit=r.unit, measurement_at=r.measurement_at,
                        retrieved_at=r.retrieved_at, station_external_id=r.external_station_id,
                        external_record_id=r.external_record_id, quality_flags=flags,
                        notes="; ".join(notes) or None, payload_index=idx,
                    ))
                except ValidationError as exc:
                    result.record_errors.append(f"{payload.label} data-row {line_no}: {exc}".replace("\n", " ")[:600])
        result.stations = list(stations.values())
        return result
