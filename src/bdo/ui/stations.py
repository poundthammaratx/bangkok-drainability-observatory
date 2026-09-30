"""Stations page — time series of actual records only."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import plotly.express as px
import streamlit as st

from bdo.ui import components as c
from bdo.util.time import ensure_utc


def render() -> None:
    st.title("Stations & time series")
    c.banner()
    with c.session() as s:
        stations = c.stations_df(s)
        meas_all = c.measurements_df(s)

    if meas_all.empty:
        st.info("No measurements yet. Seed demonstration data or run an ingest.")
        return

    f = st.columns(4)
    src_opts = sorted(meas_all["source_key"].unique())
    sel_src = f[0].multiselect("Source", src_opts, default=src_opts)
    m1 = meas_all[meas_all["source_key"].isin(sel_src)]
    st_opts = sorted(m1["station"].unique())
    sel_st = f[1].multiselect("Node / station", st_opts, default=st_opts[: min(len(st_opts), 12)])
    m2 = m1[m1["station"].isin(sel_st)]
    var_opts = sorted(m2["variable"].unique())
    sel_var = f[2].multiselect("Variable", var_opts, default=var_opts)
    m3 = m2[m2["variable"].isin(sel_var)]

    timed = m3.dropna(subset=["measurement_at"])
    zone = c.tz()
    if not timed.empty:
        lo = timed["measurement_at"].min().tz_convert(zone).date()
        hi = timed["measurement_at"].max().tz_convert(zone).date()
        rng = f[3].date_input("Measurement period (local dates)", value=(lo, hi))
        if isinstance(rng, tuple) and len(rng) == 2:
            start = ensure_utc(datetime.combine(rng[0], time(0, 0)), zone)
            end = ensure_utc(datetime.combine(rng[1], time(0, 0)) + timedelta(days=1), zone)
            timed = timed[(timed["measurement_at"] >= start) & (timed["measurement_at"] < end)]
    n_unknown = int(m3["measurement_at"].isna().sum())

    connect = st.checkbox("Draw lines between consecutive records (visual aid only; off = markers for actual records)",
                          value=False)
    numeric = timed.dropna(subset=["value_num"]).copy()
    if numeric.empty:
        st.info("No numeric, time-stamped records match the filters.")
    else:
        numeric["unit"] = numeric["unit"].fillna("no unit")
        # One panel per (variable, unit): quantities are never co-plotted just because units match.
        numeric["panel"] = numeric["variable"] + " [" + numeric["unit"] + "]"
        numeric["t_local"] = numeric["measurement_at"].dt.tz_convert(zone)
        numeric = numeric.sort_values("t_local")
        panels = list(dict.fromkeys(numeric["panel"]))
        if len(panels) > 8:
            st.info(f"{len(panels)} variable panels selected; narrow the Variable filter for readability.")
        fig = px.line(
            numeric, x="t_local", y="value_num", color="station", markers=True,
            facet_row="panel" if len(panels) > 1 else None, category_orders={"panel": panels},
            hover_data={"variable": True, "unit": True, "measured": True, "retrieved": True,
                        "age_at_retrieval": True, "evidence_class": True, "quality_flag": True,
                        "raw_snapshot_id": True, "t_local": False, "panel": False},
            height=max(380, 230 * len(panels)), facet_row_spacing=min(0.1, 0.6 / max(len(panels), 1)),
        )
        if not connect:
            fig.update_traces(mode="markers", marker=dict(size=9))
        fig.update_traces(connectgaps=False)
        fig.update_yaxes(matches=None, title_text="")
        domains = [fig.layout[k].domain for k in fig.layout if k.startswith("yaxis")]

        def _place(a):
            top = next((d[1] for d in domains if d and d[0] <= a.y <= d[1]), a.y)
            a.update(text=a.text.replace("panel=", ""), textangle=0, x=0, xanchor="left", xref="paper",
                     y=top, yanchor="bottom", font=dict(size=12))

        fig.for_each_annotation(_place)
        fig.update_layout(xaxis_title=f"measurement time ({zone})", legend_title_text="station / node",
                          margin=dict(l=10, r=10, t=40, b=10))
        if len(panels) == 1:
            fig.update_layout(yaxis_title=panels[0])
        st.plotly_chart(fig, width="stretch")
        if numeric["demo"].any():
            st.caption("Includes SEED / DEMONSTRATION historical records.")
        if len(panels) > 1:
            st.caption("One panel per variable [unit]; each panel has its own y-axis; values are never rescaled.")

    if n_unknown:
        st.warning(f"{n_unknown} matching record(s) have UNKNOWN measurement time and cannot be placed on the time "
                   "axis. They are listed in the table below.")

    st.subheader("Underlying records")
    table = m3 if n_unknown else timed
    table = table.assign(value=table.apply(c.value_display, axis=1)) if not table.empty else table
    cols = ["id", "station", "variable", "value", "measured", "retrieved", "age_at_retrieval", "freshness",
            "evidence_class", "quality_flag", "source_key", "raw_snapshot_id", "raw_payload", "notes"]
    st.dataframe(table[cols] if not table.empty else table, hide_index=True, width="stretch")

    with st.expander(f"Node catalogue ({len(stations)} nodes)"):
        st.dataframe(stations, hide_index=True, width="stretch")
