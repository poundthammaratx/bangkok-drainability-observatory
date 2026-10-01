"""Map page — static reference topology + canonical current state; renders even with an empty DB.

Point sources merged into one table (see ``build_points``), in priority order:

1. **Resolved dynamic state** — ``bdo.analytics.current_state.resolve_current_state()``, the one
   shared current-state layer also used by Overview and Live Situation (milestone §17A0). This
   already reconciles the persisted archive against the transient live read-through per natural
   key, so a FloodBangkok sensor that a collector has persisted into the ``stations`` table is
   never double-plotted alongside its own live reading — the resolver picks one winner per key.
2. **Static DB topology** — stations in this deployment's database with *no* resolved current
   reading (e.g. BKK telemetry nodes a researcher ingested locally but hasn't collected live data
   for). Shown as ``status="static"``.
3. **Static reference** — the packaged BKK CKAN snapshot under ``data/reference/`` (see
   ``bdo.repository.reference``), loaded whenever neither of the above has a row for that network.
   This is what makes the map render on a fresh, empty database (milestone item C).
4. **Field observations** — unchanged from v0.1/v0.2, a separate network with its own evidence class.

Only nodes with a published latitude/longitude are ever plotted — coordinates are never invented.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from bdo.analytics.current_state import ResolvedObservation, resolve_public_current_state
from bdo.database import archive_reachable
from bdo.live import manager as live_manager
from bdo.live.base import LiveHealth
from bdo.repository import reference as ref_repo
from bdo.ui import components as c
from bdo.util.time import format_duration, format_ts

NO_RECENT = "STATE UNKNOWN / NO RECENT MEASUREMENT"

POINT_COLUMNS = ["id", "external_station_id", "name", "node_type", "layer", "latitude", "longitude",
                 "district", "point_source", "operator", "value_display", "measured", "retrieved",
                 "age_now", "freshness_label", "freshness", "evidence_class", "status"]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _empty_points() -> pd.DataFrame:
    return pd.DataFrame(columns=POINT_COLUMNS)


def _dynamic_points(resolved: list[ResolvedObservation]) -> pd.DataFrame:
    """One map point per (source, station) from the shared resolver — see module docstring.

    A station reporting several variables (e.g. a future multi-sensor node) is still one point:
    its hover value lists every variable, and its freshness/measured fields come from whichever
    variable has the newest ``measurement_at``.
    """
    groups: dict[tuple[str, str], list[ResolvedObservation]] = {}
    for r in resolved:
        if r.external_station_id is None or r.latitude is None or r.longitude is None:
            continue
        groups.setdefault((r.source_key, r.external_station_id), []).append(r)

    rows = []
    for (source_key, ext_id), items in groups.items():
        best = max(items, key=lambda r: r.measurement_at or _EPOCH)
        value_display = "; ".join(
            f"{r.variable}: {r.value_num if r.value_num is not None else (r.value_text or '—')} {r.unit or ''}".strip()
            for r in items
        )
        status = "static"
        notes_blob = " ".join(r.notes or "" for r in items)
        if "device_status=" in notes_blob:
            status = notes_blob.split("device_status=", 1)[1].split(";")[0].split()[0].strip()
        elif best.measurement_at is not None:
            status = best.freshness.value
        rows.append({
            "id": ext_id, "external_station_id": ext_id, "name": best.station_name or ext_id,
            "node_type": best.node_type, "layer": best.node_type, "latitude": best.latitude,
            "longitude": best.longitude, "district": best.district, "point_source": source_key,
            "operator": best.operator, "value_display": value_display,
            "measured": format_ts(best.measurement_at, c.tz()) if best.measurement_at else "UNKNOWN",
            "retrieved": format_ts(best.retrieved_at, c.tz()) if best.retrieved_at else "UNKNOWN",
            "age_now": format_duration(None if best.measurement_at is None else c.now_utc() - best.measurement_at),
            "freshness_label": best.freshness.value, "freshness": c.FRESHNESS_ICON[best.freshness.value],
            "evidence_class": best.evidence_class, "status": status,
        })
    return pd.DataFrame(rows)


def _static_db_points(s, dynamic_keys: set[tuple[str, str]]) -> pd.DataFrame:
    """DB-registered stations with no resolved current reading — topology only."""
    stations = c.stations_df(s)
    if stations.empty:
        return _empty_points()
    geo = stations.dropna(subset=["latitude", "longitude"]).copy()
    geo = geo[~geo.apply(lambda r: (r["source_key"], r["external_station_id"]) in dynamic_keys, axis=1)]
    if geo.empty:
        return _empty_points()
    geo["layer"] = geo["node_type"]
    geo["point_source"] = geo["source_key"].fillna("db")
    geo["operator"] = "Bangkok Metropolitan Administration"
    for col, default in (("value_display", None), ("measured", "UNKNOWN"), ("retrieved", "UNKNOWN"),
                         ("age_now", None), ("freshness_label", "UNKNOWN"), ("freshness", "⚪ UNKNOWN"),
                         ("evidence_class", None)):
        geo[col] = default
    geo["status"] = "static"
    return geo


def _reference_points(exclude_ids: set[str]) -> pd.DataFrame:
    ref = ref_repo.load_coordinate_topology()
    if ref.empty:
        return ref
    ref = ref[~ref["external_station_id"].isin(exclude_ids)].copy()
    ref["layer"] = ref["node_type"]
    ref["point_source"] = ref["source_key"]
    ref["value_display"] = None
    ref["measured"] = None
    ref["retrieved"] = None
    ref["age_now"] = None
    ref["freshness_label"] = "UNKNOWN"
    ref["freshness"] = "⚪ UNKNOWN"
    ref["evidence_class"] = ref["coordinate_evidence_class"]
    ref["status"] = "static"
    ref["id"] = ref["external_station_id"]
    return ref


def _field_obs_points(s) -> pd.DataFrame:
    """Research team field observations — preserved from v0.1's map (own layer, own evidence class)."""
    fobs = c.field_obs_df(s)
    if fobs.empty:
        return fobs
    geo = fobs.dropna(subset=["latitude", "longitude"]).copy()
    if geo.empty:
        return geo
    geo["id"] = geo["observation_id"]
    geo["external_station_id"] = geo["observation_id"]
    geo["name"] = geo["location_name"]
    geo["node_type"] = "field_observation"
    geo["layer"] = "field_observation"
    geo["point_source"] = "research_field_observations"
    geo["operator"] = "Bangkok Drainability Observatory research team"
    geo["value_display"] = geo.apply(
        lambda r: f"{r['phenomenon']}; depth {r['water_depth_cm']} cm; {r['passability']}; {r['trend']}", axis=1)
    geo["measured"] = geo["observed"]
    geo["retrieved"] = geo["observed"]
    geo["age_now"] = None
    geo["freshness_label"] = "UNKNOWN"
    geo["freshness"] = "⚪ UNKNOWN"
    geo["status"] = "observed"
    return geo


def build_points_offline(settings, live_states: dict) -> pd.DataFrame:
    """Map points with **no database access at all** — packaged reference topology + live
    read-through only. Used when the persistent archive is unreachable (milestone §17H/§18):
    ``resolve_current_state`` isn't even called here, since it would try to query the DB; callers
    pass pre-computed ``live_only_observations(settings, live_states)`` to ``resolved`` instead
    of this function querying anything itself.
    """
    from bdo.analytics.current_state import live_only_observations

    resolved = live_only_observations(settings, live_states)
    dynamic = _dynamic_points(resolved)
    dynamic_keys = {(row.source_key, row.external_station_id) for row in resolved
                    if row.external_station_id is not None and row.latitude is not None}
    ref = _reference_points({ext_id for _, ext_id in dynamic_keys})

    frames = [f.reindex(columns=POINT_COLUMNS) for f in (dynamic, ref) if not f.empty]
    if not frames:
        return _empty_points()
    points = pd.concat(frames, ignore_index=True)
    points["hover_state"] = points["value_display"].fillna(NO_RECENT)
    points["freshness_label"] = points["freshness_label"].fillna("UNKNOWN")
    points["status"] = points["status"].fillna("static")
    return points


def build_points(s, settings, live_states: dict) -> pd.DataFrame:
    resolved = resolve_public_current_state(s, settings, live_states)
    dynamic = _dynamic_points(resolved)
    dynamic_keys = {(row.source_key, row.external_station_id) for row in resolved
                    if row.external_station_id is not None and row.latitude is not None}
    static_db = _static_db_points(s, dynamic_keys)

    stations_all = c.stations_df(s)
    exclude_ids = (set(stations_all["external_station_id"].dropna()) if not stations_all.empty else set()) \
        | {ext_id for _, ext_id in dynamic_keys}
    ref = _reference_points(exclude_ids)
    fobs = _field_obs_points(s)

    frames = [f.reindex(columns=POINT_COLUMNS) for f in (dynamic, static_db, ref, fobs) if not f.empty]
    if not frames:
        return _empty_points()
    points = pd.concat(frames, ignore_index=True)
    points["hover_state"] = points["value_display"].fillna(NO_RECENT)
    points["freshness_label"] = points["freshness_label"].fillna("UNKNOWN")
    points["status"] = points["status"].fillna("static")
    return points


def render() -> None:
    st.title("Map")
    c.banner()

    settings = c.get_settings_cached()
    live_states = live_manager.get_all_live_states(settings)

    archive_ok = archive_reachable(settings)
    if archive_ok:
        try:
            with c.session() as s:
                points = build_points(s, settings, live_states)
        except Exception:
            archive_ok = False  # reachability probe can race an actual failure
    if not archive_ok:
        st.caption("Persistent archive unavailable this cycle — showing packaged topology and "
                  "live read-through only (milestone v0.3 §18).")
        points = build_points_offline(settings, live_states)

    unavailable = [k for k, st_ in live_states.items() if st_.health is LiveHealth.UNAVAILABLE]
    if unavailable:
        st.caption(f"Live overlay unavailable this cycle: {', '.join(unavailable)} — static topology "
                   "and any last-known values are still shown below.")

    if points.empty:
        st.info("No coordinate-bearing station, system node, or live reading is available. "
                "Coordinates are never invented, so nothing is plotted.")
        return

    f = st.columns(5)
    sources = sorted(points["point_source"].dropna().unique())
    sel_sources = f[0].multiselect("Source", sources, default=sources)
    layers = sorted(points["layer"].dropna().unique())
    sel_layers = f[1].multiselect("Node type", layers, default=layers)
    freshness_opts = ["LIVE", "RECENT", "STALE", "VERY_STALE", "UNKNOWN"]
    present_freshness = [x for x in freshness_opts if x in set(points["freshness_label"])]
    sel_freshness = f[2].multiselect("Freshness", present_freshness, default=present_freshness)
    statuses = sorted(points["status"].dropna().unique())
    sel_status = f[3].multiselect("Status", statuses, default=statuses)
    if "district" in points.columns and points["district"].notna().any():
        districts = sorted(points["district"].dropna().unique())
        sel_districts = f[4].multiselect("District", districts, default=districts)
    else:
        sel_districts = None

    t = st.columns(4)
    show_stale = t[0].checkbox("Show stale", value=True)
    show_failed = t[1].checkbox("Show failed sensors", value=True)
    show_unknown = t[2].checkbox("Show unknown", value=True)
    show_static_only = t[3].checkbox("Show static-only nodes", value=True)

    filtered = points[points["point_source"].isin(sel_sources) & points["layer"].isin(sel_layers) &
                      points["freshness_label"].isin(sel_freshness) & points["status"].isin(sel_status)]
    if sel_districts is not None:
        filtered = filtered[filtered["district"].isin(sel_districts) | filtered["district"].isna()]
    if not show_stale:
        filtered = filtered[~filtered["freshness_label"].isin(["STALE", "VERY_STALE"])]
    if not show_failed:
        filtered = filtered[filtered["status"] != "malfunction"]
    if not show_unknown:
        filtered = filtered[filtered["freshness_label"] != "UNKNOWN"]
    if not show_static_only:
        filtered = filtered[filtered["status"] != "static"]

    styles = ["carto-positron", "open-street-map", "white-bg"]
    default_style = os.environ.get("BDO_MAP_STYLE", "carto-positron")
    style_col, count_col = st.columns([1, 3])
    style = style_col.selectbox("Base map", styles, index=styles.index(default_style) if default_style in styles else 0,
                                help="white-bg needs no internet — use it if map tiles fail to load")
    count_col.caption(f"{len(filtered)} of {len(points)} point(s) shown after filters. "
                      "white-bg is a no-tile fallback if the CARTO/OSM basemap fails to load in your browser.")

    if filtered.empty:
        st.warning("No point matches the current filters.")
        return

    fig = px.scatter_map(
        filtered, lat="latitude", lon="longitude", color="layer", hover_name="name",
        hover_data={"layer": True, "point_source": True, "operator": True, "hover_state": True,
                    "measured": True, "retrieved": True, "age_now": True, "freshness": True,
                    "evidence_class": True, "status": True, "latitude": ":.5f", "longitude": ":.5f"},
        zoom=9.5, center={"lat": 13.75, "lon": 100.55}, height=640, map_style=style,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="node type")
    st.plotly_chart(fig, width="stretch")
    st.caption("Colour encodes node type only — never a safety judgement. Hover 'hover_state' shows "
               f"the latest reported value, or \"{NO_RECENT}\" when none is available. "
               "'measured' is the source's measurement time, never retrieval time.")

    with st.expander("Live source health (this overlay)"):
        rows = [{
            "source_key": k, "health": v.health.value, "records": v.record_count,
            "freshness": v.freshness.value, "age_min": None if v.age_minutes is None else round(v.age_minutes, 1),
            "http_status": v.http_status, "error": v.error, "endpoint": v.endpoint,
        } for k, v in live_states.items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
