"""Public visualization tests for the Live Situation page (milestone v0.3 §17I).

SEED/DEMO exclusion from public current state, and the current-state resolver's merge/fallback
logic generally, are unit-tested directly against ``bdo.analytics.current_state`` in
tests/test_current_state_resolver.py — this file covers page-level rendering and the public-facing
language constraints (no SAFE/UNSAFE, stale/unknown stay visible) instead of re-deriving them here.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from bdo.ingestion.runner import run_source
from bdo.live import manager as live_manager
from tests_helpers import ckan_adapter

FORBIDDEN_WORDS = ("SAFE", "UNSAFE")


@pytest.fixture()
def warroom_env(seeded, settings, monkeypatch):
    run_source(settings, "bkk_open_data_dds", adapter=ckan_adapter(settings))
    monkeypatch.setenv("BDO_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("BDO_DATA_DIR", str(settings.data_dir))
    monkeypatch.setattr("bdo.live.manager.get_all_live_states", lambda *a, **kw: {})
    from bdo.ui import components
    components.get_settings_cached.clear()
    yield
    components.get_settings_cached.clear()


def _render():
    at = AppTest.from_string("from bdo.ui import live_situation\nlive_situation.render()", default_timeout=90)
    at.run()
    return at


def _all_text(at: AppTest) -> str:
    parts = []
    for coll in (at.markdown, at.caption, at.warning, at.error, at.info, at.subheader, at.title):
        parts += [el.value for el in coll]
    return " ".join(parts)


def test_renders_with_archive_available(warroom_env):
    at = _render()
    assert not at.exception, [e.value for e in at.exception]


def test_renders_with_archive_unavailable(settings, monkeypatch):
    monkeypatch.setattr("bdo.live.manager.get_all_live_states", lambda *a, **kw: {})
    # live_situation.py does `from bdo.database import archive_reachable`, binding the name into
    # its own module namespace — patch it there, not on bdo.database itself.
    monkeypatch.setattr("bdo.ui.live_situation.archive_reachable", lambda *a, **kw: False)
    from bdo.ui import components
    components.get_settings_cached.clear()
    at = _render()
    components.get_settings_cached.clear()
    assert not at.exception, [e.value for e in at.exception]
    assert "Persistent archive unavailable" in _all_text(at)


def test_renders_with_all_live_sources_failed(warroom_env, monkeypatch):
    def unavailable(*a, **kw):
        from bdo.enums import FreshnessLabel
        from bdo.live.base import LiveHealth, LiveSourceState
        from bdo.util.time import utcnow
        return {
            key: LiveSourceState(source_key=key, endpoint=None, fetch_started_at=utcnow(),
                                 fetch_finished_at=utcnow(), http_status=None,
                                 health=LiveHealth.UNAVAILABLE, record_count=0,
                                 source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN,
                                 error="simulated outage")
            for key in live_manager.LIVE_SOURCE_KEYS
        }
    monkeypatch.setattr("bdo.live.manager.get_all_live_states", unavailable)
    at = _render()
    assert not at.exception, [e.value for e in at.exception]


def test_no_safe_unsafe_status_labels(warroom_env):
    """§17B/§24: no status/state *label* may be SAFE or UNSAFE.

    The page's own disclaimer prose legitimately contains the word "safe" (e.g. "does not
    guarantee ... is safe", "never route safety") — those are warnings *against* a safety
    inference, which is required copy, not a violation. What must never exist is a status
    category, map legend entry, or metric label that itself reads SAFE/UNSAFE.
    """
    from bdo.analytics.current_state import ResolvedObservation
    from bdo.enums import FreshnessLabel
    from bdo.ui.map_view import _dynamic_points
    from bdo.util.time import utcnow

    sample = [ResolvedObservation(
        source_key="bma_floodbangkok", external_station_id="FL.TEST.01", station_name="Test",
        node_type="road_flood_sensor", variable="road_flood_depth", value_num=0.0, value_text=None,
        unit="cm", measurement_at=utcnow(), retrieved_at=utcnow(), freshness=FreshnessLabel.LIVE,
        evidence_class="OFFICIAL_REPORTED", persistence="live_only", quality_flags=frozenset(),
        latitude=13.7, longitude=100.5, notes="device_status=normal",
    )]
    points = _dynamic_points(sample)
    statuses = {s.upper() for s in points["status"].dropna().unique()}
    for word in FORBIDDEN_WORDS:
        assert word not in statuses

    # metric labels actually shown in Zone 1 / explanatory copy
    at = _render()
    labels = [m.label.upper() for m in at.metric]
    for word in FORBIDDEN_WORDS:
        assert not any(word == lbl for lbl in labels)


def test_stale_and_unknown_labels_visible_not_hidden(warroom_env):
    """§17B: never hide stale measurements or unknown timestamps behind a cheerful default."""
    at = _render()
    text = _all_text(at)
    # the explanatory microcopy (17E) must be present — these exact terms are the vocabulary the
    # page commits to using instead of SAFE/UNSAFE (see STATUS_EXPLANATIONS in live_situation.py)
    assert "STALE" in text.upper() or "UNKNOWN" in text.upper()
