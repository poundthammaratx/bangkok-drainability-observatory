"""Public Alpha safety guarantees.

* SEED/DEMONSTRATION records never appear in "current verified state".
* Stale (or worse) records never carry the LIVE label.
* A record with UNKNOWN measurement time never carries the LIVE label.
* "Current verified state" is computed from measurement_at, not retrieved_at.
* PUBLIC_DEPLOYMENT=true blocks the write-capable entry points (ingestion, field-obs import).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bdo.config import load_settings
from bdo.enums import FreshnessLabel
from bdo.ingestion.runner import run_source
from bdo.ui import components as c
from bdo.ui.overview import current_verified_state
from bdo.util.access import PublicDeploymentBlocked, assert_writes_allowed
from bdo.util.time import UTC


def test_seed_records_never_in_current_verified_state(seeded, settings, sessions):
    with sessions() as s:
        meas = c.measurements_df(s)
    assert meas["demo"].any()  # sanity: seed data is present
    verified = current_verified_state(meas)
    assert verified.empty


def test_stale_records_never_labelled_live(seeded, settings, sessions):
    with sessions() as s:
        meas = c.measurements_df(s)
    stale_or_worse = meas[meas["freshness_label"].isin(["STALE", "VERY_STALE"])]
    assert not stale_or_worse.empty
    assert not (stale_or_worse["freshness"] == "🟢 LIVE").any()


def test_unknown_measurement_time_never_live(settings, sessions, fake_adapter_factory):
    adapter = fake_adapter_factory([{"variable": "water_level", "value": 1.0, "unit": "m"}])  # no "t"
    run_source(settings, "thaiwater_bangkok", adapter=adapter)
    with sessions() as s:
        meas = c.measurements_df(s)
    unknown = meas[meas["measurement_at"].isna()]
    assert not unknown.empty
    assert (unknown["freshness_label"] == FreshnessLabel.UNKNOWN.value).all()
    assert not (unknown["freshness"] == "🟢 LIVE").any()
    assert current_verified_state(meas).empty or not current_verified_state(meas)["measured"].eq("UNKNOWN").any()


def test_current_verified_state_uses_measurement_at_not_retrieved_at(settings, sessions, fake_adapter_factory):
    """Retrieved seconds ago but measured two days ago must not count as current."""
    old_measurement = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    adapter = fake_adapter_factory(
        [{"variable": "water_level", "value": 1.0, "unit": "m", "t": old_measurement}],
        source_key="thaiwater_bangkok",
    )
    run_source(settings, "thaiwater_bangkok", adapter=adapter)  # adapter.retrieved_at defaults to "now"
    with sessions() as s:
        meas = c.measurements_df(s)
    assert current_verified_state(meas).empty


def test_public_deployment_blocks_ingestion():
    s = load_settings(public_deployment=True)
    with pytest.raises(PublicDeploymentBlocked):
        assert_writes_allowed(s, "ingestion")


def test_public_deployment_allows_writes_by_default():
    s = load_settings()
    assert_writes_allowed(s, "ingestion")  # must not raise


def test_public_deployment_blocks_field_observation_import(tmp_path, settings, sessions):
    from bdo.repository.observations import import_csv

    public_settings = settings.model_copy(update={"public_deployment": True})
    csv_path = tmp_path / "obs.csv"
    csv_path.write_text("observed_at,location_name,evidence_class,phenomenon\n", encoding="utf-8")
    with sessions() as session:
        with pytest.raises(PublicDeploymentBlocked):
            import_csv(session, public_settings, csv_path)
