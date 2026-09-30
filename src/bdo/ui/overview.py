"""Overview page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from bdo.live import manager as live_manager
from bdo.ui import components as c
from bdo.util.time import format_duration, format_ts

# (source_key, variables-or-None-for-all) per analyst-oriented group. thaiwater_bangkok appears
# twice because one source publishes both a rain-gauge and a water-level network.
LIVE_GROUPS: list[tuple[str, list[tuple[str, list[str] | None]]]] = [
    ("RAINFALL / DISTURBANCE", [("thaiwater_bangkok", ["rainfall_24h"]), ("tmd_bangkok_radar", None)]),
    ("SURFACE FLOODING", [("bma_floodbangkok", None)]),
    ("URBAN HYDRAULIC STATE", [("bkk_open_data_dds", None), ("thaiwater_bangkok", ["water_level_msl"])]),
    ("UPSTREAM / DOWNSTREAM CONTEXT", [("rid_water_situation", None)]),
]
_REFRESH_CHOICES = {"OFF": None, "1 min": 60, "5 min": 300, "10 min": 600}


def latest_per_series(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.sort_values(["measurement_at", "id"], ascending=[False, False], na_position="last")
    return d.groupby(["source_key", "station_id", "variable"], dropna=False, as_index=False).head(1)


def current_verified_state(meas: pd.DataFrame) -> pd.DataFrame:
    """The subset of the latest-per-series records that may be shown as *current*.

    Only a record whose freshness is LIVE (per its source-specific threshold, computed from
    ``measurement_at``) and that is not SEED/DEMONSTRATION data qualifies. Stale, historical and
    demonstration records are excluded even though ``assess()`` already prevents demo data from
    being labelled LIVE — the explicit ``~demo`` filter here is a second, independent guard.
    """
    if meas.empty:
        return meas
    latest = latest_per_series(meas)
    return latest[(latest["freshness_label"] == "LIVE") & (~latest["demo"].astype(bool))]


def _group_rows(live_states: dict, sources: list[tuple[str, list[str] | None]]) -> list[dict]:
    rows = []
    now = c.now_utc()
    for source_key, variables in sources:
        state = live_states.get(source_key)
        if state is None:
            continue
        for m in state.measurements:
            if variables is not None and m.variable not in variables:
                continue
            age = None if m.measurement_at is None else now - m.measurement_at
            value = m.value_num if m.value_num is not None else (m.value_text or "—")
            rows.append({
                "source": m.source_key, "station": m.station_name or m.external_station_id or "—",
                "variable": m.variable, "value": f"{value} {m.unit or ''}".strip(),
                "measured": format_ts(m.measurement_at, c.tz()) if m.measurement_at else "UNKNOWN",
                "age": format_duration(age), "evidence_class": m.evidence_class,
                "notes": m.notes or "",
            })
        if not state.measurements:
            # source contributes to this group but returned no per-station values (e.g. RID, TMD):
            # surface its source-level state instead of silently omitting the group.
            rows.append({
                "source": source_key, "station": "(source-level)", "variable": "—", "value": "—",
                "measured": "UNKNOWN", "age": "UNKNOWN", "evidence_class": "—",
                "notes": f"health={state.health.value}"
                        + (f"; {state.error}" if state.error else "")
                        + (f"; {state.context.get('note')}" if state.context.get("note") else ""),
            })
    return rows


def render_live_current_state(settings) -> None:
    st.subheader("Live read-through current state (not persisted)")
    st.caption("Independent GET against official public endpoints, cached briefly per source — "
               "never written to the database, never combined with 'Current verified state' "
               "above. See docs/LIVE_DATA_ARCHITECTURE.md and docs/SOURCE_ENDPOINTS.md.")

    ctl = st.columns([1, 3])
    choice = ctl[0].selectbox("Auto refresh", list(_REFRESH_CHOICES), index=2, key="bdo_auto_refresh_choice")
    interval = _REFRESH_CHOICES[choice]

    def _body() -> None:
        _render_live_body(settings, interval)

    st.fragment(_body, run_every=interval)()


def _render_live_body(settings, interval: int | None) -> None:
    page_refreshed = c.now_utc()
    live_states = live_manager.get_all_live_states(settings)
    freshest = max((v.source_measurement_at for v in live_states.values() if v.source_measurement_at is not None),
                  default=None)

    info = st.columns(3)
    info[0].markdown(f"**Page refreshed:**  \n{c.fmt(page_refreshed)}")
    info[1].markdown(f"**Freshest source measurement:**  \n{c.fmt(freshest) if freshest else 'UNKNOWN'}")
    info[2].markdown("**Next refresh:**  \n" + (f"~{interval}s (auto)" if interval else "manual (auto-refresh OFF)"))

    for title, sources in LIVE_GROUPS:
        st.markdown(f"**{title}**")
        rows = _group_rows(live_states, sources)
        if not rows:
            st.caption("No live data for this group this cycle.")
            continue
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    with st.expander("Live source health (all sources)"):
        health_rows = [{
            "source_key": k, "health": v.health.value, "records": v.record_count,
            "freshness": v.freshness.value, "age_min": None if v.age_minutes is None else round(v.age_minutes, 1),
            "http_status": v.http_status, "endpoint": v.endpoint, "error": v.error,
        } for k, v in live_states.items()]
        st.dataframe(pd.DataFrame(health_rows), hide_index=True, width="stretch")
        st.caption("HEALTHY requires both a reachable endpoint and a recent data timestamp — HTTP "
                   "200 alone is never enough. UNAVAILABLE falls back to the last successful "
                   "in-memory result where one exists (shown as DEGRADED); it is never replaced by "
                   "SEED/DEMO data.")


def data_gaps(src: pd.DataFrame, meas: pd.DataFrame, stations: pd.DataFrame) -> list[str]:
    gaps = []
    for _, r in src.iterrows():
        if r["status"] in ("UNAVAILABLE", "UNKNOWN", "DEGRADED"):
            detail = next((str(v) for v in (r["last_health"], r["notes"]) if v is not None and not pd.isna(v) and str(v).strip()),
                          "no healthcheck yet")
            if r["status"] == "UNKNOWN" and (r["last_health"] is None or pd.isna(r["last_health"])):
                detail = f"never health-checked — run `python scripts/ingest.py --source {r['source_key']}`"
            gaps.append(f"**{r['source_key']}** — status {r['status']}: {detail}")
        elif r["status"] == "MANUAL" and r["measurements"] == 0 and r["source_key"] != "research_field_observations":
            gaps.append(f"**{r['source_key']}** — MANUAL source with no transcribed records yet")
    if not meas.empty:
        n_unknown = int(meas["measurement_at"].isna().sum())
        if n_unknown:
            gaps.append(f"{n_unknown} measurement(s) with UNKNOWN measurement time")
        n_nounit = int((meas["unit"].isna() & meas["value_num"].notna()).sum())
        if n_nounit:
            gaps.append(f"{n_nounit} numeric measurement(s) without a unit")
        stale = meas["freshness_label"].isin(["STALE", "VERY_STALE"]).mean() if len(meas) else 0
        if stale == 1:
            gaps.append("No measurement is current: every record is STALE or VERY_STALE")
    if not stations.empty:
        n_nocoord = int(stations["latitude"].isna().sum())
        if n_nocoord:
            gaps.append(f"{n_nocoord} of {len(stations)} station/system node(s) without coordinates")
    return gaps


def render() -> None:
    settings = c.get_settings_cached()

    head = st.columns([5, 1])
    with head[0]:
        st.caption("POUND — Detector Technologies · Physical Systems")
        st.title(settings.project_name)
        st.caption("Project 001 · Public Alpha — Independent monitoring, archival and research "
                   "infrastructure for urban drainage and flood-recovery data.")
    with head[1]:
        st.link_button("POUND Global →", "https://pound-global-website.vercel.app/", width="stretch")

    st.caption(f"Working research title: *{settings.research_title}* · v{settings.version} · "
               f"page rendered {c.fmt(c.now_utc())}")
    c.banner()
    c.help_now_panel()

    with c.session() as s:
        src = c.sources_df(s)
        meas = c.measurements_df(s)
        stations = c.stations_df(s)
        runs = c.runs_df(s, limit=15)
        fobs = c.field_obs_df(s)
        snaps = c.snapshots_df(s, limit=None)

    if src.empty:
        st.error("No sources registered. Run `python scripts/seed_sources.py`.")
        return

    st.subheader("Current verified state")
    verified = current_verified_state(meas)
    if verified.empty:
        st.warning("No verified live measurement is currently available from the connected "
                   "sources.  \nUse the official links above for operational information.", icon="🛰️")
    else:
        for _, row in verified.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['station']} — {row['variable']}**: {c.value_display(row)}")
                fields = st.columns(6)
                fields[0].markdown(f"**Measured:**  \n{row['measured']}")
                fields[1].markdown(f"**Retrieved:**  \n{row['retrieved']}")
                fields[2].markdown(f"**Age:**  \n{row['age_now']}")
                fields[3].markdown(f"**Source:**  \n{row['source_key']}")
                fields[4].markdown(f"**Evidence:**  \n{row['evidence_class']}")
                fields[5].markdown(f"**Freshness:**  \n{row['freshness']}")
    st.caption("Only records with a known measurement time that are LIVE per source-specific "
               "thresholds appear above. SEED/DEMONSTRATION and historical records never do — see "
               "Stations and Event Archive for history.")

    render_live_current_state(settings)

    last_retrieved = meas["retrieved_at"].max() if not meas.empty else None
    last_measured = meas["measurement_at"].max() if not meas.empty else None
    k = st.columns(5)
    k[0].metric("Registered sources", len(src))
    k[1].metric("Measurements", len(meas))
    k[2].metric("Stations / nodes", len(stations))
    k[3].metric("Field observations", len(fobs))
    k[4].metric("Raw snapshots", len(snaps))

    t = st.columns(2)
    t[0].markdown(f"**Data last retrieved:** {c.fmt(last_retrieved) if last_retrieved is not None else '—'}")
    t[1].markdown(f"**Most recent measurement time:** {c.fmt(last_measured) if last_measured is not None else '—'}")
    st.caption("Retrieval time is when this system obtained the data; measurement time is the time the source says "
               "the value represents. Freshness is computed from measurement time only.")

    st.subheader("Source health")
    show = src[["source_key", "agency", "status", "access_mode", "adapter", "default_evidence", "measurements",
                "latest_measurement_at", "last_run_status", "last_health"]].copy()
    show["latest_measurement_at"] = show["latest_measurement_at"].map(lambda v: c.fmt(v) if v is not None and not pd.isna(v) else "—")
    st.dataframe(show, hide_index=True, width="stretch")

    st.subheader("All latest measurements per series (may include historical / SEED data)")
    latest = latest_per_series(meas)
    if latest.empty:
        st.info("No measurements yet.")
    else:
        if latest["demo"].all():
            st.info("Every value below is **SEED / DEMONSTRATION** historical data — not current conditions.", icon="🗂️")
        latest = latest.assign(value=latest.apply(c.value_display, axis=1),
                               demo=latest["demo"].map({True: "SEED/DEMO", False: ""}))
        st.dataframe(latest[["source_key", "station", "variable", "value", "measured", "retrieved",
                             "age_at_retrieval", "age_now", "freshness", "evidence_class", "demo", "raw_snapshot_id"]],
                     hide_index=True, width="stretch")

    st.subheader("Open data gaps")
    gaps = data_gaps(src, meas, stations)
    if gaps:
        st.markdown("\n".join(f"- {g}" for g in gaps))
    else:
        st.success("No open gaps detected by the automatic checks.")

    st.subheader("Latest ingest runs")
    st.dataframe(runs, hide_index=True, width="stretch")
