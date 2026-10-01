"""One canonical current-state resolution layer (v0.3 milestone §17A0).

Overview, Map and Live Situation must not each reimplement "what is the current reading" as a
separate business rule — they all call ``resolve_current_state()`` and get the same answer for the
same underlying observation. See docs/PERSISTENT_ARCHIVE.md §Current-state resolution.

Resolution rule, per natural key ``(source_key, external_station_id, variable)``:

* **Persisted-only**: no live reading for this key (either the source has no live adapter, e.g.
  ``rid_water_situation``, or the live fetch failed this cycle) — show the persisted canonical
  observation (``bdo.repository.measurements.current_observations``: latest ``measurement_at``,
  tie-broken by latest ``retrieved_at`` among revisions).
* **Live-only**: no persisted row for this key yet (common right after a v0.3 deploy, before a
  collector has ever run for a source) — show the live reading.
* **Both**: compare ``measurement_at``. Whichever is newer wins **for display only** — a newer
  transient live observation is shown as current; it is never written back into the archive, and
  the archive is never overwritten merely for display (milestone §17).

A reading with ``measurement_at is None`` never wins a comparison against one that has a
timestamp; two timestamp-less readings fall back to preferring the persisted one. SEED/DEMONSTRATION
rows are included in the raw query (so callers that explicitly want history can still see them) but
every resolved row carries an explicit ``demo`` flag — a UI building a *current-state* view must
filter ``demo=False`` itself, exactly as ``bdo.ui.overview.current_verified_state`` already did in
v0.2; this module does not silently drop them, since "resolve consistently" means every caller sees
the same flag, not that the module makes the display decision for them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from bdo.analytics.freshness import assess
from bdo.config import Settings
from bdo.enums import FreshnessLabel
from bdo.live.base import LiveSourceState
from bdo.repository import measurements as meas_repo
from bdo.util.time import utcnow


@dataclass(frozen=True)
class ResolvedObservation:
    source_key: str
    external_station_id: str | None
    station_name: str | None
    node_type: str | None
    variable: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    measurement_at: datetime | None
    retrieved_at: datetime | None
    freshness: FreshnessLabel
    evidence_class: str
    # "persisted" | "live_only" | "persisted+live" — which stores contributed to this key, not
    # which one is currently winning the display comparison (see module docstring).
    persistence: str
    quality_flags: frozenset[str] = field(default_factory=frozenset)
    latitude: float | None = None
    longitude: float | None = None
    district: str | None = None
    operator: str | None = None
    demo: bool = False
    notes: str | None = None


def _key(source_key: str, external_station_id: str | None, variable: str) -> tuple[str, str | None, str]:
    return (source_key, external_station_id, variable)


def _from_persisted(m, settings: Settings) -> ResolvedObservation:
    source_key = m.source.source_key
    station = m.station
    fr = assess(m.measurement_at, m.retrieved_at, settings.thresholds_for(source_key), m.quality_flag)
    from bdo.enums import split_flags

    return ResolvedObservation(
        source_key=source_key,
        external_station_id=station.external_station_id if station else None,
        station_name=station.name if station else None,
        node_type=station.node_type if station else None,
        variable=m.variable, value_num=m.value_num, value_text=m.value_text, unit=m.unit,
        measurement_at=m.measurement_at, retrieved_at=m.retrieved_at, freshness=fr.label,
        evidence_class=m.evidence_class.value, persistence="persisted",
        quality_flags=frozenset(split_flags(m.quality_flag)),
        latitude=station.latitude if station else None, longitude=station.longitude if station else None,
        district=station.district if station else None, operator=station.operator if station else None,
        demo=fr.is_demonstration, notes=m.notes,
    )


def _from_live(lm, settings: Settings) -> ResolvedObservation:
    from bdo.analytics.freshness import classify_age

    age = None if lm.measurement_at is None else utcnow() - lm.measurement_at
    label = classify_age(age, settings.thresholds_for(lm.source_key))
    return ResolvedObservation(
        source_key=lm.source_key, external_station_id=lm.external_station_id,
        station_name=lm.station_name, node_type=lm.node_type, variable=lm.variable,
        value_num=lm.value_num, value_text=lm.value_text, unit=lm.unit,
        measurement_at=lm.measurement_at, retrieved_at=None, freshness=label,
        evidence_class=lm.evidence_class, persistence="live_only", quality_flags=lm.quality_flags,
        latitude=lm.latitude, longitude=lm.longitude, district=lm.district, operator=lm.operator,
        demo=False, notes=lm.notes,
    )


def _newer(a: datetime | None, b: datetime | None) -> bool:
    """True if ``a`` is strictly newer than ``b``; a missing timestamp never wins."""
    if a is None:
        return False
    if b is None:
        return True
    return a > b


def resolve_public_current_state(
    session: Session,
    settings: Settings,
    live_states: dict[str, LiveSourceState] | None = None,
) -> list[ResolvedObservation]:
    """``resolve_current_state()``, minus SEED/DEMONSTRATION rows — what Overview's live section,
    Map and Live Situation must call, never the raw ``resolve_current_state`` directly, wherever
    they are displaying "the current state" rather than explicit history.

    A SEED/DEMONSTRATION record can otherwise end up looking exactly like a live current reading:
    if no live adapter ever produces a given natural key (e.g. an aggregate counter only present
    in the old SEED CSVs, with nothing live superseding it), ``resolve_current_state`` correctly
    reports it as "the latest row for that key" — which is true, but would be dishonest to show on
    a public current-state view. This wrapper is the one place that distinction is enforced, so
    every public-facing caller gets it automatically rather than reimplementing the ``~demo``
    filter itself (the same failure mode §17A0 exists to prevent).
    """
    return [r for r in resolve_current_state(session, settings, live_states) if not r.demo]


def live_only_observations(
    settings: Settings, live_states: dict[str, LiveSourceState] | None,
) -> list[ResolvedObservation]:
    """The persisted archive's contribution, with none: used when the archive is unreachable
    (milestone §17H — "Live Situation must still render... fall back to packaged topology + live
    read-through"). Every row here is ``persistence="live_only"`` since there is nothing to merge
    against."""
    return [_from_live(lm, settings) for state in (live_states or {}).values() for lm in state.measurements]


def resolve_current_state(
    session: Session,
    settings: Settings,
    live_states: dict[str, LiveSourceState] | None = None,
) -> list[ResolvedObservation]:
    """The one function Overview, Map and Live Situation all call for "what is current now"."""
    persisted_rows = meas_repo.current_observations(session)
    by_key: dict[tuple, ResolvedObservation] = {}
    for m in persisted_rows:
        ro = _from_persisted(m, settings)
        by_key[_key(ro.source_key, ro.external_station_id, ro.variable)] = ro

    for state in (live_states or {}).values():
        for lm in state.measurements:
            key = _key(lm.source_key, lm.external_station_id, lm.variable)
            live_ro = _from_live(lm, settings)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = live_ro
            elif _newer(live_ro.measurement_at, existing.measurement_at):
                by_key[key] = ResolvedObservation(**{**live_ro.__dict__, "persistence": "persisted+live"})
            else:
                by_key[key] = ResolvedObservation(**{**existing.__dict__, "persistence": "persisted+live"})

    return list(by_key.values())
