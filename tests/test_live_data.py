"""Live read-through data plane — network is always mocked (see docs/LIVE_DATA_ARCHITECTURE.md).

Real endpoint shapes were captured by manual inspection on 2026-09-30 (see the adapter docstrings
and docs/SOURCE_ENDPOINTS.md); these tests replay small fixture payloads through
``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from bdo.config import load_settings
from bdo.enums import FreshnessLabel, QualityFlag
from bdo.live import manager as live_manager
from bdo.live.adapters import floodbangkok_live
from bdo.live.base import LiveHealth, LiveSourceState
from bdo.repository import reference as ref_repo
from bdo.ui.map_view import build_points
from bdo.util.access import PublicDeploymentBlocked, assert_writes_allowed
from bdo.util.time import UTC, utcnow


@pytest.fixture(autouse=True)
def _reset_live_cache():
    live_manager.invalidate_all()
    yield
    live_manager.invalidate_all()


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fb_handler(reading_timestamp: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": "FL.TEST.01", "name": "Test Sensor", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": "normal"},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": "5.0", "timestamp": reading_timestamp},
        ]})
    return handler


def test_fresh_reading_is_healthy_and_live():
    fresh_ts = (utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    settings = load_settings()
    state = floodbangkok_live.fetch(settings, client=_mock_client(_fb_handler(fresh_ts)))
    assert state.health is LiveHealth.HEALTHY
    assert state.freshness.value == "LIVE"
    assert state.record_count == 1
    assert state.measurements[0].value_num == 5.0


def test_http_200_with_old_timestamp_is_not_considered_live():
    """The milestone's core honesty rule: HTTP success alone never implies HEALTHY/LIVE."""
    old_ts = (utcnow() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
    settings = load_settings()
    state = floodbangkok_live.fetch(settings, client=_mock_client(_fb_handler(old_ts)))
    assert state.http_status == 200
    assert state.health is LiveHealth.STALE
    assert state.freshness.value in ("STALE", "VERY_STALE")
    assert state.health is not LiveHealth.HEALTHY


def test_stale_data_marked_stale_not_unknown_or_live():
    old_ts = (utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
    settings = load_settings()
    state = floodbangkok_live.fetch(settings, client=_mock_client(_fb_handler(old_ts)))
    assert state.health is LiveHealth.STALE


def test_source_timestamp_stays_separate_from_fetch_timestamp():
    """A reading measured 5 minutes ago, fetched 'now', must report both distinctly."""
    fresh_ts = (utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    settings = load_settings()
    before = utcnow()
    state = floodbangkok_live.fetch(settings, client=_mock_client(_fb_handler(fresh_ts)))
    assert state.fetch_finished_at >= before
    assert state.source_measurement_at is not None
    assert state.source_measurement_at < state.fetch_finished_at - timedelta(minutes=4)


def test_live_source_failure_returns_unavailable_not_an_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure", request=request)

    settings = load_settings()
    state = floodbangkok_live.fetch(settings, client=_mock_client(handler))
    assert state.health is LiveHealth.UNAVAILABLE
    assert state.error
    assert state.record_count == 0


def test_manager_falls_back_to_last_known_good_on_failure(monkeypatch):
    calls = {"n": 0}
    good = LiveSourceState(
        source_key="bma_floodbangkok", endpoint="mock://test", fetch_started_at=utcnow(),
        fetch_finished_at=utcnow(), http_status=200, health=LiveHealth.HEALTHY, record_count=1,
        source_measurement_at=utcnow(), freshness=FreshnessLabel.LIVE,
    )

    def flaky(settings):
        calls["n"] += 1
        if calls["n"] == 1:
            return good
        raise RuntimeError("simulated outage")

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", flaky)
    settings = load_settings()
    first = live_manager.get_live_state(settings, "bma_floodbangkok", force=True)
    assert first.health is LiveHealth.HEALTHY
    second = live_manager.get_live_state(settings, "bma_floodbangkok", force=True)
    assert second.health is LiveHealth.DEGRADED  # not UNAVAILABLE and not silently dropped
    assert second.record_count == 1
    assert "simulated outage" in (second.error or "")


def test_live_measurements_never_carry_seed_demonstration_flag():
    fresh_ts = (utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    settings = load_settings()
    state = floodbangkok_live.fetch(settings, client=_mock_client(_fb_handler(fresh_ts)))
    for m in state.measurements:
        assert QualityFlag.SEED_DEMONSTRATION.value not in m.quality_flags


def test_reference_topology_loads_offline_with_coordinates():
    """The packaged static registry (data/reference/) needs no network and no database."""
    topo = ref_repo.load_coordinate_topology()
    assert len(topo) > 0
    assert topo["latitude"].notna().all()
    assert topo["longitude"].notna().all()


def test_map_has_packaged_points_on_a_fresh_empty_database(settings, sessions):
    """A fresh PUBLIC_DEPLOYMENT container: empty DB, live sources all offline."""
    with sessions() as s:
        points = build_points(s, settings, live_states={})
    assert len(points) > 0
    assert points["point_source"].eq("bkk_open_data_dds").all()  # only the static layer, offline
    assert points["latitude"].notna().all()


def test_live_source_failure_does_not_break_map(settings, sessions, monkeypatch):
    unavailable_states = {
        key: LiveSourceState(
            source_key=key, endpoint=None, fetch_started_at=utcnow(), fetch_finished_at=utcnow(),
            http_status=None, health=LiveHealth.UNAVAILABLE, record_count=0,
            source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN, error="simulated outage",
        )
        for key in live_manager.LIVE_SOURCE_KEYS
    }
    with sessions() as s:
        points = build_points(s, settings, live_states=unavailable_states)
    assert len(points) > 0  # falls back to static topology, never an empty/broken map


def test_public_deployment_permits_read_only_get_but_blocks_writes():
    public_settings = load_settings(public_deployment=True)
    fresh_ts = (utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    # live read-through GET must still work under PUBLIC_DEPLOYMENT=true
    state = floodbangkok_live.fetch(public_settings, client=_mock_client(_fb_handler(fresh_ts)))
    assert state.health is LiveHealth.HEALTHY
    # but any persistence write is blocked
    with pytest.raises(PublicDeploymentBlocked):
        assert_writes_allowed(public_settings, "ingestion")
