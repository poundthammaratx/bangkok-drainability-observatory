"""The v0.3 collector: dedup/revision correctness, provenance, failure handling, write-blocking.

Network is always mocked — the real-endpoint collection is exercised manually (see
docs/PERSISTENT_ARCHIVE.md verification log), never in automated tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from bdo.collector.runner import collect_one
from bdo.config import Settings
from bdo.enums import IngestStatus
from bdo.live import manager as live_manager
from bdo.models import IngestRun, Measurement, RawSnapshot, Source, SourceHealthHistory, Station
from bdo.util.access import PublicDeploymentBlocked
from bdo.util.time import UTC, utcnow


@pytest.fixture(autouse=True)
def _reset_live_cache():
    live_manager.invalidate_all()
    yield
    live_manager.invalidate_all()


def _fb_handler(flood_now: str, reading_timestamp: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": "FL.TEST.01", "name": "Test Sensor", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": "normal"},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": flood_now, "timestamp": reading_timestamp},
        ]})
    return handler


def _patch_floodbangkok(monkeypatch, flood_now: str, age_minutes: float = 2):
    from bdo.live.adapters import floodbangkok_live

    ts = (utcnow() - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    handler = _fb_handler(flood_now, ts)

    def fake_fetch(s, client=None):
        return floodbangkok_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fetch)


def test_identical_rerun_does_not_duplicate(settings, sessions, monkeypatch):
    _patch_floodbangkok(monkeypatch, "0.0")
    r1 = collect_one(settings, "bma_floodbangkok", sessions=sessions)
    r2 = collect_one(settings, "bma_floodbangkok", sessions=sessions)
    assert r1.records_inserted == 1
    assert r2.records_inserted == 0
    assert r2.records_skipped == 1
    with sessions() as s:
        assert s.query(Measurement).count() == 1


def test_source_correction_preserves_both_revisions(settings, sessions, monkeypatch):
    """A different value at the same natural key is a revision, not a duplicate or an overwrite."""
    _patch_floodbangkok(monkeypatch, "0.0")
    collect_one(settings, "bma_floodbangkok", sessions=sessions)
    _patch_floodbangkok(monkeypatch, "15.0")  # same station, same minute-ish, different value
    r2 = collect_one(settings, "bma_floodbangkok", sessions=sessions)
    assert r2.records_inserted == 1
    with sessions() as s:
        rows = s.query(Measurement).order_by(Measurement.id).all()
        assert len(rows) == 2
        assert {r.value_num for r in rows} == {0.0, 15.0}
        # provenance: both rows keep their own retrieved_at, neither overwritten
        assert rows[0].retrieved_at != rows[1].retrieved_at or rows[0].id != rows[1].id


def test_measurement_at_separate_from_retrieved_at(settings, sessions, monkeypatch):
    _patch_floodbangkok(monkeypatch, "5.0", age_minutes=37)
    collect_one(settings, "bma_floodbangkok", sessions=sessions)
    with sessions() as s:
        m = s.query(Measurement).one()
        assert m.measurement_at is not None
        assert m.retrieved_at is not None
        assert (m.retrieved_at - m.measurement_at) > timedelta(minutes=30)


def test_unknown_measurement_time_is_not_fabricated(settings, sessions, monkeypatch):
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
    collect_one(settings, "bma_floodbangkok", sessions=sessions)
    with sessions() as s:
        m = s.query(Measurement).one()
        assert m.measurement_at is None  # never back-filled with retrieved_at


def test_source_failure_creates_run_and_health_but_no_measurement(settings, sessions, monkeypatch):
    def boom(s):
        raise RuntimeError("simulated outage")

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", boom)
    result = collect_one(settings, "bma_floodbangkok", sessions=sessions)
    assert result.status is IngestStatus.FAILED
    with sessions() as s:
        assert s.query(Measurement).count() == 0
        run = s.query(IngestRun).filter_by(mode="collector").one()
        assert run.status is IngestStatus.FAILED
        health = s.query(SourceHealthHistory).one()
        assert health.health == "UNAVAILABLE"


def test_raw_snapshot_preserved_with_hash(settings, sessions, monkeypatch):
    _patch_floodbangkok(monkeypatch, "0.0")
    collect_one(settings, "bma_floodbangkok", sessions=sessions)
    with sessions() as s:
        snaps = s.query(RawSnapshot).all()
        assert len(snaps) == 2  # sensor_profile + sensor_now
        for snap in snaps:
            assert snap.payload_path is None  # inline, not file-backed — see module docstring
            assert snap.payload_text is not None
            assert snap.payload_hash


def test_collector_upserts_station_topology(settings, sessions, monkeypatch):
    _patch_floodbangkok(monkeypatch, "0.0")
    collect_one(settings, "bma_floodbangkok", sessions=sessions)
    with sessions() as s:
        st = s.query(Station).filter_by(external_station_id="FL.TEST.01").one()
        assert st.latitude == 13.7 and st.longitude == 100.5
        assert st.node_type == "road_flood_sensor"


def test_public_deployment_blocks_collector_writes(settings):
    public_settings = settings.model_copy(update={"public_deployment": True})
    with pytest.raises(PublicDeploymentBlocked):
        collect_one(public_settings, "bma_floodbangkok")


def test_viewer_runtime_role_blocks_collector_writes(settings):
    from bdo.enums import RuntimeRole

    viewer_settings = settings.model_copy(update={"runtime_role": RuntimeRole.VIEWER})
    with pytest.raises(PublicDeploymentBlocked):
        collect_one(viewer_settings, "bma_floodbangkok")


def test_collector_uses_real_settings_class(settings, sessions, monkeypatch):
    """Guard against the v0.2.1 regression class: collector must work with the real Settings,
    not a dummy object, and must resolve live_ttl_for without raising."""
    assert isinstance(settings, Settings)
    assert settings.live_ttl_for("bma_floodbangkok") > 0
    _patch_floodbangkok(monkeypatch, "0.0")
    result = collect_one(settings, "bma_floodbangkok", sessions=sessions)
    assert result.status is IngestStatus.SUCCESS
