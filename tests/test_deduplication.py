"""Deterministic deduplication."""

from datetime import datetime, timedelta

from bdo.ingestion.runner import run_source
from bdo.models import Measurement, RawSnapshot
from bdo.util.time import UTC

REC = [
    {"variable": "water_level", "value": 1.20, "unit": "m", "t": "2026-09-30T08:00:00+07:00", "station": "S1"},
    {"variable": "water_level", "value": 1.35, "unit": "m", "t": "2026-09-30T09:00:00+07:00", "station": "S1"},
]


def test_duplicate_ingestion_creates_no_duplicate_measurements(fake_adapter_factory, settings, sessions):
    t1 = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)
    a = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory(REC, retrieved_at=t1))
    # same readings retrieved again an hour later: a new snapshot, but no new measurements
    b = run_source(settings, "thaiwater_bangkok",
                   adapter=fake_adapter_factory(REC, retrieved_at=t1 + timedelta(hours=1)))
    assert (a.records_inserted, b.records_inserted, b.records_skipped) == (2, 0, 2)
    with sessions() as s:
        assert s.query(Measurement).count() == 2
        assert s.query(RawSnapshot).count() == 2          # each retrieval is still archived
        assert {m.retrieved_at for m in s.query(Measurement)} == {t1}  # first retrieval kept


def test_equal_values_in_different_representation_are_duplicates(fake_adapter_factory, settings, sessions):
    run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([dict(REC[0], value=1.2)]))
    r = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([dict(REC[0], value=1.2000000000001)]))
    assert r.records_skipped == 1


def test_within_batch_duplicates_are_skipped(fake_adapter_factory, settings, sessions):
    r = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([REC[0], REC[0]]))
    assert (r.records_inserted, r.records_skipped) == (1, 1)


def test_revised_value_same_time_is_kept_as_conflict(fake_adapter_factory, settings, sessions):
    run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([REC[0]]))
    r = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([dict(REC[0], value=1.25)]))
    assert r.records_inserted == 1
    with sessions() as s:
        assert s.query(Measurement).count() == 2


def test_different_station_or_variable_not_duplicate(fake_adapter_factory, settings, sessions):
    recs = [REC[0], dict(REC[0], station="S2"), dict(REC[0], variable="water_level_max")]
    r = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory(recs))
    assert r.records_inserted == 3


def test_undated_records_distinguished_by_source_record_id(fake_adapter_factory, settings, sessions):
    recs = [{"variable": "status", "text": "flooded", "rid": "a"}, {"variable": "status", "text": "flooded", "rid": "b"}]
    r1 = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory(recs))
    r2 = run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory(recs))
    assert (r1.records_inserted, r2.records_inserted, r2.records_skipped) == (2, 0, 2)


def test_seed_is_idempotent(seeded, settings, sessions):
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    for key in ("rid_water_situation", "bma_floodbangkok"):
        files = sorted((ROOT / "data" / "manual" / key).glob("SEED_*.csv"))
        r = run_source(settings, key, manual=True, paths=files, mode="seed")
        assert r.records_inserted == 0
    with sessions() as s:
        assert s.query(Measurement).count() == 13
        assert s.query(RawSnapshot).count() == 2
