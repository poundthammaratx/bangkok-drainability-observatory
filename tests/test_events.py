"""Recent-change events (§17C Zone 5) — only recorded facts, never invented transitions."""

from __future__ import annotations

from datetime import timedelta

import httpx

from bdo.analytics.events import recent_events
from bdo.live import manager as live_manager
from bdo.util.time import utcnow


def _fb_handler(flood_now: str, device_status: str, reading_timestamp: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": "FL.TEST.01", "name": "Test", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": device_status},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": flood_now, "timestamp": reading_timestamp},
        ]})
    return handler


def _collect_with_status(settings, sessions, monkeypatch, flood_now: str, device_status: str, age_minutes: float = 2):
    from bdo.collector.runner import collect_one
    from bdo.live.adapters import floodbangkok_live

    ts = (utcnow() - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    handler = _fb_handler(flood_now, device_status, ts)

    def fake_fetch(s, client=None):
        return floodbangkok_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fetch)
    live_manager.invalidate_all()
    return collect_one(settings, "bma_floodbangkok", sessions=sessions)


def test_no_events_when_nothing_changed(settings, sessions, monkeypatch):
    _collect_with_status(settings, sessions, monkeypatch, "0.0", "normal")
    _collect_with_status(settings, sessions, monkeypatch, "0.0", "normal")
    with sessions() as s:
        events = recent_events(s, utcnow() - timedelta(hours=1), utcnow() + timedelta(minutes=1))
    station_events = [e for e in events if e.kind == "station_status"]
    assert station_events == []


def test_station_status_transition_is_recorded(settings, sessions, monkeypatch):
    _collect_with_status(settings, sessions, monkeypatch, "0.0", "normal")
    _collect_with_status(settings, sessions, monkeypatch, "15.0", "flooding")
    with sessions() as s:
        events = recent_events(s, utcnow() - timedelta(hours=1), utcnow() + timedelta(minutes=1))
    station_events = [e for e in events if e.kind == "station_status"]
    assert len(station_events) == 1
    assert "normal" in station_events[0].text and "flooding" in station_events[0].text


def test_source_health_transition_is_recorded(settings, sessions, monkeypatch):
    _collect_with_status(settings, sessions, monkeypatch, "0.0", "normal")

    def boom(s):
        raise RuntimeError("simulated outage")

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", boom)
    live_manager.invalidate_all()
    from bdo.collector.runner import collect_one
    collect_one(settings, "bma_floodbangkok", sessions=sessions)

    with sessions() as s:
        events = recent_events(s, utcnow() - timedelta(hours=1), utcnow() + timedelta(minutes=1))
    health_events = [e for e in events if e.kind == "source_health"]
    assert len(health_events) == 1
    assert "UNAVAILABLE" in health_events[0].text


def test_events_outside_window_are_excluded(settings, sessions, monkeypatch):
    _collect_with_status(settings, sessions, monkeypatch, "0.0", "normal")
    _collect_with_status(settings, sessions, monkeypatch, "15.0", "flooding")
    with sessions() as s:
        # a window entirely in the past, before either collection happened
        events = recent_events(s, utcnow() - timedelta(days=2), utcnow() - timedelta(days=1))
    assert events == []
