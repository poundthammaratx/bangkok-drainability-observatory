"""Event Archive — reconstruct what was known about a period."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import streamlit as st

from bdo.ui import components as c
from bdo.util.time import ensure_utc, to_zone, utcnow


def render() -> None:
    st.title("Event archive")
    st.caption("Select a window on *measurement time* to reconstruct an event from preserved records. "
               "Snapshots are filtered on *retrieval time* over the same window.")
    c.banner()
    zone = c.tz()

    with c.session() as s:
        all_meas = c.measurements_df(s)
    latest = all_meas["measurement_at"].max() if not all_meas.empty else None
    anchor = to_zone(latest.to_pydatetime() if latest is not None and not isinstance(latest, float) else utcnow(), zone)
    default_start = (anchor - timedelta(days=2)).date()
    default_end = (anchor + timedelta(days=1)).date()

    a = st.columns(4)
    d0 = a[0].date_input("Start date", value=default_start)
    t0 = a[1].time_input("Start time", value=time(0, 0), step=timedelta(minutes=15))
    d1 = a[2].date_input("End date", value=default_end)
    t1 = a[3].time_input("End time", value=time(23, 45), step=timedelta(minutes=15))
    start = ensure_utc(datetime.combine(d0, t0), zone)
    end = ensure_utc(datetime.combine(d1, t1), zone)
    if end <= start:
        st.error("End must be after start.")
        return
    st.caption(f"Window: {c.fmt(start)} → {c.fmt(end)}")

    b = st.columns(4)
    src_opts = sorted(all_meas["source_key"].unique()) if not all_meas.empty else []
    sel_src = b[0].multiselect("Source", src_opts)
    st_opts = sorted(all_meas["station"].unique()) if not all_meas.empty else []
    sel_st = b[1].multiselect("Station / node", st_opts)
    var_opts = sorted(all_meas["variable"].unique()) if not all_meas.empty else []
    sel_var = b[2].multiselect("Variable", var_opts)
    include_unknown = b[3].checkbox("Include records with UNKNOWN measurement time", value=False)

    with c.session() as s:
        meas = c.measurements_df(s, start=start, end=end, source_keys=sel_src or None,
                                 variables=sel_var or None, include_unknown_time=include_unknown)
        snaps = c.snapshots_df(s, start=start, end=end, source_keys=sel_src or None, limit=None)
        fobs = c.field_obs_df(s, start=start, end=end)
    if sel_st and not meas.empty:
        meas = meas[meas["station"].isin(sel_st)]

    tabs = st.tabs([f"Measurements ({len(meas)})", f"Raw snapshots ({len(snaps)})",
                    f"Field observations ({len(fobs)})"])
    with tabs[0]:
        if meas.empty:
            st.info("No measurements in this window.")
        else:
            view = meas.assign(value=meas.apply(c.value_display, axis=1)).sort_values("measurement_at")
            cols = ["id", "measured", "retrieved", "age_at_retrieval", "source_key", "station", "variable", "value",
                    "evidence_class", "quality_flag", "freshness", "raw_snapshot_id", "raw_payload", "request_url",
                    "ingest_run_id", "notes"]
            st.dataframe(view[cols], hide_index=True, width="stretch")
            st.download_button("Export measurements (CSV, UTC ISO timestamps)",
                               view.drop(columns=["measured", "retrieved"]).to_csv(index=False).encode("utf-8"),
                               file_name=f"bdo_measurements_{d0}_{d1}.csv", mime="text/csv")
    with tabs[1]:
        if snaps.empty:
            st.info("No raw snapshots were retrieved in this window.")
        else:
            st.dataframe(snaps, hide_index=True, width="stretch")
            st.caption("payload_path is relative to the data directory; files are immutable and SHA-256 verified.")
    with tabs[2]:
        if fobs.empty:
            st.info("No field observations in this window.")
        else:
            st.dataframe(fobs, hide_index=True, width="stretch")
