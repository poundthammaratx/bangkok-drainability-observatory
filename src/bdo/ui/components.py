"""Shared UI helpers: data access as DataFrames (with provenance columns) and presentation."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bdo import DISCLAIMER, NOT_A_WARNING_SERVICE
from bdo.analytics.freshness import assess
from bdo.config import Settings, load_settings
from bdo.database import session_factory
from bdo.enums import FreshnessLabel
from bdo.models import FieldObservation, IngestRun, Measurement, RawSnapshot, Source, Station
from bdo.util.time import format_duration, format_ts, utcnow

FRESHNESS_ICON = {
    FreshnessLabel.LIVE.value: "🟢 LIVE",
    FreshnessLabel.RECENT.value: "🟡 RECENT",
    FreshnessLabel.STALE.value: "🟠 STALE",
    FreshnessLabel.VERY_STALE.value: "🔴 VERY_STALE",
    FreshnessLabel.UNKNOWN.value: "⚪ UNKNOWN",
}


@st.cache_resource
def get_settings_cached() -> Settings:
    s = load_settings()
    from bdo.bootstrap import ensure_seeded
    from bdo.util.logging import get_logger

    try:
        ensure_seeded(s)
    except Exception as exc:
        # The archive (PostgreSQL in production) can be temporarily unreachable — the whole app
        # must still boot (milestone v0.3 §18: "never crash the entire application because
        # PostgreSQL cannot be reached"). Pages that need the database check
        # bdo.database.archive_reachable() themselves and degrade individually; this only
        # guarantees get_settings_cached() itself never raises.
        get_logger("bdo.startup").warning("startup bootstrap skipped: %s", exc)
    return s


def session() -> Session:
    return session_factory(get_settings_cached())()


def now_utc() -> datetime:
    return utcnow()


def tz() -> str:
    return get_settings_cached().display_timezone


def is_public_deployment() -> bool:
    return get_settings_cached().public_deployment


def fmt(dt) -> str:
    if dt is None or (isinstance(dt, float) and pd.isna(dt)) or dt is pd.NaT:
        return "UNKNOWN"
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    return format_ts(dt, tz())


PUBLIC_ALPHA_NOTICE = (
    "**PUBLIC ALPHA** — This is an independent research observatory, not an official "
    "flood-warning, emergency-response, navigation, or drainage-control service.\n\n"
    "Always check the measurement timestamp and source. For operational decisions, use the "
    "responsible authority's official information."
)


def banner() -> None:
    st.warning(f"**{NOT_A_WARNING_SERVICE}**  \n{DISCLAIMER}", icon="⚠️")
    st.info(PUBLIC_ALPHA_NOTICE, icon="🧪")


OFFICIAL_LINKS = [
    ("Bangkok Flood Alert — current information", "https://now.bangkok.go.th/flood-alert.html"),
    ("FloodBangkok", "https://floodbangkok.bangkok.go.th/"),
    ("Traffy Fondue — report an issue", "https://share.traffy.in.th/"),
    ("Thai Meteorological Department radar", "https://weather.tmd.go.th/THA_Z.php"),
    ("ThaiWater Bangkok", "https://bangkok.thaiwater.net/"),
    ("Royal Irrigation Department — water situation", "https://www.rid.go.th/th/water-situation"),
]


def help_now_panel() -> None:
    with st.container(border=True):
        st.markdown("#### Need official information or assistance?")
        cols = st.columns(3)
        for i, (label, url) in enumerate(OFFICIAL_LINKS):
            cols[i % 3].link_button(label, url, width="stretch")
        st.markdown("**Bangkok contact centre: 1555**")
        st.caption("For emergencies, use the relevant official emergency service. This observatory "
                   "does not dispatch assistance and does not infer emergency need from sensor values.")


FOOTER_NOTICE = (
    "© 2026 POUND — Detector Technologies. Bangkok Drainability Observatory is an independent "
    "research initiative.\n\n"
    "Data remain attributable to their respective source agencies. No affiliation or endorsement "
    "by BMA, DDS, RID, TMD, HII, Traffy Fondue, or other data providers is implied."
)

_FOOTER_SOURCES = """
| Source | Agency |
|---|---|
| BKK Open Data — Drainage and Sewerage | Bangkok Metropolitan Administration |
| FloodBangkok | BMA — Department of Drainage and Sewerage |
| RID Water Situation | Royal Irrigation Department |
| Bangkok Weather Radar | Thai Meteorological Department |
| ThaiWater Bangkok | Hydro-Informatics Institute |
| Traffy x Bangkok | BMA / Traffy Fondue (citizen reports; corroboration only) |
| Field observations | Bangkok Drainability Observatory research team |

Full registry, adapters and access mode: see the Stations and Data Quality pages, and
`config/sources.yaml` in the repository.
"""

_FOOTER_METHODOLOGY = """
* **Two clocks.** *Measurement time* is what the source says a value represents; *retrieval time*
  is when this system obtained it. Freshness is always measurement time.
* **Evidence class** (OBSERVED / OFFICIAL_REPORTED / THIRD_PARTY_REPORTED / MODELLED / ASSUMED) is
  recorded per record and never silently converted.
* **Provenance.** Every value traces to an immutable, SHA-256-verified raw snapshot.
* **No invented data.** Coordinates, units, datums and timestamps are never guessed; unknown stays
  unknown.

Details: `docs/architecture.md`, `docs/source_policy.md`, `docs/data_model.md`.
"""

_FOOTER_LIMITATIONS = """
* No hydraulic prediction, flood forecasting or routing recommendation is computed in v0.1.
* Most sources are MANUAL (transcribed) or UNAVAILABLE; only one automatic adapter (CKAN) exists.
* Units and vertical datums of several datasets are not stated by the source and are flagged
  declared-unverified.
* This is a public alpha: expect gaps, and always check the freshness label before relying on a
  value.

Full list: `README.md` → *Known limitations*, `docs/research_scope.md`.
"""


def footer() -> None:
    st.divider()
    st.caption(FOOTER_NOTICE)
    cols = st.columns(4)
    cols[0].link_button("POUND Global →", "https://pound-global-website.vercel.app/", width="stretch")
    with cols[1].popover("Data Sources", width="stretch"):
        st.markdown(_FOOTER_SOURCES)
    with cols[2].popover("Methodology", width="stretch"):
        st.markdown(_FOOTER_METHODOLOGY)
    with cols[3].popover("Limitations", width="stretch"):
        st.markdown(_FOOTER_LIMITATIONS)


# ------------------------------------------------------------------------------------------------
# Data access
# ------------------------------------------------------------------------------------------------

def sources_df(s: Session) -> pd.DataFrame:
    rows = []
    for src in s.scalars(select(Source).order_by(Source.id)):
        n_meas = s.scalar(select(func.count()).select_from(Measurement).where(Measurement.source_id == src.id))
        last_meas = s.scalar(select(func.max(Measurement.measurement_at)).where(Measurement.source_id == src.id))
        last_run = s.scalars(select(IngestRun).where(IngestRun.source_id == src.id)
                             .order_by(IngestRun.id.desc()).limit(1)).first()
        rows.append({
            "source_key": src.source_key, "name": src.name, "agency": src.agency, "status": src.status.value,
            "access_mode": src.access_mode, "adapter": src.adapter, "priority": src.priority,
            "default_evidence": src.default_evidence_class.value, "measurements": n_meas,
            "latest_measurement_at": last_meas,
            "last_run_status": last_run.status.value if last_run else None,
            "last_run_at": last_run.started_at if last_run else None,
            "last_health": src.last_health_message, "base_url": src.base_url, "notes": src.notes,
        })
    return pd.DataFrame(rows)


def measurements_df(
    s: Session,
    start: datetime | None = None,
    end: datetime | None = None,
    source_keys: list[str] | None = None,
    station_ids: list[int] | None = None,
    variables: list[str] | None = None,
    include_unknown_time: bool = True,
    limit: int | None = None,
) -> pd.DataFrame:
    settings = get_settings_cached()
    q = (select(Measurement, Source.source_key, Source.name.label("source_name"), Station.name.label("station_name"),
                Station.node_type, Station.external_station_id, RawSnapshot.payload_path, RawSnapshot.request_url)
         .join(Source, Measurement.source_id == Source.id)
         .outerjoin(Station, Measurement.station_id == Station.id)
         .outerjoin(RawSnapshot, Measurement.raw_snapshot_id == RawSnapshot.id))
    if source_keys:
        q = q.where(Source.source_key.in_(source_keys))
    if station_ids:
        q = q.where(Measurement.station_id.in_(station_ids))
    if variables:
        q = q.where(Measurement.variable.in_(variables))
    if start is not None or end is not None:
        cond = []
        if start is not None:
            cond.append(Measurement.measurement_at >= start)
        if end is not None:
            cond.append(Measurement.measurement_at <= end)
        from sqlalchemy import and_, or_
        timed = and_(*cond)
        q = q.where(or_(timed, Measurement.measurement_at.is_(None)) if include_unknown_time else timed)
    elif not include_unknown_time:
        q = q.where(Measurement.measurement_at.is_not(None))
    q = q.order_by(Measurement.measurement_at.desc().nulls_last(), Measurement.id.desc())
    if limit:
        q = q.limit(limit)

    now = now_utc()
    rows = []
    for m, skey, sname, stname, ntype, ext, ppath, rurl in s.execute(q):
        fr = assess(m.measurement_at, m.retrieved_at, settings.thresholds_for(skey), m.quality_flag, now=now)
        rows.append({
            "id": m.id, "source_key": skey, "source": sname, "station_id": m.station_id,
            "station": stname or "—", "external_station_id": ext, "node_type": ntype, "variable": m.variable,
            "value_num": m.value_num, "value_text": m.value_text, "unit": m.unit,
            "measurement_at": m.measurement_at, "retrieved_at": m.retrieved_at,
            "measured": fmt(m.measurement_at), "retrieved": fmt(m.retrieved_at),
            "age_at_retrieval": format_duration(fr.age_at_retrieval),
            "age_now": format_duration(fr.age),
            "freshness": FRESHNESS_ICON[fr.label.value], "freshness_label": fr.label.value,
            "demo": fr.is_demonstration, "evidence_class": m.evidence_class.value,
            "quality_flag": m.quality_flag, "raw_snapshot_id": m.raw_snapshot_id,
            "raw_payload": ppath if not settings.public_deployment else None,
            "request_url": rurl, "ingest_run_id": m.ingest_run_id, "parser_version": m.parser_version,
            "external_record_id": m.external_record_id, "notes": m.notes,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["measurement_at"] = pd.to_datetime(df["measurement_at"], utc=True)
        df["retrieved_at"] = pd.to_datetime(df["retrieved_at"], utc=True)
    return df


def stations_df(s: Session) -> pd.DataFrame:
    rows = []
    for st_, skey in s.execute(select(Station, Source.source_key).outerjoin(Source, Station.source_id == Source.id)
                               .order_by(Station.id)):
        rows.append({
            "id": st_.id, "source_key": skey, "external_station_id": st_.external_station_id, "name": st_.name,
            "node_type": st_.node_type, "latitude": st_.latitude, "longitude": st_.longitude,
            "district": st_.district, "validation_status": st_.validation_status.value,
            "coordinate_evidence": st_.coordinate_evidence_class.value if st_.coordinate_evidence_class else None,
            "source_type_code": st_.source_type_code, "raw_snapshot_id": st_.raw_snapshot_id, "notes": st_.notes,
        })
    return pd.DataFrame(rows)


def snapshots_df(s: Session, start: datetime | None = None, end: datetime | None = None,
                 source_keys: list[str] | None = None, limit: int | None = 500) -> pd.DataFrame:
    settings = get_settings_cached()
    q = select(RawSnapshot, Source.source_key).join(Source, RawSnapshot.source_id == Source.id)
    if source_keys:
        q = q.where(Source.source_key.in_(source_keys))
    if start is not None:
        q = q.where(RawSnapshot.retrieved_at >= start)
    if end is not None:
        q = q.where(RawSnapshot.retrieved_at <= end)
    q = q.order_by(RawSnapshot.id.desc())
    if limit:
        q = q.limit(limit)
    rows = [{
        "id": r.id, "source_key": k, "label": r.label, "retrieved": fmt(r.retrieved_at),
        "source_measurement_at": fmt(r.source_measurement_at), "content_type": r.content_type,
        "bytes": r.payload_bytes, "sha256": r.payload_hash[:16] + "…", "http_status": r.http_status,
        "payload_path": r.payload_path if not settings.public_deployment else None,
        "request_url": r.request_url, "parser_version": r.parser_version,
        "ingest_run_id": r.ingest_run_id, "notes": r.notes,
    } for r, k in s.execute(q)]
    return pd.DataFrame(rows)


def runs_df(s: Session, limit: int = 50) -> pd.DataFrame:
    q = (select(IngestRun, Source.source_key).join(Source, IngestRun.source_id == Source.id)
         .order_by(IngestRun.id.desc()).limit(limit))
    rows = []
    for r, k in s.execute(q):
        rows.append({
            "run": r.id, "source_key": k, "mode": r.mode, "status": r.status.value, "started": fmt(r.started_at),
            "duration_s": (r.finished_at - r.started_at).total_seconds() if r.finished_at else None,
            "snapshots": r.snapshots_saved, "retrieved": r.records_retrieved, "inserted": r.records_inserted,
            "skipped_dup": r.records_skipped, "failed": r.records_failed, "stations": r.stations_upserted,
            "parser": r.parser_version, "message": r.error_message,
        })
    return pd.DataFrame(rows)


def field_obs_df(s: Session, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
    q = select(FieldObservation).order_by(FieldObservation.observed_at.desc())
    if start is not None:
        q = q.where(FieldObservation.observed_at >= start)
    if end is not None:
        q = q.where(FieldObservation.observed_at <= end)
    rows = [{
        "id": o.id, "observation_id": o.external_observation_id, "observed": fmt(o.observed_at),
        "location_name": o.location_name, "district": o.district, "latitude": o.latitude, "longitude": o.longitude,
        "evidence_class": o.evidence_class.value, "phenomenon": o.phenomenon, "water_depth_cm": o.water_depth_cm,
        "passability": o.passability.value if o.passability else None, "trend": o.trend.value if o.trend else None,
        "photo_ref": o.photo_ref, "source_url": o.source_url, "raw_snapshot_id": o.raw_snapshot_id, "notes": o.notes,
    } for o in s.scalars(q)]
    return pd.DataFrame(rows)


def value_display(row) -> str:
    if row.get("value_num") is not None and not pd.isna(row.get("value_num")):
        v = row["value_num"]
        txt = f"{v:g}"
    else:
        txt = str(row.get("value_text") or "")
    unit = row.get("unit")
    return f"{txt} {unit}" if unit and not pd.isna(unit) else txt


PROVENANCE_COLUMNS = ["measured", "retrieved", "age_at_retrieval", "age_now", "freshness", "evidence_class",
                      "quality_flag", "source_key", "raw_snapshot_id", "raw_payload", "ingest_run_id", "parser_version"]
