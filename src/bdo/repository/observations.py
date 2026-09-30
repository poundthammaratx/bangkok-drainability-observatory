"""Field-observation import (CSV -> validated FieldObservation rows).

* The CSV file itself is archived byte-exact as a RawSnapshot of ``research_field_observations``.
* Each row is validated by ``FieldObservationRow`` (invalid evidence_class etc. are rejected).
* Rejected rows are written to an import-error report under data/exports/.
* Re-importing the same rows is idempotent (keyed by observation_id, or by a content hash when
  observation_id is blank).
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.config import Settings
from bdo.models import FieldObservation
from bdo.repository import snapshots as snap_repo
from bdo.repository import sources as source_repo
from bdo.schemas import FIELD_OBSERVATION_COLUMNS, FieldObservationRow, RawPayload
from bdo.util.access import assert_writes_allowed
from bdo.util.hashing import sha256_bytes
from bdo.util.time import compact_utc_stamp, utcnow

FIELD_SOURCE_KEY = "research_field_observations"
REQUIRED_COLUMNS = {"observed_at", "location_name", "evidence_class", "phenomenon"}


@dataclass
class ImportReport:
    file: str
    rows_total: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    rejected: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_snapshot_id: int | None = None
    error_report_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.rejected == 0 and not any(e.get("row") == "header" for e in self.errors)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ())) or "row"
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


def _auto_id(row: dict) -> str:
    basis = "|".join(str(row.get(c) or "").strip() for c in FIELD_OBSERVATION_COLUMNS if c != "observation_id")
    return "auto:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def import_csv(session: Session, settings: Settings, csv_path: str | Path, strict: bool = False) -> ImportReport:
    assert_writes_allowed(settings, "field-observation import")
    csv_path = Path(csv_path)
    content = csv_path.read_bytes()
    report = ImportReport(file=str(csv_path))

    src = source_repo.get_by_key(session, FIELD_SOURCE_KEY)
    if src is None:
        src = source_repo.upsert_from_config(session, settings.source(FIELD_SOURCE_KEY))

    # 1. Preserve the file as submitted (even if every row later fails).
    existing = snap_repo.find_by_hash(session, src.id, sha256_bytes(content))
    if existing is not None:
        snap = existing
        report.warnings.append(f"identical file already archived as snapshot {existing.id}; not re-archived")
    else:
        snap = snap_repo.save_snapshot(
            session, settings, src,
            RawPayload(content=content, content_type="text/csv", label=csv_path.stem, extension="csv",
                       request_url=f"file://{csv_path.resolve()}", notes="field-observation CSV as imported"),
            retrieved_at=utcnow(), parser_version="field_obs_csv/0.1",
        )
        session.commit()
    report.raw_snapshot_id = snap.id

    # 2. Parse.
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = REQUIRED_COLUMNS - set(header)
    if missing:
        report.errors.append({"row": "header", "observation_id": "", "error": f"missing columns: {sorted(missing)}"})
        report.rejected = 0
        _write_error_report(settings, report)
        return report
    unknown = [h for h in header if h not in FIELD_OBSERVATION_COLUMNS]
    if unknown:
        report.warnings.append(f"ignored unknown columns: {unknown}")

    valid: list[tuple[str, FieldObservationRow]] = []
    for i, raw_row in enumerate(reader, start=2):  # line 1 is the header
        report.rows_total += 1
        row = {k.strip(): v for k, v in raw_row.items() if k and k.strip() in FIELD_OBSERVATION_COLUMNS}
        try:
            parsed = FieldObservationRow(**row)
        except ValidationError as exc:
            report.rejected += 1
            report.errors.append({"row": i, "observation_id": row.get("observation_id") or "",
                                  "error": _format_validation_error(exc)})
            continue
        ext_id = parsed.observation_id or _auto_id(row)
        valid.append((ext_id, parsed))

    if strict and report.rejected:
        report.warnings.append("strict mode: no rows inserted because at least one row was rejected")
        _write_error_report(settings, report)
        return report

    # 3. Insert (idempotent).
    ids = [e for e, _ in valid]
    already = set(session.scalars(select(FieldObservation.external_observation_id)
                                  .where(FieldObservation.external_observation_id.in_(ids)))) if ids else set()
    batch_seen: set[str] = set()
    for ext_id, r in valid:
        if ext_id in already or ext_id in batch_seen:
            report.skipped_duplicate += 1
            continue
        batch_seen.add(ext_id)
        session.add(FieldObservation(
            external_observation_id=ext_id, observed_at=r.observed_at, location_name=r.location_name,
            latitude=r.latitude, longitude=r.longitude, district=r.district, evidence_class=r.evidence_class,
            phenomenon=r.phenomenon, water_depth_cm=r.water_depth_cm, passability=r.passability,
            trend=r.trend, photo_ref=r.photo_ref, notes=r.notes, source_url=r.source_url,
            raw_snapshot_id=snap.id,
        ))
        report.inserted += 1
    session.commit()
    _write_error_report(settings, report)
    return report


def _write_error_report(settings: Settings, report: ImportReport) -> None:
    if not report.errors:
        return
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.exports_dir / f"field_obs_import_errors_{compact_utc_stamp(utcnow())}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["row", "observation_id", "error"])
        w.writeheader()
        w.writerows(report.errors)
    report.error_report_path = str(path)
