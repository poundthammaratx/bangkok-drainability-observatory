"""v0.3 production hardening: CLI backend identity, collector watchdog, registry-vs-runtime-health
separation, and public historical-redistribution gating. Network is always mocked.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from bdo.collector.runner import collect_one
from bdo.collector.status import registry_vs_runtime_health
from bdo.collector.watchdog import FAILED, HEALTHY, NEVER_RUN, STALE, check_source
from bdo.database import CollectorBackendMisconfigured, backend_identity, guard_collector_backend
from bdo.enums import IngestStatus, RuntimeRole, SourceStatus
from bdo.live import manager as live_manager
from bdo.models import IngestRun, Source
from bdo.ui import components as c
from bdo.util.time import utcnow
from zoneinfo import ZoneInfo


def _bangkok_local_now_str() -> str:
    """ThaiWater's timestamps are naive Asia/Bangkok local time (see thaiwater_live.py); a mocked
    reading must be expressed the same way for the fetch to classify it as freshly HEALTHY."""
    return utcnow().astimezone(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------------------------
# 1. COLLECTOR + archive enabled cannot silently use SQLite
# ---------------------------------------------------------------------------------------------

def test_guard_rejects_collector_with_archive_enabled_on_sqlite(settings):
    bad = settings.model_copy(update={"runtime_role": RuntimeRole.COLLECTOR, "archive_enabled": True})
    assert bad.database_url.startswith("sqlite")
    with pytest.raises(CollectorBackendMisconfigured):
        guard_collector_backend(bad)


def test_guard_allows_explicit_development_override(settings):
    allowed = settings.model_copy(update={
        "runtime_role": RuntimeRole.COLLECTOR, "archive_enabled": True, "allow_sqlite_collector": True,
    })
    guard_collector_backend(allowed)  # must not raise


def test_guard_allows_collector_without_archive_enabled(settings):
    """archive_enabled=False (plain local collector testing) is unaffected."""
    dev = settings.model_copy(update={"runtime_role": RuntimeRole.COLLECTOR})
    guard_collector_backend(dev)  # must not raise


def test_collect_one_refuses_sqlite_collector_before_touching_network(settings, sessions, monkeypatch):
    """The guard fires before any fetch — a network call here would mean the guard was bypassed."""
    def boom(s, client=None):
        raise AssertionError("the live adapter must never be called once the guard has fired")

    monkeypatch.setitem(live_manager._ADAPTERS, "thaiwater_bangkok", boom)
    bad = settings.model_copy(update={"runtime_role": RuntimeRole.COLLECTOR, "archive_enabled": True})
    with pytest.raises(CollectorBackendMisconfigured):
        collect_one(bad, "thaiwater_bangkok", sessions=sessions)


# ---------------------------------------------------------------------------------------------
# 2. CLI backend identity never exposes credentials
# ---------------------------------------------------------------------------------------------

def test_backend_identity_never_exposes_credentials(settings):
    leaky = settings.model_copy(update={
        "database_url": "postgresql+psycopg://bdo_collector:s3cr3t-pw@db.example.internal:5432/bdo",
    })
    identity = backend_identity(leaky)
    rendered = identity.render()
    assert "s3cr3t-pw" not in rendered
    assert "bdo_collector" not in rendered
    assert "db.example.internal" not in rendered
    assert "postgresql+psycopg://" not in rendered
    assert identity.backend == "PostgreSQL"
    assert identity.database_target == "remote PostgreSQL"


def test_backend_identity_sqlite_local_file(settings):
    identity = backend_identity(settings)
    assert identity.backend == "SQLite"
    assert identity.database_target == "local file"
    assert identity.archive_label == "LOCAL"


def test_backend_identity_archive_enabled_label(settings):
    enabled = settings.model_copy(update={"archive_enabled": True})
    assert backend_identity(enabled).archive_label == "ENABLED"


# ---------------------------------------------------------------------------------------------
# 3-6. Watchdog state machine
# ---------------------------------------------------------------------------------------------

def _source(sessions, key="thaiwater_bangkok"):
    with sessions() as s:
        return s.scalar(select(Source).where(Source.source_key == key))


def _add_run(sessions, source_id, *, status: IngestStatus, started_minutes_ago: float, error=None):
    with sessions() as s:
        run = IngestRun(
            source_id=source_id, mode="collector", status=status,
            started_at=utcnow() - timedelta(minutes=started_minutes_ago),
            finished_at=utcnow() - timedelta(minutes=started_minutes_ago),
            error_message=error,
        )
        s.add(run)
        s.commit()
        return run.id


def test_watchdog_never_run(settings, sessions):
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == NEVER_RUN
    assert not report.ok
    assert report.last_success_at is None


def test_watchdog_healthy(settings, sessions):
    src = _source(sessions)
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=5)
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == HEALTHY
    assert report.ok
    assert report.age_minutes < 30


def test_watchdog_stale(settings, sessions):
    src = _source(sessions)
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=90)
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == STALE
    assert not report.ok


def test_watchdog_failed_after_recent_failure_with_no_recent_success(settings, sessions):
    src = _source(sessions)
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=90)
    _add_run(sessions, src.id, status=IngestStatus.FAILED, started_minutes_ago=5, error="simulated outage")
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == FAILED
    assert not report.ok


def test_watchdog_recent_success_wins_even_if_a_failure_happened_before_it(settings, sessions):
    """A transient failure that's already been superseded by a fresh success is HEALTHY, not FAILED."""
    src = _source(sessions)
    _add_run(sessions, src.id, status=IngestStatus.FAILED, started_minutes_ago=90, error="old outage")
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=2)
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == HEALTHY


def test_watchdog_unknown_source_raises_keyerror(settings, sessions):
    with sessions() as s:
        with pytest.raises(KeyError):
            check_source(s, "not_a_real_source")


def test_watchdog_selects_latest_successful_run_not_first(settings, sessions):
    """Several successes exist; age_minutes must reflect the MOST recent one."""
    src = _source(sessions)
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=120)
    _add_run(sessions, src.id, status=IngestStatus.SUCCESS, started_minutes_ago=3)
    with sessions() as s:
        report = check_source(s, "thaiwater_bangkok", max_age_minutes=30)
    assert report.status == HEALTHY
    assert report.age_minutes < 10


# ---------------------------------------------------------------------------------------------
# 7. Registry status vs runtime health stay semantically distinct
# ---------------------------------------------------------------------------------------------

def test_registry_status_and_runtime_health_are_independent(settings, sessions):
    src = _source(sessions)
    with sessions() as s:
        row = s.get(Source, src.id)
        row.status = SourceStatus.UNAVAILABLE  # stale registry entry
        s.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if "rain24" in str(request.url):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [{
            "station": {"tele_station_oldcode": "TW.1", "tele_station_name": {"en": "T"},
                        "tele_station_lat": 13.7, "tele_station_long": 100.5},
            "waterlevel_msl": 1.0, "waterlevel_datetime": _bangkok_local_now_str(),
        }]})

    def fake_fetch(s, client=None):
        from bdo.live.adapters import thaiwater_live
        return thaiwater_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    import bdo.live.manager as lm
    orig = lm._ADAPTERS["thaiwater_bangkok"]
    lm._ADAPTERS["thaiwater_bangkok"] = fake_fetch
    try:
        collect_one(settings, "thaiwater_bangkok", sessions=sessions)
    finally:
        lm._ADAPTERS["thaiwater_bangkok"] = orig
        live_manager.invalidate_all()

    with sessions() as s:
        comparisons = {row.source_key: row for row in registry_vs_runtime_health(s)}
    tw = comparisons["thaiwater_bangkok"]
    assert tw.registry_status == "UNAVAILABLE"           # unchanged by the successful collector run
    assert tw.latest_collector_health == "HEALTHY"       # the actual, current runtime observation


def test_registry_vs_runtime_health_never_collected(settings, sessions):
    with sessions() as s:
        comparisons = {row.source_key: row for row in registry_vs_runtime_health(s)}
    assert comparisons["thaiwater_bangkok"].latest_collector_health is None


# ---------------------------------------------------------------------------------------------
# 8. Public archive does not expose persistent ThaiWater history merely because collection is enabled
# ---------------------------------------------------------------------------------------------

def _patch_thaiwater_with_one_reading(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "rain24" in str(request.url):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [{
            "station": {"tele_station_oldcode": "TW.1", "tele_station_name": {"en": "T"},
                        "tele_station_lat": 13.7, "tele_station_long": 100.5},
            "waterlevel_msl": 1.0, "waterlevel_datetime": utcnow().strftime("%Y-%m-%d %H:%M"),
        }]})

    def fake_fetch(s, client=None):
        from bdo.live.adapters import thaiwater_live
        return thaiwater_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "thaiwater_bangkok", fake_fetch)


@pytest.fixture(autouse=True)
def _reset_live_cache():
    live_manager.invalidate_all()
    yield
    live_manager.invalidate_all()


def test_public_viewer_deployment_excludes_thaiwater_history(settings, sessions, monkeypatch):
    _patch_thaiwater_with_one_reading(monkeypatch)
    collect_one(settings, "thaiwater_bangkok", sessions=sessions)

    public_settings = settings.model_copy(update={
        "runtime_role": RuntimeRole.VIEWER,
        "public_history_excluded_sources": ["thaiwater_bangkok"],
    })
    monkeypatch.setattr(c, "get_settings_cached", lambda: public_settings)
    with sessions() as s:
        meas = c.measurements_df(s)
    assert meas.empty or "thaiwater_bangkok" not in set(meas["source_key"])


def test_development_role_still_sees_thaiwater_history(settings, sessions, monkeypatch):
    """Collection/research access is unaffected — only the public/VIEWER deployment is gated."""
    _patch_thaiwater_with_one_reading(monkeypatch)
    collect_one(settings, "thaiwater_bangkok", sessions=sessions)

    dev_settings = settings.model_copy(update={
        "runtime_role": RuntimeRole.DEVELOPMENT,
        "public_history_excluded_sources": ["thaiwater_bangkok"],
    })
    monkeypatch.setattr(c, "get_settings_cached", lambda: dev_settings)
    with sessions() as s:
        meas = c.measurements_df(s)
    assert "thaiwater_bangkok" in set(meas["source_key"])


def test_viewer_without_exclusion_list_still_sees_other_sources(settings, sessions, monkeypatch):
    """The gate only withholds explicitly listed sources, not everything, under VIEWER."""
    from bdo.live.adapters import floodbangkok_live

    def handler(request: httpx.Request) -> httpx.Response:
        if "sensor_profile" in request.url.path:
            return httpx.Response(200, json={"data": [
                {"id": 1, "code": "FL.TEST.01", "name": "Test", "lat": 13.7, "long": 100.5,
                 "district": "Test", "device_status": "normal"},
            ]})
        return httpx.Response(200, json={"data": [
            {"id": 1, "sensor_profile": 1, "flood_now": "0.0", "timestamp": utcnow().isoformat()},
        ]})

    def fake_fb(s, client=None):
        return floodbangkok_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fb)
    collect_one(settings, "bma_floodbangkok", sessions=sessions)

    public_settings = settings.model_copy(update={
        "runtime_role": RuntimeRole.VIEWER,
        "public_history_excluded_sources": ["thaiwater_bangkok"],
    })
    monkeypatch.setattr(c, "get_settings_cached", lambda: public_settings)
    with sessions() as s:
        meas = c.measurements_df(s)
    assert "bma_floodbangkok" in set(meas["source_key"])


# ---------------------------------------------------------------------------------------------
# Raw source timestamp preservation (§4)
# ---------------------------------------------------------------------------------------------

def test_source_timestamp_raw_preserved_alongside_measurement_at(settings, sessions, monkeypatch):
    from bdo.models import Measurement

    raw = utcnow().strftime("%Y-%m-%d %H:%M")

    def handler(request: httpx.Request) -> httpx.Response:
        if "rain24" in str(request.url):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [{
            "station": {"tele_station_oldcode": "TW.1", "tele_station_name": {"en": "T"},
                        "tele_station_lat": 13.7, "tele_station_long": 100.5},
            "waterlevel_msl": 1.0, "waterlevel_datetime": raw,
        }]})

    def fake_fetch(s, client=None):
        from bdo.live.adapters import thaiwater_live
        return thaiwater_live.fetch(s, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setitem(live_manager._ADAPTERS, "thaiwater_bangkok", fake_fetch)
    collect_one(settings, "thaiwater_bangkok", sessions=sessions)

    with sessions() as s:
        m = s.query(Measurement).filter_by(variable="water_level_msl").one()
        assert m.source_timestamp_raw == raw          # verbatim, not reformatted
        assert m.measurement_at is not None            # normalized value still present
        assert m.source_timestamp_raw != m.measurement_at.isoformat()  # genuinely distinct representations


def test_source_timestamp_raw_not_fabricated_when_absent(settings, sessions, monkeypatch):
    """bkk_live's dds011 reading has no raw string when wl_date is missing — never invented."""
    from bdo.models import Measurement

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": {"records": [{"wl_max": 1.2}]}})  # no wl_date

    from bdo.live.adapters import bkk_live

    def fake_fetch(s, client=None):
        from bdo.ingestion.adapters.bkk_ckan import CKANAdapter
        adapter = CKANAdapter(s.source("bkk_open_data_dds"), s,
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
        return bkk_live.fetch(s, adapter=adapter)

    monkeypatch.setitem(live_manager._ADAPTERS, "bkk_open_data_dds", fake_fetch)
    collect_one(settings, "bkk_open_data_dds", sessions=sessions)

    with sessions() as s:
        m = s.query(Measurement).one()
        assert m.source_timestamp_raw is None
        assert m.measurement_at is None
