"""measurement_at vs retrieved_at, zones, and day/month transposition handling."""

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from bdo.enums import QualityFlag
from bdo.models import Measurement
from bdo.normalization.measurements import resolve_dm_ambiguity
from bdo.schemas import ManualMeasurementRow
from bdo.util.time import UTC, format_duration, format_ts, parse_timestamp


def test_measurement_and_retrieval_times_are_independent(seeded, sessions):
    with sessions() as s:
        rows = s.query(Measurement).all()
    assert len(rows) == 13
    for m in rows:
        assert m.measurement_at is not None and m.retrieved_at is not None
        assert m.measurement_at != m.retrieved_at
    rid = [m for m in rows if m.variable == "flow_rate"]
    # 08:00 ICT = 01:00 UTC, independent of when the seed was ingested
    assert {m.measurement_at for m in rid} == {datetime(2026, 9, 30, 1, 0, tzinfo=UTC)}
    fb = [m for m in rows if m.variable == "flooded_stations"][0]
    assert fb.measurement_at == datetime(2026, 9, 30, 6, 1, tzinfo=UTC)


def test_retrieved_at_from_fetch_not_from_source(fake_adapter_factory, settings, sessions):
    from bdo.ingestion.runner import run_source

    t_meas = "2026-09-30T08:00:00+07:00"
    t_fetch = datetime(2026, 9, 30, 15, 35, tzinfo=UTC)  # 22:35 ICT
    ad = fake_adapter_factory([{"variable": "discharge", "value": 2200, "unit": "m3/s", "t": t_meas}],
                              retrieved_at=t_fetch)
    run_source(settings, "thaiwater_bangkok", adapter=ad)
    with sessions() as s:
        m = s.query(Measurement).one()
    assert m.retrieved_at == t_fetch
    assert m.measurement_at == datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    assert format_duration(m.retrieved_at - m.measurement_at) == "14 h 35 min"


def test_unknown_measurement_time_is_not_backfilled(fake_adapter_factory, settings, sessions):
    from bdo.ingestion.runner import run_source

    run_source(settings, "thaiwater_bangkok", adapter=fake_adapter_factory([{"variable": "note", "text": "x"}]))
    with sessions() as s:
        m = s.query(Measurement).one()
    assert m.measurement_at is None
    assert QualityFlag.MEASUREMENT_TIME_UNKNOWN.value in m.quality_flag


def test_display_format():
    t = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    assert format_ts(t) == "30 Sep 2026 08:00 ICT"
    assert format_ts(None) == "UNKNOWN"
    assert format_duration(timedelta(days=1, hours=2, minutes=5)) == "1 d 2 h 5 min"


def test_naive_timestamp_requires_declared_zone():
    with pytest.raises(ValueError):
        parse_timestamp("2026-09-30T08:00:00")
    assert parse_timestamp("2026-09-30T08:00:00", "Asia/Bangkok") == datetime(2026, 9, 30, 1, 0, tzinfo=UTC)


def test_manual_row_naive_time_without_zone_rejected():
    with pytest.raises(ValidationError):
        ManualMeasurementRow(variable="x", value_num=1, measurement_at="2026-09-30T08:00:00",
                             evidence_class="OFFICIAL_REPORTED")


def test_dm_swap_nearest_forward_on_real_sequence():
    # As published for _id 1..5, 11..13 of dds011 (inspected 2026-09-30)
    published = [date(2023, 1, 1), date(2023, 2, 1), date(2023, 3, 1), date(2023, 4, 1), date(2023, 5, 1),
                 date(2023, 11, 1), date(2023, 12, 1), date(2023, 1, 13)]
    out = resolve_dm_ambiguity(published, "nearest_forward")
    resolved = [d for d, _, _ in out]
    assert resolved == [date(2023, 1, 1), date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5),
                        date(2023, 1, 11), date(2023, 1, 12), date(2023, 1, 13)]
    assert QualityFlag.DATE_REPAIRED_DM_SWAP.value in out[1][1]
    assert out[1][2] and "as-published date 2023-02-01" in out[1][2]
    assert out[7][1] == set()  # day 13 cannot be transposed


def test_dm_swap_keeps_legitimate_month_boundary():
    out = resolve_dm_ambiguity([date(2023, 1, 31), date(2023, 2, 1)], "nearest_forward")
    assert out[1][0] == date(2023, 2, 1) and not out[1][1]


def test_dm_flag_mode_flags_but_never_changes():
    out = resolve_dm_ambiguity([date(2023, 2, 15), date(2023, 3, 5)], "flag")
    assert [d for d, _, _ in out] == [date(2023, 2, 15), date(2023, 3, 5)]
    assert out[0][1] == set() and QualityFlag.DATE_DM_AMBIGUOUS.value in out[1][1]
