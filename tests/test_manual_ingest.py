"""Manual CSV importers: field observations and manual measurement transcriptions."""

import csv

from bdo.enums import EvidenceClass, IngestStatus, Passability
from bdo.ingestion.runner import run_source
from bdo.models import FieldObservation, Measurement, RawSnapshot
from bdo.repository.observations import import_csv
from bdo.util.time import UTC

HEADER = ("observation_id,observed_at,location_name,latitude,longitude,district,evidence_class,phenomenon,"
          "water_depth_cm,passability,trend,photo_ref,notes,source_url\n")
# Synthetic test rows (not real observations).
VALID_1 = "T-001,2026-09-30T18:30:00,Test location A,13.7563,100.5018,TestDistrict,OBSERVED,road_ponding,15,motorcycle_only,rising,,synthetic,\n"
VALID_2 = "T-002,2026-09-30T19:00:00+07:00,Test location B,,,,THIRD_PARTY_REPORTED,road_ponding,,unknown,unknown,,synthetic,https://example.invalid\n"
BAD_EVIDENCE = "T-003,2026-09-30T19:10:00,Test location C,,,,SEEN,road_ponding,5,normal,stable,,bad evidence,\n"
BAD_PASS = "T-004,2026-09-30T19:20:00,Test location D,,,,OBSERVED,road_ponding,5,swimming,stable,,bad passability,\n"
BAD_COORD = "T-005,2026-09-30T19:30:00,Test location E,13.7,,,OBSERVED,road_ponding,5,normal,stable,,half coordinate,\n"
BAD_DEPTH = "T-006,2026-09-30T19:40:00,Test location F,,,,OBSERVED,road_ponding,-3,normal,stable,,negative depth,\n"


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def test_valid_rows_import_with_provenance(settings, sessions, tmp_path):
    p = _write(tmp_path, "obs.csv", VALID_1 + VALID_2)
    with sessions() as s:
        rep = import_csv(s, settings, p)
    assert rep.ok and rep.inserted == 2 and rep.rejected == 0
    with sessions() as s:
        a = s.query(FieldObservation).filter_by(external_observation_id="T-001").one()
        assert a.observed_at.hour == 11 and a.observed_at.tzinfo is not None   # 18:30 ICT naive -> 11:30 UTC
        assert a.evidence_class is EvidenceClass.OBSERVED and a.passability is Passability.motorcycle_only
        assert a.raw_snapshot_id == rep.raw_snapshot_id
        assert s.get(RawSnapshot, rep.raw_snapshot_id).content_type == "text/csv"


def test_invalid_rows_rejected_and_reported(settings, sessions, tmp_path):
    p = _write(tmp_path, "mixed.csv", VALID_1 + BAD_EVIDENCE + BAD_PASS + BAD_COORD + BAD_DEPTH)
    with sessions() as s:
        rep = import_csv(s, settings, p)
    assert rep.inserted == 1 and rep.rejected == 4 and not rep.ok
    by_id = {e["observation_id"]: e["error"] for e in rep.errors}
    assert "evidence_class" in by_id["T-003"]
    assert "passability" in by_id["T-004"]
    assert "latitude and longitude" in by_id["T-005"]
    assert "water_depth_cm" in by_id["T-006"]
    with open(rep.error_report_path, encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 4
    with sessions() as s:
        assert s.query(FieldObservation).count() == 1
        assert s.query(RawSnapshot).count() == 1   # the rejected-row file is still archived


def test_strict_mode_inserts_nothing_on_any_error(settings, sessions, tmp_path):
    p = _write(tmp_path, "strict.csv", VALID_1 + BAD_EVIDENCE)
    with sessions() as s:
        rep = import_csv(s, settings, p, strict=True)
    assert rep.inserted == 0 and rep.rejected == 1
    with sessions() as s:
        assert s.query(FieldObservation).count() == 0


def test_reimport_is_idempotent(settings, sessions, tmp_path):
    p = _write(tmp_path, "obs.csv", VALID_1 + VALID_2)
    with sessions() as s:
        import_csv(s, settings, p)
    with sessions() as s:
        rep = import_csv(s, settings, p)
    assert rep.inserted == 0 and rep.skipped_duplicate == 2
    with sessions() as s:
        assert s.query(FieldObservation).count() == 2 and s.query(RawSnapshot).count() == 1


def test_missing_required_columns(settings, sessions, tmp_path):
    p = tmp_path / "bad_header.csv"
    p.write_text("observation_id,location_name\nX,Somewhere\n", encoding="utf-8")
    with sessions() as s:
        rep = import_csv(s, settings, p)
    assert not rep.ok and rep.errors[0]["row"] == "header"


def test_manual_measurement_csv_rejects_invalid_evidence(settings, sessions, tmp_path):
    p = tmp_path / "manual.csv"
    p.write_text(
        "# test transcription\n"
        "external_station_id,station_name,node_type,variable,value_num,unit,measurement_at,measurement_timezone,evidence_class\n"
        "T:1,Test node,canal,water_level,1.1,m,2026-09-30T08:00:00,Asia/Bangkok,OFFICIAL_REPORTED\n"
        "T:1,Test node,canal,water_level,1.2,m,2026-09-30T09:00:00,Asia/Bangkok,official\n"
        "T:1,Test node,canal,water_level,1.3,m,2026-09-30T10:00:00,,OFFICIAL_REPORTED\n",
        encoding="utf-8")
    r = run_source(settings, "traffy_bangkok", manual=True, paths=[p])
    assert r.status is IngestStatus.PARTIAL
    assert r.records_inserted == 1 and r.records_failed == 2
    with sessions() as s:
        m = s.query(Measurement).one()
        assert m.measurement_at.astimezone(UTC).hour == 1
        assert "MANUAL_TRANSCRIPTION" in m.quality_flag
