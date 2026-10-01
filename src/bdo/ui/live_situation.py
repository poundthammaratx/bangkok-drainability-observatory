"""Live Situation — "Bangkok Drainability Public War Room" / Public Situation View (v0.3 §17A).

Not an operational control room. A public observability interface: what the system currently
knows, what has recently changed, how fresh the observations are, where coverage exists, and
where it doesn't. Every number here is built from ``bdo.analytics.current_state`` — the one
canonical current-state resolver also used by Overview and Map (milestone §17A0) — so the same
observation never disagrees across pages. See docs/PUBLIC_WAR_ROOM.md.

Never: SAFE/UNSAFE labels, road-safety inference, causal explanations for state changes, or
SEED/DEMO data presented as current. See the module docstrings of ``bdo.analytics.current_state``
and ``bdo.analytics.events`` for how each rule is enforced in code, not just in this page's copy.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from bdo.analytics.current_state import live_only_observations, resolve_public_current_state
from bdo.analytics.events import recent_events
from bdo.analytics.trends import descriptive_trend_label
from bdo.database import archive_reachable
from bdo.live import manager as live_manager
from bdo.live.base import LiveHealth
from bdo.ui import components as c
from bdo.ui.map_view import build_points, build_points_offline
from bdo.util.time import format_duration, format_ts

_REFRESH_CHOICES = {"OFF": None, "1 min": 60, "5 min": 300, "10 min": 600}
_TREND_WINDOWS = {"1 h": 1, "3 h": 3, "6 h": 6, "12 h": 12, "24 h": 24}
_EVENT_WINDOWS = {"15 min": 15, "30 min": 30, "1 h": 60, "3 h": 180, "6 h": 360}

STATUS_EXPLANATIONS = {
    "What does STALE mean?": (
        "The source was reachable, but its latest measurement is older than the configured "
        "freshness threshold for that source."
    ),
    "What does UNKNOWN mean?": (
        "BDO does not currently have enough timestamped evidence to determine the state of this "
        "location."
    ),
    "What does NORMAL AT SENSOR mean?": (
        "The reporting sensor currently does not indicate flooding according to the source. It "
        "does not guarantee the surrounding road or area is safe or dry."
    ),
    "What does OBSERVABILITY COVERAGE mean?": (
        "It describes where BDO currently has recent usable measurements. It does not describe "
        "where flooding is or is not occurring."
    ),
}


def render() -> None:
    st.title("Live Situation")
    st.caption("Bangkok Drainability Public War Room — Public Situation View")
    c.banner()
    st.warning(
        "This is a **public observability interface**, not an operational control room. It shows "
        "what the system currently knows, what has recently changed, and where its knowledge is "
        "incomplete — never a flood forecast, a safety assessment, or a route recommendation.",
        icon="🧭",
    )

    settings = c.get_settings_cached()
    ctl = st.columns([1, 3])
    choice = ctl[0].selectbox("Auto refresh", list(_REFRESH_CHOICES), index=2, key="warroom_refresh_choice")
    interval = _REFRESH_CHOICES[choice]

    def _body() -> None:
        _render_body(settings, interval)

    st.fragment(_body, run_every=interval)()


def _render_body(settings, interval: int | None) -> None:
    page_refreshed = c.now_utc()
    archive_ok = archive_reachable(settings)
    live_states = live_manager.get_all_live_states(settings)

    if archive_ok:
        try:
            with c.session() as s:
                resolved = resolve_public_current_state(s, settings, live_states)
                points = build_points(s, settings, live_states)
        except Exception:
            # the reachability probe can race an actual failure (e.g. the pool's connection drops
            # between the check and the query) — degrade exactly as if archive_ok had been False
            archive_ok = False
    if not archive_ok:
        st.error("Persistent archive unavailable — live read-through only. Historical trends and "
                 "the full observability history are unavailable this cycle; current live readings "
                 "and packaged topology are still shown below.", icon="🗄️")
        resolved = live_only_observations(settings, live_states)
        points = build_points_offline(settings, live_states)

    info_row = st.columns(3)
    info_row[0].markdown(f"**Page refreshed:**  \n{c.fmt(page_refreshed)}")
    info_row[1].markdown("**Next refresh:**  \n" + (f"~{interval}s (auto)" if interval else "manual (OFF)"))
    info_row[2].markdown(f"**Archive status:**  \n{'reachable' if archive_ok else 'UNAVAILABLE — live-only mode'}")

    _zone1_bangkok_now(resolved, live_states)
    _zone_time_alignment(live_states)
    st.divider()
    _zone2_map(points, live_states)
    st.divider()
    if archive_ok:
        _zone3_trends(settings)
    else:
        st.subheader("Recent hydrological trends")
        st.info("Archive unavailable this cycle — recent trends need persisted history and cannot "
                "be shown from live read-through alone.")
    st.divider()
    _zone4_observability(resolved, points, live_states)
    st.divider()
    if archive_ok:
        _zone5_events()
    else:
        st.subheader("Recent changes")
        st.info("Archive unavailable this cycle — the recent-change feed is built from persisted "
                "history.")
    st.divider()
    _zone_explanations()


# ------------------------------------------------------------------------------------------------
# Zone 1 — Bangkok Now
# ------------------------------------------------------------------------------------------------

def _zone1_bangkok_now(resolved: list, live_states: dict) -> None:
    st.subheader("Bangkok Now")
    now = c.now_utc()
    timed = [r for r in resolved if r.measurement_at is not None]
    freshest = max((r.measurement_at for r in timed), default=None)
    active_sources = sum(1 for v in live_states.values() if v.health is not LiveHealth.UNAVAILABLE)
    reporting_stations = len({(r.source_key, r.external_station_id) for r in timed if r.external_station_id})
    stale = sum(1 for r in resolved if r.freshness.value in ("STALE", "VERY_STALE"))
    unknown = sum(1 for r in resolved if r.freshness.value == "UNKNOWN")

    k = st.columns(6)
    k[0].metric("Freshest measurement", c.fmt(freshest) if freshest else "UNKNOWN")
    k[1].metric("Freshest age", format_duration(None if freshest is None else now - freshest))
    k[2].metric("Active sources", f"{active_sources}/{len(live_states)}")
    k[3].metric("Reporting stations", reporting_stations)
    k[4].metric("Stale observations", stale)
    k[5].metric("Unknown-state observations", unknown)

    fb = live_states.get("bma_floodbangkok")
    if fb is not None and fb.context.get("device_status_counts"):
        counts = fb.context["device_status_counts"]
        st.caption(f"FloodBangkok sensor status as of {c.fmt(fb.fetch_finished_at)} "
                   f"(age {format_duration(None if fb.source_measurement_at is None else now - fb.source_measurement_at)}) — "
                   "these counts describe FloodBangkok's own network only, at FloodBangkok's own "
                   "measurement time; they are not combined with any other source's counts.")
        sc = st.columns(4)
        sc[0].metric("Flooding reported", counts.get("flooding", 0))
        sc[1].metric("Minor ponding reported", counts.get("minor_flood", 0))
        sc[2].metric("Normal at sensor", counts.get("normal", 0))
        sc[3].metric("Sensor malfunction", counts.get("malfunction", 0))


def _zone_time_alignment(live_states: dict) -> None:
    st.markdown("**Cross-source time alignment** — are these sources describing approximately the same moment?")
    now = c.now_utc()
    rows = [{
        "source": key, "latest_measurement": c.fmt(state.source_measurement_at) if state.source_measurement_at else "UNKNOWN",
        "age": format_duration(None if state.source_measurement_at is None else now - state.source_measurement_at),
        "health": state.health.value,
    } for key, state in live_states.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ages = [state.age_minutes for state in live_states.values() if state.age_minutes is not None]
    if len(ages) >= 2 and (max(ages) - min(ages)) > 60:
        st.caption(f"⚠️ Spread of {max(ages) - min(ages):.0f} minutes between the freshest and "
                   "stalest source shown above — treat any apparent city-wide pattern with that in "
                   "mind; it is not one synchronized snapshot.")


# ------------------------------------------------------------------------------------------------
# Zone 2 — Map
# ------------------------------------------------------------------------------------------------

def _zone2_map(points: pd.DataFrame, live_states: dict) -> None:
    st.subheader("Live Bangkok Map")
    unavailable = [k for k, v in live_states.items() if v.health is LiveHealth.UNAVAILABLE]
    if unavailable:
        st.caption(f"Live overlay unavailable this cycle: {', '.join(unavailable)} — static "
                   "topology and any last-known values are still shown.")
    if points.empty:
        st.info("No coordinate-bearing station or live reading is available.")
        return

    f = st.columns(3)
    layers = sorted(points["layer"].dropna().unique())
    sel_layers = f[0].multiselect("Node type", layers, default=layers, key="warroom_layers")
    statuses = sorted(points["status"].dropna().unique())
    sel_status = f[1].multiselect("Status", statuses, default=statuses, key="warroom_status")
    show_stale = f[2].checkbox("Include stale", value=True, key="warroom_show_stale")

    filtered = points[points["layer"].isin(sel_layers) & points["status"].isin(sel_status)]
    if not show_stale:
        filtered = filtered[~filtered["freshness_label"].isin(["STALE", "VERY_STALE"])]
    if filtered.empty:
        st.warning("No point matches the current filters.")
        return

    fig = px.scatter_map(
        filtered, lat="latitude", lon="longitude", color="status", hover_name="name",
        hover_data={"layer": True, "point_source": True, "measured": True, "age_now": True,
                    "freshness": True, "evidence_class": True, "latitude": ":.5f", "longitude": ":.5f"},
        zoom=9.5, center={"lat": 13.75, "lon": 100.55}, height=560, map_style="carto-positron",
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="status (not a safety judgement)")
    st.plotly_chart(fig, width="stretch")
    st.caption("Colour encodes reported status/freshness category only — never route safety. "
              "Absence of a point means absence of a sensor, not absence of flooding.")


# ------------------------------------------------------------------------------------------------
# Zone 3 — Recent hydrological trends
# ------------------------------------------------------------------------------------------------

def _zone3_trends(settings) -> None:
    st.subheader("Recent hydrological trends")
    excluded = set(settings.public_history_excluded_sources)
    visible_sources = [k for k in live_manager.LIVE_SOURCE_KEYS if k not in excluded]
    if excluded & set(live_manager.LIVE_SOURCE_KEYS):
        st.caption(
            f"Historical trends for {', '.join(sorted(excluded & set(live_manager.LIVE_SOURCE_KEYS)))} "
            "are withheld from this public panel pending redistribution/licensing review (collection "
            "into the research archive continues unaffected) — see docs/PUBLIC_WAR_ROOM.md."
        )
    choice = st.selectbox("Window", list(_TREND_WINDOWS), index=1, key="warroom_trend_window")
    hours = _TREND_WINDOWS[choice]
    end = c.now_utc()
    start = end - timedelta(hours=hours)

    if not visible_sources:
        st.info("No source is currently cleared for public historical display.")
        return

    with c.session() as s:
        meas = c.measurements_df(s, start=start, end=end, source_keys=visible_sources,
                                 include_unknown_time=False)
        earliest_ever = c.measurements_df(s, source_keys=visible_sources,
                                          include_unknown_time=False)
    earliest_at = earliest_ever["measurement_at"].min() if not earliest_ever.empty else None
    if earliest_at is not None and earliest_at > start + timedelta(minutes=5):
        st.info(f"Archival coverage is currently shorter than the selected window — the earliest "
                f"persisted measurement is {c.fmt(earliest_at)}. Showing only what is actually "
                "available; no history is fabricated to fill the window.", icon="📉")

    if meas.empty:
        st.info("No persisted measurements in this window yet.")
        return

    rows = []
    for (source_key, station, variable, unit), g in meas.groupby(["source_key", "station", "variable", "unit"], dropna=False):
        series = list(zip(g["measurement_at"], g["value_num"]))
        label = descriptive_trend_label(series)
        latest = g.sort_values("measurement_at").iloc[-1]
        rows.append({
            "source": source_key, "station": station, "variable": variable, "unit": unit,
            "latest_value": latest["value_num"], "measured": latest["measured"],
            "age": latest["age_now"], "trend": label, "points_in_window": len(g),
        })
    st.dataframe(pd.DataFrame(rows).sort_values(["source", "station", "variable"]), hide_index=True, width="stretch")
    st.caption("Trend is descriptive only — rising/falling/unchanged between the two most recent "
              "compatible observations (same source, station, variable and unit). Never smoothed, "
              "modelled, or compared across stations/variables/units.")


# ------------------------------------------------------------------------------------------------
# Zone 4 — Observability / Data Confidence Wall
# ------------------------------------------------------------------------------------------------

_CONFIDENCE_NOTE = {
    "bma_floodbangkok": "Timestamp zone (UTC) is empirically inferred, not documented by the source.",
    "thaiwater_bangkok": "Timestamp zone (Asia/Bangkok) is empirically inferred, not documented by the source.",
    "bkk_open_data_dds": "Dataset is batch-published, not telemetry — treat any 'live' label with caution.",
    "tmd_bangkok_radar": "No machine-readable product timestamp exists; health is never HEALTHY.",
    "rid_water_situation": "No verified structured endpoint; figures are not extracted in v0.3.",
}


def _zone4_observability(resolved: list, points: pd.DataFrame, live_states: dict) -> None:
    st.subheader("Observability Coverage")
    st.caption("This section describes where BDO currently has recent usable measurements — "
              "**not** where flooding is or is not occurring. An unobserved area is not implied "
              "to be normal.")

    rows = []
    for key, state in live_states.items():
        source_resolved = [r for r in resolved if r.source_key == key]
        stale_count = sum(1 for r in source_resolved if r.freshness.value in ("STALE", "VERY_STALE"))
        malfunction_count = state.context.get("device_status_counts", {}).get("malfunction")
        rows.append({
            "source": key, "health": state.health.value,
            "latest_measurement": c.fmt(state.source_measurement_at) if state.source_measurement_at else "UNKNOWN",
            "age": format_duration(None if state.source_measurement_at is None else c.now_utc() - state.source_measurement_at),
            "last_retrieval": c.fmt(state.fetch_finished_at) if state.fetch_finished_at else "UNKNOWN",
            "http_status": state.http_status, "records": state.record_count, "stale_count": stale_count,
            "failed_sensors": malfunction_count, "known_limitation": _CONFIDENCE_NOTE.get(key, "—"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if not points.empty:
        cov = points["status"].value_counts()
        freshness_cov = points["freshness_label"].value_counts()
        cc = st.columns(4)
        cc[0].metric("Nodes with recent observations", int(freshness_cov.get("LIVE", 0) + freshness_cov.get("RECENT", 0)))
        cc[1].metric("Nodes stale", int(freshness_cov.get("STALE", 0) + freshness_cov.get("VERY_STALE", 0)))
        cc[2].metric("Nodes unknown", int(freshness_cov.get("UNKNOWN", 0)))
        cc[3].metric("Static-only nodes", int(cov.get("static", 0)))
    unavailable_sources = [k for k, v in live_states.items() if v.health is LiveHealth.UNAVAILABLE]
    if unavailable_sources:
        st.caption(f"Sources unavailable this cycle: {', '.join(unavailable_sources)}")


# ------------------------------------------------------------------------------------------------
# Zone 5 — Recent changes / event strip
# ------------------------------------------------------------------------------------------------

def _zone5_events() -> None:
    st.subheader("Recent Changes")
    choice = st.selectbox("Time window", list(_EVENT_WINDOWS), index=2, key="warroom_event_window")
    minutes = _EVENT_WINDOWS[choice]
    end = c.now_utc()
    start = end - timedelta(minutes=minutes)
    with c.session() as s:
        events = recent_events(s, start, end)
    if not events:
        st.caption("No recorded state transitions in this window.")
        return
    for e in events:
        st.markdown(f"`{format_ts(e.at, c.tz())}`  {e.text}")
    st.caption("Only recorded, already-timestamped state transitions are shown — source health "
              "changes and FloodBangkok device-status changes between two persisted readings. No "
              "causal explanation is generated.")


# ------------------------------------------------------------------------------------------------
# Public explanation (17E)
# ------------------------------------------------------------------------------------------------

def _zone_explanations() -> None:
    with st.expander("What do these terms mean?"):
        for q, a in STATUS_EXPLANATIONS.items():
            st.markdown(f"**{q}**  \n{a}")
