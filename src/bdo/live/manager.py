"""Orchestrates the live read-through adapters: TTL caching, per-source fallback, batching.

This is the only module the UI talks to. It never raises: every adapter failure is caught here
and turned into an UNAVAILABLE (or, if a previous successful fetch is still held, a
DEGRADED/STALE "last known good") ``LiveSourceState`` — see item I of the v0.2 milestone
("live source fails -> retain/show last available in-memory result where possible; mark
DEGRADED / STALE").

Caching is a single process-wide ``TTLCache`` (module-level singleton), so repeated Streamlit
reruns / widget events within a source's TTL window never re-issue the HTTP request — this is
what keeps auto-refresh and filter interactions cheap (milestone item K).
"""

from __future__ import annotations

from dataclasses import replace

from bdo.config import Settings
from bdo.live.adapters import bkk_live, floodbangkok_live, rid_live, tmd_radar_live, thaiwater_live
from bdo.live.base import LiveHealth, LiveSourceState, unavailable_state
from bdo.live.cache import TTLCache
from bdo.util.time import utcnow

_ADAPTERS = {
    bkk_live.SOURCE_KEY: bkk_live.fetch,
    floodbangkok_live.SOURCE_KEY: floodbangkok_live.fetch,
    thaiwater_live.SOURCE_KEY: thaiwater_live.fetch,
    tmd_radar_live.SOURCE_KEY: tmd_radar_live.fetch,
    rid_live.SOURCE_KEY: rid_live.fetch,
}

LIVE_SOURCE_KEYS = tuple(_ADAPTERS)

# Process-wide singleton: caching must survive across Streamlit reruns within one worker process.
_CACHE = TTLCache()


def _fetch_with_fallback(settings: Settings, source_key: str) -> LiveSourceState:
    fetch_fn = _ADAPTERS[source_key]
    try:
        state = fetch_fn(settings)
    except Exception as exc:  # an adapter must never crash the page
        state = unavailable_state(source_key, None, utcnow(), f"adapter error: {exc!s}")

    if state.health is LiveHealth.UNAVAILABLE:
        last_good = _CACHE.last_known(f"good:{source_key}")
        if last_good is not None:
            age = _CACHE.age_seconds(f"good:{source_key}") or 0
            return replace(
                last_good, health=LiveHealth.DEGRADED,
                error=f"live fetch failed ({state.error}); showing last successful result from "
                      f"{age:.0f}s ago",
            )
        return state

    _CACHE.set(f"good:{source_key}", state)  # remember every success, regardless of its own TTL
    return state


def get_live_state(settings: Settings, source_key: str, force: bool = False) -> LiveSourceState:
    """One source's current read-through state, honouring its configured TTL."""
    if source_key not in _ADAPTERS:
        raise KeyError(f"no live adapter for '{source_key}'")
    ttl = settings.live_ttl_for(source_key)
    if force:
        _CACHE.invalidate(f"state:{source_key}")
    return _CACHE.get_or_set(f"state:{source_key}", ttl, lambda: _fetch_with_fallback(settings, source_key))


def get_all_live_states(settings: Settings, force: bool = False) -> dict[str, LiveSourceState]:
    return {key: get_live_state(settings, key, force=force) for key in LIVE_SOURCE_KEYS}


def invalidate_all() -> None:
    _CACHE.invalidate()
