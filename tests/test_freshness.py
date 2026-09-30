"""Freshness is a function of measurement_at only; seed data is never LIVE later."""

from datetime import datetime, timedelta

from bdo.analytics.freshness import assess, classify_age, freshness_age
from bdo.config import FreshnessThresholds
from bdo.enums import FreshnessLabel
from bdo.models import Measurement
from bdo.util.time import UTC

TH = FreshnessThresholds(live_minutes=60, recent_minutes=360, stale_minutes=1440)
MEASURED = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)       # 08:00 ICT


def test_freshness_uses_measurement_time_not_retrieval_time():
    now = datetime(2026, 9, 30, 15, 40, tzinfo=UTC)
    retrieved = now - timedelta(minutes=5)                   # just retrieved...
    fr = assess(MEASURED, retrieved, TH, now=now)            # ...but measured 14 h 40 min ago
    assert fr.age == now - MEASURED
    assert fr.label is FreshnessLabel.STALE
    assert fr.age_at_retrieval == retrieved - MEASURED


def test_unknown_measurement_time_is_unknown_not_fresh():
    fr = assess(None, datetime(2026, 9, 30, 15, 0, tzinfo=UTC), TH, now=datetime(2026, 9, 30, 15, 1, tzinfo=UTC))
    assert fr.label is FreshnessLabel.UNKNOWN and fr.age is None


def test_future_timestamp_is_not_live():
    now = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    assert classify_age(freshness_age(MEASURED, now), TH) is FreshnessLabel.UNKNOWN


def test_label_boundaries():
    assert classify_age(timedelta(minutes=60), TH) is FreshnessLabel.LIVE
    assert classify_age(timedelta(minutes=61), TH) is FreshnessLabel.RECENT
    assert classify_age(timedelta(minutes=1441), TH) is FreshnessLabel.VERY_STALE


def test_seed_data_never_live_at_later_date(seeded, settings, sessions):
    with sessions() as s:
        rows = [(m, m.source.source_key) for m in s.query(Measurement).all()]
    later_times = [
        datetime(2026, 9, 30, 1, 5, tzinfo=UTC),    # 5 min after the RID bulletin time
        datetime(2026, 9, 30, 6, 5, tzinfo=UTC),    # 4 min after FloodBangkok dashboard time
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
    ]
    for now in later_times:
        for m, key in rows:
            if m.measurement_at > now:
                continue
            fr = assess(m.measurement_at, m.retrieved_at, settings.thresholds_for(key), m.quality_flag, now=now)
            assert fr.is_demonstration
            assert fr.label is not FreshnessLabel.LIVE, (m.variable, now)
    # a day later every seed record reflects its true age
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    for m, key in rows:
        fr = assess(m.measurement_at, m.retrieved_at, settings.thresholds_for(key), m.quality_flag, now=now)
        assert fr.label in (FreshnessLabel.STALE, FreshnessLabel.VERY_STALE)
        assert fr.age == now - m.measurement_at
