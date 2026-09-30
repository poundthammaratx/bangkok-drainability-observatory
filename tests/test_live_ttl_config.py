"""Regression tests for HOTFIX v0.2.1: ``Settings.live_ttl_for`` AttributeError in production.

Root cause: ``bdo.live.manager.get_live_state`` calls ``settings.live_ttl_for(source_key)`` (added
alongside the v0.2 live data plane), but the deployed build's ``bdo.config.Settings`` predated
that method — a version-skew between ``bdo/live/manager.py`` and ``bdo/config.py``, not a design
flaw in either. These tests pin the contract between the two modules using the real ``Settings``
class (never a dummy/mock settings object), so any future skew fails CI instead of production.
"""

from __future__ import annotations

import httpx
import pytest

from bdo.config import Settings
from bdo.live import manager as live_manager
from bdo.live.base import LiveHealth
from bdo.util.access import assert_writes_allowed

EXPECTED_DEFAULTS = {
    "bma_floodbangkok": 180,
    "bkk_open_data_dds": 300,
    "tmd_bangkok_radar": 300,
    "thaiwater_bangkok": 300,
    "rid_water_situation": 1800,
}


@pytest.fixture(autouse=True)
def _reset_live_cache():
    live_manager.invalidate_all()
    yield
    live_manager.invalidate_all()


def test_settings_has_live_ttl_for_method():
    """The exact call site that crashed in production must exist on the real class."""
    settings = Settings()
    assert hasattr(settings, "live_ttl_for")
    assert callable(settings.live_ttl_for)


def test_every_live_source_key_resolves_a_positive_ttl():
    """bdo.live.manager.LIVE_SOURCE_KEYS must all resolve via the real Settings class."""
    settings = Settings(live_ttl_seconds=dict(EXPECTED_DEFAULTS))
    for key in live_manager.LIVE_SOURCE_KEYS:
        ttl = settings.live_ttl_for(key)
        assert isinstance(ttl, int)
        assert ttl > 0


def test_configured_ttl_defaults_match_the_milestone_spec():
    """The intended per-source TTLs (config/settings.yaml) must not silently drift."""
    settings = Settings(live_ttl_seconds=dict(EXPECTED_DEFAULTS))
    for key, expected in EXPECTED_DEFAULTS.items():
        assert settings.live_ttl_for(key) == expected


def test_settings_yaml_actually_declares_the_expected_defaults():
    """The real config file (not a hand-built Settings object) must match too."""
    from bdo.config import load_settings

    settings = load_settings()
    for key, expected in EXPECTED_DEFAULTS.items():
        assert settings.live_ttl_for(key) == expected


def test_unknown_source_key_fails_safe_with_documented_default():
    """Requirement: unknown keys never raise; they get the documented default TTL."""
    settings = Settings(live_default_ttl_seconds=300)
    assert settings.live_ttl_for("not_a_real_source") == 300


def test_bare_settings_with_no_config_still_resolves_every_live_source_key():
    """Even a Settings() with zero YAML loaded (no live_ttl_seconds at all) must not raise."""
    settings = Settings()  # every field at its class default; live_ttl_seconds == {}
    for key in live_manager.LIVE_SOURCE_KEYS:
        assert settings.live_ttl_for(key) > 0


def test_get_live_state_with_real_settings_does_not_raise_attributeerror(monkeypatch):
    """The exact failure mode from the production traceback, reproduced and pinned closed."""
    settings = Settings(live_ttl_seconds=dict(EXPECTED_DEFAULTS))

    def fake_fetch(_settings):
        from bdo.enums import FreshnessLabel
        from bdo.live.base import LiveSourceState
        from bdo.util.time import utcnow

        return LiveSourceState(
            source_key="bma_floodbangkok", endpoint="mock://test", fetch_started_at=utcnow(),
            fetch_finished_at=utcnow(), http_status=200, health=LiveHealth.HEALTHY,
            record_count=0, source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN,
        )

    monkeypatch.setitem(live_manager._ADAPTERS, "bma_floodbangkok", fake_fetch)
    state = live_manager.get_live_state(settings, "bma_floodbangkok", force=True)
    assert state.health is LiveHealth.HEALTHY


def test_get_all_live_states_with_real_settings_and_mocked_adapters(monkeypatch):
    settings = Settings(live_ttl_seconds=dict(EXPECTED_DEFAULTS))

    def make_fake(key):
        def fake_fetch(_settings):
            from bdo.enums import FreshnessLabel
            from bdo.live.base import LiveSourceState
            from bdo.util.time import utcnow

            return LiveSourceState(
                source_key=key, endpoint="mock://test", fetch_started_at=utcnow(),
                fetch_finished_at=utcnow(), http_status=200, health=LiveHealth.UNKNOWN,
                record_count=0, source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN,
            )
        return fake_fetch

    for key in live_manager.LIVE_SOURCE_KEYS:
        monkeypatch.setitem(live_manager._ADAPTERS, key, make_fake(key))

    states = live_manager.get_all_live_states(settings, force=True)
    assert set(states) == set(live_manager.LIVE_SOURCE_KEYS)
    for key, state in states.items():
        assert state.source_key == key


def test_public_deployment_still_permits_read_only_get_with_real_settings(monkeypatch):
    """PUBLIC_DEPLOYMENT=true must not block bdo.live — only bdo.util.access writes."""
    public_settings = Settings(public_deployment=True, live_ttl_seconds=dict(EXPECTED_DEFAULTS))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    from bdo.live.adapters import floodbangkok_live

    client = httpx.Client(transport=httpx.MockTransport(handler))
    state = floodbangkok_live.fetch(public_settings, client=client)
    assert state.http_status == 200  # the GET was allowed to happen at all

    from bdo.util.access import PublicDeploymentBlocked

    with pytest.raises(PublicDeploymentBlocked):
        assert_writes_allowed(public_settings, "ingestion")
