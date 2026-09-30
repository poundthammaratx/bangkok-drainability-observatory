"""Data Quality page — uncertainty is shown, not hidden."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from bdo.enums import split_flags
from bdo.ingestion.adapters import IMPLEMENTED_AUTOMATIC
from bdo.models import IngestRun, Measurement, RawSnapshot
from bdo.repository import snapshots as snap_repo
from bdo.ui import components as c


def render() -> None:
    st.title("Data quality")
    c.banner()
    settings = c.get_settings_cached()
    with c.session() as s:
        meas = c.measurements_df(s)
        src = c.sources_df(s)
        stations = c.stations_df(s)
        runs = c.runs_df(s, limit=500)
        dup_prevented = s.scalar(select(func.coalesce(func.sum(IngestRun.records_skipped), 0))) or 0
        by_mode = pd.DataFrame(s.execute(
            select(IngestRun.mode, func.count(Measurement.id))
            .join(Measurement, Measurement.ingest_run_id == IngestRun.id).group_by(IngestRun.mode)).all(),
            columns=["run_mode", "measurements"])
        dup_keys = s.scalar(select(func.count()).select_from(
            select(Measurement.dedup_key).group_by(Measurement.dedup_key).having(func.count() > 1).subquery()))

    n = len(meas)
    unknown = meas[meas["measurement_at"].isna()] if n else meas
    stale = meas[meas["freshness_label"].isin(["STALE", "VERY_STALE"])] if n else meas
    nounit = meas[meas["unit"].isna() & meas["value_num"].notna()] if n else meas
    nocoord = stations[stations["latitude"].isna()] if not stations.empty else stations
    failed_src = src[src["status"].isin(["UNAVAILABLE", "DEGRADED"])]
    bad_runs = runs[runs["status"].isin(["FAILED", "PARTIAL"])] if not runs.empty else runs

    k = st.columns(6)
    k[0].metric("Unknown measurement time", len(unknown))
    k[1].metric("Stale / very stale", len(stale), help="freshness from measurement_at, per-source thresholds")
    k[2].metric("Numeric without unit", len(nounit))
    k[3].metric("Nodes without coordinates", len(nocoord))
    k[4].metric("Duplicates prevented", int(dup_prevented), help="sum of records skipped by deterministic dedup")
    k[5].metric("Failed / partial runs", len(bad_runs))

    st.subheader("Freshness distribution")
    if n:
        dist = meas.groupby(["source_key", "freshness_label"]).size().unstack(fill_value=0)
        st.dataframe(dist, width="stretch")
        st.caption("Labels: LIVE ≤ live, RECENT ≤ recent, STALE ≤ stale, else VERY_STALE; thresholds per source in "
                   "config/sources.yaml. SEED/DEMONSTRATION records can never be LIVE.")
    else:
        st.info("No measurements.")

    st.subheader("Quality flags")
    if n:
        flags = pd.Series([f for q in meas["quality_flag"] for f in split_flags(q)], dtype="object")
        if flags.empty:
            st.write("No flags.")
        else:
            st.dataframe(flags.value_counts().rename_axis("flag").reset_index(name="records"),
                         hide_index=True, width="stretch")

    st.subheader("Source adapters")
    ad = src[["source_key", "status", "adapter", "access_mode", "last_run_status", "last_health"]].copy()
    ad["automatic_adapter_implemented"] = ad["adapter"].isin(IMPLEMENTED_AUTOMATIC)
    st.dataframe(ad, hide_index=True, width="stretch")
    if not failed_src.empty:
        st.warning("Unavailable / degraded: " + ", ".join(failed_src["source_key"]))

    st.subheader("Manual vs automated")
    cols = st.columns(2)
    cols[0].dataframe(src.groupby("access_mode").size().rename("sources").reset_index(), hide_index=True,
                      width="stretch")
    cols[1].dataframe(by_mode, hide_index=True, width="stretch")

    st.subheader("Duplicates and conflicting reports")
    st.write(f"Duplicate dedup keys in database: **{dup_keys}** (expected 0 — enforced by a unique constraint).")
    if n:
        timed = meas.dropna(subset=["measurement_at"])
        grp = timed.groupby(["source_key", "station_id", "variable", "measurement_at"])
        conflicts = grp.filter(lambda g: g["value_num"].nunique(dropna=True) + g["value_text"].nunique(dropna=True) > 1)
        if conflicts.empty:
            st.write("No conflicting values for the same source / station / variable / measurement time.")
        else:
            st.warning(f"{len(conflicts)} record(s) report different values for the same series and time "
                       "(e.g. a source revision). Both are kept.")
            st.dataframe(conflicts[["source_key", "station", "variable", "measured", "value_num", "value_text",
                                    "retrieved", "raw_snapshot_id"]], hide_index=True, width="stretch")

    with st.expander(f"Records with UNKNOWN measurement time ({len(unknown)})"):
        st.dataframe(unknown[["id", "source_key", "station", "variable", "value_num", "value_text", "retrieved",
                              "quality_flag", "notes"]] if len(unknown) else unknown, hide_index=True, width="stretch")
    with st.expander(f"Stale records ({len(stale)})"):
        st.dataframe(stale[["id", "source_key", "station", "variable", "measured", "age_now", "freshness"]]
                     if len(stale) else stale, hide_index=True, width="stretch")
    with st.expander(f"Numeric records without unit ({len(nounit)})"):
        st.dataframe(nounit[["id", "source_key", "station", "variable", "value_num"]] if len(nounit) else nounit,
                     hide_index=True, width="stretch")
    with st.expander(f"Nodes without coordinates ({len(nocoord)})"):
        st.dataframe(nocoord[["id", "source_key", "external_station_id", "name", "node_type", "district"]]
                     if len(nocoord) else nocoord, hide_index=True, width="stretch")

    st.subheader("Ingestion errors")
    if bad_runs.empty:
        st.write("No failed or partial runs.")
    else:
        st.dataframe(bad_runs, hide_index=True, width="stretch")
        st.caption("Full per-record error lists: data/processed/ingest_errors/run_<id>.txt")

    st.subheader("Raw archive integrity")
    if settings.public_deployment:
        st.info("Raw-archive verification is a research/developer control and is disabled in the "
                "Public Alpha read-only deployment.")
    elif st.button("Verify SHA-256 of every raw snapshot"):
        with c.session() as s:
            snaps = list(s.scalars(select(RawSnapshot)))
            bad = [(sn.id, sn.payload_path) for sn in snaps if not snap_repo.verify(settings, sn)]
        if bad:
            st.error(f"{len(bad)} of {len(snaps)} snapshot(s) missing or altered: {bad[:20]}")
        else:
            st.success(f"All {len(snaps)} snapshot file(s) present and unaltered.")
