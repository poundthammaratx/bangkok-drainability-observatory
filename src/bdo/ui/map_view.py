"""Map page — only nodes with source-published (or field-observed) coordinates are plotted."""

from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import streamlit as st

from bdo.ui import components as c
from bdo.ui.overview import latest_per_series


def render() -> None:
    st.title("Map")
    c.banner()
    with c.session() as s:
        stations = c.stations_df(s)
        meas = c.measurements_df(s)
        fobs = c.field_obs_df(s)

    geo = stations.dropna(subset=["latitude", "longitude"]) if not stations.empty else stations
    fgeo = fobs.dropna(subset=["latitude", "longitude"]) if not fobs.empty else fobs
    n_missing = 0 if stations.empty else int(stations["latitude"].isna().sum())

    if geo.empty and fgeo.empty:
        st.info("Geospatial records are not yet available. No station, system node or field observation in the "
                "database has published coordinates, and coordinates are never invented.")
        if n_missing:
            st.caption(f"{n_missing} node(s) exist without coordinates — see the Stations and Data Quality pages.")
        return

    latest = latest_per_series(meas) if not meas.empty else meas
    if not latest.empty:
        latest = latest.assign(value=latest.apply(c.value_display, axis=1))
        per_station = (latest.dropna(subset=["station_id"])
                       .groupby("station_id")
                       .apply(lambda g: pd.Series({
                           "latest": "<br>".join(f"{r.variable}: {r.value}" for r in g.itertuples()),
                           "measured": g["measured"].iloc[0], "freshness": g["freshness"].iloc[0]}),
                           include_groups=False)
                       .reset_index())
    else:
        per_station = pd.DataFrame(columns=["station_id", "latest", "measured", "freshness"])

    points = geo.merge(per_station, left_on="id", right_on="station_id", how="left")
    points["latest"] = points["latest"].fillna("no measurements")
    points["measured"] = points["measured"].fillna("—")
    points["freshness"] = points["freshness"].fillna("—")
    points["layer"] = points["node_type"]

    if not fgeo.empty:
        fpts = fgeo.assign(name=fgeo["location_name"], layer="field_observation", source_key="research_field_observations",
                           latest=fgeo.apply(lambda r: f"{r['phenomenon']}; depth {r['water_depth_cm']} cm; "
                                                        f"{r['passability']}; {r['trend']}", axis=1),
                           measured=fgeo["observed"], freshness="—", coordinate_evidence=fgeo["evidence_class"])
        points = pd.concat([points, fpts[["name", "latitude", "longitude", "layer", "source_key", "latest",
                                          "measured", "freshness", "coordinate_evidence"]]], ignore_index=True)

    layers = sorted(points["layer"].dropna().unique())
    ctl = st.columns([3, 1])
    chosen = ctl[0].multiselect("Node types", layers, default=layers)
    styles = ["carto-positron", "open-street-map", "white-bg"]
    default_style = os.environ.get("BDO_MAP_STYLE", "carto-positron")
    style = ctl[1].selectbox("Base map", styles, index=styles.index(default_style) if default_style in styles else 0,
                             help="white-bg needs no internet (no tiles)")
    points = points[points["layer"].isin(chosen)]

    fig = px.scatter_map(
        points, lat="latitude", lon="longitude", color="layer", hover_name="name",
        hover_data={"layer": True, "source_key": True, "latest": True, "measured": True, "freshness": True,
                    "coordinate_evidence": True, "latitude": ":.5f", "longitude": ":.5f"},
        zoom=9.5, center={"lat": 13.75, "lon": 100.55}, height=640, map_style=style,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="node type")
    st.plotly_chart(fig, width="stretch")
    st.caption(f"{len(points)} point(s) shown. Coordinates are as published by the source "
               f"(evidence class shown on hover). {n_missing} node(s) have no coordinates and are not plotted. "
               "Hover 'measured' is the source's measurement time, not retrieval time.")
