"""Provenance: raw payloads survive, every record is traceable, CKAN adapter end-to-end (offline)."""

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from bdo.enums import EvidenceClass, IngestStatus, QualityFlag, ValidationStatus
from bdo.ingestion.adapters.bkk_ckan import CKANAdapter
from bdo.ingestion.runner import run_source
from bdo.models import IngestRun, Measurement, RawSnapshot, Source, Station
from bdo.repository import snapshots as snap_repo
from bdo.schemas import RawPayload
from bdo.util.time import UTC

from tests_helpers import ckan_adapter


# -------------------------------------------------------------------------------------------------
def test_normalization_failure_preserves_raw_snapshot(fake_adapter_factory, settings, sessions):
    ad = fake_adapter_factory([{"variable": "x", "value": 1, "t": "2026-09-30T08:00:00+07:00"}], fail_normalize=True)
    summary = run_source(settings, "thaiwater_bangkok", adapter=ad)
    assert summary.status is IngestStatus.FAILED
    assert "normalization failed" in summary.message and "raw snapshots preserved" in summary.message
    with sessions() as s:
        snaps = s.query(RawSnapshot).all()
        assert len(snaps) == 1
        assert snap_repo.verify(settings, snaps[0])            # file on disk, hash matches
        assert s.query(Measurement).count() == 0
        run = s.get(IngestRun, summary.run_id)
        assert run.status is IngestStatus.FAILED and run.snapshots_saved == 1


def test_snapshot_files_are_never_overwritten(settings, sessions):
    t = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    with sessions() as s:
        src = s.query(Source).filter_by(source_key="thaiwater_bangkok").one()
        p = RawPayload(content=b"original", content_type="text/plain", label="x", extension="txt")
        snap = snap_repo.save_snapshot(s, settings, src, p, t)
        s.commit()
        path = snap_repo.absolute_path(settings, snap)
        assert path.parts[-4:-1] == ("2026", "09", "30")
        with pytest.raises(FileExistsError):
            snap_repo.save_snapshot(s, settings, src, p, t)
        assert path.read_bytes() == b"original"


def test_every_seed_measurement_traceable(seeded, settings, sessions):
    with sessions() as s:
        for m in s.query(Measurement).all():
            assert m.source_id is not None and m.evidence_class is EvidenceClass.OFFICIAL_REPORTED
            assert m.raw_snapshot_id is not None and m.ingest_run_id is not None
            snap = s.get(RawSnapshot, m.raw_snapshot_id)
            assert snap_repo.verify(settings, snap)
            assert b"SEED / DEMONSTRATION" in snap_repo.read_payload(settings, snap)
            assert QualityFlag.SEED_DEMONSTRATION.value in m.quality_flag
        assert s.query(Station).filter(Station.latitude.is_not(None)).count() == 0  # no invented coordinates


def test_shell_adapters_do_not_fabricate(settings, sessions):
    for key in ("bma_floodbangkok", "tmd_bangkok_radar", "thaiwater_bangkok", "rid_water_situation"):
        r = run_source(settings, key)
        assert r.status is IngestStatus.SKIPPED and r.records_inserted == 0 and r.snapshots_saved == 0


# -------------------------------------------------------------------------------------------------
# CKAN (offline, MockTransport, verbatim records from the 2026-09-30 inspection)
# -------------------------------------------------------------------------------------------------
def test_ckan_end_to_end_with_mirror_fallback(settings, sessions):
    ad = ckan_adapter(settings, fail_host="data.bangkok.go.th")
    summary = run_source(settings, "bkk_open_data_dds", adapter=ad)
    assert summary.status is IngestStatus.SUCCESS, summary.message
    # pages: telemetry 1, dds011 4 (17 records / 5), frd 1  + 3 package_show
    assert summary.snapshots_saved == 9
    assert summary.records_inserted == 17 + 3 * 5

    with sessions() as s:
        snaps = s.query(RawSnapshot).all()
        assert all(sn.request_url.startswith("https://data.go.th/") for sn in snaps)  # host actually used
        tel = s.query(Station).filter(Station.external_station_id.like("WL.%")).all()
        assert len(tel) == 5
        for st in tel:
            assert st.latitude is not None and st.coordinate_evidence_class is EvidenceClass.OFFICIAL_REPORTED
            assert st.node_type == "water_level_station" and st.validation_status is ValidationStatus.TYPE_INFERRED
            assert st.source_type_code == "4"
        pkg = s.query(Station).filter_by(external_station_id="WL.PKG.01").one()
        assert (pkg.latitude, pkg.longitude) == (13.74228, 100.49454)

        wl = (s.query(Measurement).filter_by(variable="water_level_daily_max")
              .order_by(Measurement.measurement_at).all())
        assert len(wl) == 17
        times = [m.measurement_at for m in wl]
        assert times == sorted(times)
        rec2 = next(m for m in wl if m.external_record_id == "dds011_pak_khlong_talat_wl_max:2")
        assert rec2.measurement_at == datetime(2023, 1, 2, 6, 0, tzinfo=UTC)   # 13:00 ICT, 2 Jan
        assert QualityFlag.DATE_REPAIRED_DM_SWAP.value in rec2.quality_flag
        assert QualityFlag.TZ_DECLARED_BY_CONFIG.value in rec2.quality_flag
        assert QualityFlag.UNIT_DECLARED_UNVERIFIED.value in rec2.quality_flag
        assert "as-published date 2023-02-01" in rec2.notes
        assert rec2.evidence_class is EvidenceClass.OFFICIAL_REPORTED

        # record-level traceability: the referenced page contains the source record
        for m in wl:
            body = json.loads(snap_repo.read_payload(settings, s.get(RawSnapshot, m.raw_snapshot_id)))
            ids = {r["_id"] for r in body["result"]["records"]}
            assert int(m.external_record_id.split(":")[-1]) in ids

        frd = s.query(Measurement).filter_by(variable="road_flood_depth_event_max").all()
        assert len(frd) == 3 and {m.unit for m in frd} == {"cm"}
        first = next(m for m in frd if m.external_record_id.endswith(":1"))
        assert first.measurement_at == datetime(2023, 4, 30, 1, 30, tzinfo=UTC)   # 08:30 ICT
        dur = s.query(Measurement).filter_by(variable="road_flood_duration").first()
        assert dur.value_text is not None and dur.value_num is None


def test_ckan_reingest_is_deduplicated(settings, sessions):
    run_source(settings, "bkk_open_data_dds", adapter=ckan_adapter(settings))
    r2 = run_source(settings, "bkk_open_data_dds", adapter=ckan_adapter(settings))
    assert r2.records_inserted == 0 and r2.records_skipped == 32
    with sessions() as s:
        assert s.query(Measurement).count() == 32


def test_ckan_unreachable_marks_source_unavailable(settings, sessions):
    cfg = settings.source("bkk_open_data_dds")

    def down(request):
        raise httpx.ConnectError("down", request=request)

    ad = CKANAdapter(cfg, settings, client=httpx.Client(transport=httpx.MockTransport(down)))
    r = run_source(settings, "bkk_open_data_dds", adapter=ad)
    assert r.status is IngestStatus.SKIPPED
    with sessions() as s:
        assert s.query(Source).filter_by(source_key="bkk_open_data_dds").one().status.value == "UNAVAILABLE"
        assert s.query(Measurement).count() == 0
