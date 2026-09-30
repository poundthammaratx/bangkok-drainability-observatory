"""Freshness: age is always ``now - measurement_at`` — never ``now - retrieved_at``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bdo.config import FreshnessThresholds
from bdo.enums import FreshnessLabel, QualityFlag, split_flags
from bdo.util.time import ensure_utc, utcnow


@dataclass(frozen=True)
class Freshness:
    label: FreshnessLabel
    age: timedelta | None               # now - measurement_at
    age_at_retrieval: timedelta | None  # retrieved_at - measurement_at
    is_demonstration: bool
    future_timestamp: bool


def freshness_age(measurement_at: datetime | None, now: datetime | None = None) -> timedelta | None:
    if measurement_at is None:
        return None
    now = ensure_utc(now, "UTC") if now else utcnow()
    return now - ensure_utc(measurement_at, "UTC")


def age_at_retrieval(measurement_at: datetime | None, retrieved_at: datetime | None) -> timedelta | None:
    if measurement_at is None or retrieved_at is None:
        return None
    return ensure_utc(retrieved_at, "UTC") - ensure_utc(measurement_at, "UTC")


def classify_age(age: timedelta | None, thresholds: FreshnessThresholds) -> FreshnessLabel:
    if age is None:
        return FreshnessLabel.UNKNOWN
    if age < timedelta(0):
        # A timestamp in the future is not "fresh"; it is an error in the source or our parse.
        return FreshnessLabel.UNKNOWN
    minutes = age.total_seconds() / 60
    if minutes <= thresholds.live_minutes:
        return FreshnessLabel.LIVE
    if minutes <= thresholds.recent_minutes:
        return FreshnessLabel.RECENT
    if minutes <= thresholds.stale_minutes:
        return FreshnessLabel.STALE
    return FreshnessLabel.VERY_STALE


def assess(
    measurement_at: datetime | None,
    retrieved_at: datetime | None,
    thresholds: FreshnessThresholds,
    quality_flag: str | None = None,
    now: datetime | None = None,
) -> Freshness:
    age = freshness_age(measurement_at, now)
    label = classify_age(age, thresholds)
    is_demo = QualityFlag.SEED_DEMONSTRATION.value in split_flags(quality_flag)
    # Demonstration / seed data is historical by construction and may never be presented as LIVE,
    # whatever its nominal age. Its true age is still reported.
    if is_demo and label is FreshnessLabel.LIVE:
        label = FreshnessLabel.RECENT
    return Freshness(
        label=label,
        age=age,
        age_at_retrieval=age_at_retrieval(measurement_at, retrieved_at),
        is_demonstration=is_demo,
        future_timestamp=age is not None and age < timedelta(0),
    )
