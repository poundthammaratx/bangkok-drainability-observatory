"""The shared current-state resolver (milestone v0.3 §17A0) — the one place Overview, Map and
Live Situation all get "what is current" from. Network is mocked throughout.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from bdo.analytics.current_state import (
    live_only_observations,
    resolve_current_state,
    resolve_public_current_state,
)
from bdo.live import manager as live_manager
from bdo.util.time import utcnow


@pytest.fixture(autouse=True)
def _reset_live_cache():
    live_manager.invalidate_all()
    yield
    live_manager.invalidate_all()


def _fb_handler(flood_now: str, reading_timestamp: str, code: str = "FL.TEST.01"):
    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": code, "name": "Test Sensor", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": "normal"},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": flood_now, "timestamp": reading_timestamp},
        ]})
    return handler


def _patch_floodbangkok(monkeypatch, flood_now: str, age_minutes: float):
    from bdo.live.adapters import floodbangkok_live

    ts = (utcnow() - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    handler = _fb_handler(flood_now, ts)

    def fake_fetch(s, client=None):
        return floodbangkok_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fetch)


def test_live_only_when_nothing_persisted(settings, sessions, monkeypatch):
    _patch_floodbangkok(monkeypatch, "0.0", age_minutes=2)
    live_states = {"bma_floodbangkok": live_manager.get_live_state(settings, "bma_floodbangkok", force=True)}
    with sessions() as s:
        resolved = resolve_current_state(s, settings, live_states)
    rows = [r for r in resolved if r.source_key == "bma_floodbangkok"]
    assert len(rows) == 1
    assert rows[0].persistence == "live_only"


def test_persisted_and_live_merge_prefers_newer_measurement(settings, sessions, monkeypatch):
    """Collect an old reading, then resolve against a newer live reading for the same station —
    the newer live value must win for display, without touching the persisted row."""
    from bdo.collector.runner import collect_one

    _patch_floodbangkok(monkeypatch, "5.0", age_minutes=120)
    collect_one(settings, "bma_floodbangkok", sessions=sessions)

    _patch_floodbangkok(monkeypatch, "20.0", age_minutes=1)
    live_manager.invalidate_all()
    live_states = {"bma_floodbangkok": live_manager.get_live_state(settings, "bma_floodbangkok", force=True)}

    with sessions() as s:
        resolved = resolve_current_state(s, settings, live_states)
    rows = [r for r in resolved if r.source_key == "bma_floodbangkok"]
    assert len(rows) == 1
    assert rows[0].value_num == 20.0
    assert rows[0].persistence == "persisted+live"

    # the persisted archive itself is untouched — still just the one old row
    from bdo.models import Measurement
    with sessions() as s:
        persisted = s.query(Measurement).all()
        assert len(persisted) == 1
        assert persisted[0].value_num == 5.0


def test_persisted_wins_when_live_is_older_or_unavailable(settings, sessions, monkeypatch):
    from bdo.collector.runner import collect_one

    _patch_floodbangkok(monkeypatch, "5.0", age_minutes=1)
    collect_one(settings, "bma_floodbangkok", sessions=sessions)

    def boom(s):
        raise RuntimeError("simulated outage")

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", boom)
    live_manager.invalidate_all()
    live_states = {"bma_floodbangkok": live_manager.get_live_state(settings, "bma_floodbangkok", force=True)}

    with sessions() as s:
        resolved = resolve_current_state(s, settings, live_states)
    rows = [r for r in resolved if r.source_key == "bma_floodbangkok"]
    assert len(rows) == 1
    assert rows[0].value_num == 5.0
    assert rows[0].persistence == "persisted"


def test_unresolved_observation_stays_unknown(settings, sessions, monkeypatch):
    from bdo.live.adapters import floodbangkok_live

    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": "FL.TEST.01", "name": "Test", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": "normal"},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": "0.0", "timestamp": None},
        ]})

    def fake_fetch(s, client=None):
        return floodbangkok_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fetch)
    live_states = {"bma_floodbangkok": live_manager.get_live_state(settings, "bma_floodbangkok", force=True)}
    with sessions() as s:
        resolved = resolve_current_state(s, settings, live_states)
    rows = [r for r in resolved if r.source_key == "bma_floodbangkok"]
    assert rows[0].measurement_at is None
    assert rows[0].freshness.value == "UNKNOWN"


def test_seed_demo_excluded_from_public_current_state(seeded, settings, sessions):
    """The exact regression this wrapper exists to prevent — see
    bdo.analytics.current_state.resolve_public_current_state's docstring."""
    with sessions() as s:
        raw = resolve_current_state(s, settings, {})
        public = resolve_public_current_state(s, settings, {})
    assert any(r.demo for r in raw)  # sanity: the seeded fixture really does load demo rows
    assert not any(r.demo for r in public)
    assert len(public) < len(raw)


def test_live_only_observations_never_demo(settings, monkeypatch):
    _patch_floodbangkok(monkeypatch, "0.0", age_minutes=2)
    live_states = {"bma_floodbangkok": live_manager.get_live_state(settings, "bma_floodbangkok", force=True)}
    rows = live_only_observations(settings, live_states)
    assert rows and not any(r.demo for r in rows)
