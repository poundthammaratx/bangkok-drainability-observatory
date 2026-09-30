"""Schema-level guarantees."""

from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from bdo.database import get_engine
from bdo.enums import EvidenceClass
from bdo.models import Measurement, Source
from bdo.util.time import UTC


def test_all_tables_created(settings):
    names = set(inspect(get_engine(settings)).get_table_names())
    assert {"sources", "stations", "raw_snapshots", "measurements", "field_observations",
            "ingest_runs", "derived_metrics"} <= names


def test_sources_seeded_from_config(settings, sessions):
    with sessions() as s:
        keys = {k for (k,) in s.execute(text("select source_key from sources"))}
    assert {"bkk_open_data_dds", "bma_floodbangkok", "tmd_bangkok_radar", "thaiwater_bangkok",
            "rid_water_situation", "traffy_bangkok"} <= keys


def _source_id(s):
    return s.query(Source).filter_by(source_key="thaiwater_bangkok").one().id


def test_evidence_class_not_null(settings, sessions):
    with sessions() as s:
        s.add(Measurement(source_id=_source_id(s), variable="x", value_num=1.0, retrieved_at=datetime.now(UTC),
                          evidence_class=None, dedup_key="k1"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_evidence_class_check_constraint_rejects_unknown_string(settings, sessions):
    with sessions() as s:
        sid = _source_id(s)
        with pytest.raises(IntegrityError):
            s.execute(text("insert into measurements (source_id, variable, value_num, retrieved_at, evidence_class, "
                           "dedup_key, created_at) values (:sid, 'x', 1, '2026-01-01 00:00:00', 'GUESSED', 'k2', "
                           "'2026-01-01 00:00:00')"), {"sid": sid})


def test_naive_datetime_rejected(settings, sessions):
    with sessions() as s:
        s.add(Measurement(source_id=_source_id(s), variable="x", value_num=1.0,
                          retrieved_at=datetime(2026, 9, 30, 8, 0), evidence_class=EvidenceClass.OBSERVED,
                          dedup_key="k3"))
        with pytest.raises((StatementError, ValueError)):
            s.commit()


def test_utc_roundtrip(settings, sessions):
    t = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    with sessions() as s:
        s.add(Measurement(source_id=_source_id(s), variable="x", value_num=1.0, measurement_at=t, retrieved_at=t,
                          evidence_class=EvidenceClass.OBSERVED, dedup_key="k4"))
        s.commit()
    with sessions() as s:
        m = s.query(Measurement).filter_by(dedup_key="k4").one()
        assert m.measurement_at == t and m.measurement_at.tzinfo is not None
