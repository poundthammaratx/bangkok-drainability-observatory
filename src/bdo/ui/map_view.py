"""Map page — static reference topology + live overlays; renders even with an empty database.

Three layers are merged into one point table (see ``build_points``):

1. **Database** — stations already ingested into this deployment's SQLite (``stations`` table),
   joined to their latest measurement if any. Empty on a fresh PUBLIC_DEPLOYMENT container.
2. **Static reference** — the packaged BKK CKAN snapshot under ``data/reference/`` (see
   ``bdo.repository.reference``), loaded whenever a DB station for the same network is missing.
   This is what makes the map render on a fresh, empty database (milestone item C).
3. **Live** — read-through FloodBangkok and ThaiWater points (see ``bdo.live``), which are never
   persisted and are not present in the DB or the reference snapshot at all.

Only nodes with a published latitude/longitude are ever plotted — coordinates are never invented.
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import streamlit as st

from bdo.analytics.freshness import classify_age
from bdo.live import manager as live_manager
from bdo.live.base import LiveHealth
from bdo.repository import reference as ref_repo
from bdo.ui import components as c
from bdo.ui.overview import latest_per_series
from bdo.util.time import format_duration, format_ts

NO_RECENT = "STATE UNKNOWN / NO RECENT MEASUREMENT"

POINT_COLUMNS = ["id", "external_station_id", "name", "node_type", "layer", "latitude", "longitude",
                 "district", "point_source", "operator", "value_display", "measured", "retrieved",
                 "age_now", "freshness_label", "freshness", "evidence_class", "status"]


def _empty_points() -> pd.DataFrame:
    return pd.DataFrame(columns=POINT_COLUMNS)


def _db_points(s) -> pd.DataFrame:
    stations = c.stations_df(s)
    if stations.empty:
        return _empty_points()
    meas = c.measurements_df(s)
    latest = latest_per_series(meas) if not meas.empty else meas
    if not latest.empty:
        latest = latest.assign(value_display=latest.apply(c.value_display, axis=1))
        per_station = (latest.dropna(subset=["station_id"])
                       .sort_values("measurement_at")
                       .groupby("station_id")
                       .tail(1)
                       .set_index("station_id"))
    else:
        per_station = pd.DataFrame()
    geo = stations.dropna(subset=["latitude", "longitude"]).copy()
    geo["layer"] = geo["node_type"]
    geo["point_source"] = geo["source_key"].fillna("db")
    geo["operator"] = "Bangkok Metropolitan Administration"
    for col, default in (("value_display", None), ("measured", None), ("retrieved", None),
                         ("age_now", None), ("freshness_label", "UNKNOWN"), ("freshness", None),
                         ("evidence_class", None)):
        geo[col] = geo["id"].map(per_station[col]) if (not per_station.empty and col in per_station.columns) else default
    geo["status"] = geo["freshness_label"].where(geo["value_display"].notna(), "static")
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


def _live_points(settings, live_states: dict) -> pd.DataFrame:
    rows = []
    now = c.now_utc()
    for source_key, state in live_states.items():
        thresholds = settings.thresholds_for(source_key)
        for m in state.measurements:
            if m.latitude is None or m.longitude is None:
                continue
            age = None if m.measurement_at is None else now - m.measurement_at
            label = classify_age(age, thresholds)
            status = "static"
            flags = m.notes or ""
            if "device_status=" in flags:
                status = flags.split("device_status=", 1)[1].split(";")[0].strip()
            elif m.measurement_at is not None:
                status = label.value
            rows.append({
                "id": m.external_station_id, "external_station_id": m.external_station_id,
                "name": m.station_name or m.external_station_id, "node_type": m.node_type,
                "layer": m.node_type, "latitude": m.latitude, "longitude": m.longitude,
                "district": m.district, "point_source": source_key, "operator": m.operator,
                "value_display": f"{m.variable}: {m.value_num if m.value_num is not None else m.value_text} {m.unit or ''}".strip(),
                "measured": format_ts(m.measurement_at, c.tz()) if m.measurement_at else "UNKNOWN",
                "retrieved": format_ts(state.fetch_finished_at, c.tz()) if state.fetch_finished_at else "UNKNOWN",
                "age_now": format_duration(age),
                "freshness_label": label.value, "freshness": c.FRESHNESS_ICON[label.value],
                "evidence_class": m.evidence_class, "status": status,
                "validation_status": None, "source_type_code": None, "notes": m.notes,
            })
    return pd.DataFrame(rows)


def build_points(s, settings, live_states: dict) -> pd.DataFrame:
    db = _db_points(s)
    exclude_ids = set(db["external_station_id"].dropna()) if not db.empty else set()
    ref = _reference_points(exclude_ids)
    live = _live_points(settings, live_states)
    fobs = _field_obs_points(s)
    frames = [f.reindex(columns=POINT_COLUMNS) for f in (db, ref, live, fobs) if not f.empty]
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

    with c.session() as s:
        points = build_points(s, settings, live_states)

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
