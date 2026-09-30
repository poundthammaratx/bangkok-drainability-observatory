"""BKK Open Data (DDS) live adapter — an honest freshness check, not a new ingestion path.

Reuses the existing, tested ``CKANAdapter`` (src/bdo/ingestion/adapters/bkk_ckan.py) purely as an
HTTP client for one cheap query: the newest record of the water-level dataset
(``dds011_pak_khlong_talat_wl_max``). Per milestone spec item E.1 ("discover whether the dataset
documented as 5-minute data is actively updated — do not assume it is live"): empirical inspection
on 2026-09-30 found the newest record dated 2023-09-30 — over two years stale. This is reported
honestly (STALE / VERY_STALE) rather than assumed live because the CKAN API itself answers HTTP
200; see docs/SOURCE_ENDPOINTS.md for the inspection trail.

Static station topology for this source comes from the packaged reference registry
(data/reference/, see ``bdo.repository.reference``), produced by
``scripts/export_reference_registry.py`` — this module only checks whether the *measurement*
stream is actually current, it does not re-derive topology.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from bdo.config import Settings
from bdo.enums import EvidenceClass, QualityFlag
from bdo.ingestion.adapters.bkk_ckan import CKANAdapter, CKANError
from bdo.live.base import LiveMeasurement, LiveSourceState, classify_live_health, unavailable_state
from bdo.util.time import ensure_utc, utcnow

SOURCE_KEY = "bkk_open_data_dds"
RESOURCE_ID = "313cd96f-8610-495d-875e-f6d0ec08a3bc"  # dds011_pak_khlong_talat_wl_max
STATION_EXTERNAL_ID = "dds011:pak_khlong_talat"


def fetch(settings: Settings, adapter: CKANAdapter | None = None) -> LiveSourceState:
    started = utcnow()
    cfg = settings.source(SOURCE_KEY)
    owns_adapter = adapter is None
    adapter = adapter or CKANAdapter(cfg, settings)
    try:
        try:
            result = adapter.datastore_search(RESOURCE_ID, limit=1, sort="_id desc")
        except (CKANError, httpx.HTTPError) as exc:
            return unavailable_state(SOURCE_KEY, adapter.active_base, started, str(exc))
    finally:
        if owns_adapter:
            adapter.close()

    records = result.get("records", [])
    if not records:
        return unavailable_state(SOURCE_KEY, adapter.active_base, started, "no records returned")
    rec = records[0]
    measurement_at = None
    try:
        # wl_date carries the full timestamp; wl_time duplicates its time-of-day component.
        measurement_at = ensure_utc(datetime.fromisoformat(rec["wl_date"]), "Asia/Bangkok")
    except (KeyError, ValueError):
        pass

    measurement = LiveMeasurement(
        source_key=SOURCE_KEY, external_station_id=STATION_EXTERNAL_ID,
        station_name="Chao Phraya River at Pak Khlong Talat (dds011)", node_type="river_boundary",
        variable="water_level_daily_max", value_num=rec.get("wl_max"), value_text=None, unit="m",
        measurement_at=measurement_at, evidence_class=EvidenceClass.OFFICIAL_REPORTED.value,
        quality_flags=frozenset({QualityFlag.UNIT_DECLARED_UNVERIFIED.value, QualityFlag.TZ_DECLARED_BY_CONFIG.value}),
        notes="CKAN batch-published dataset, not live telemetry — see docs/SOURCE_ENDPOINTS.md",
        source_url="https://data.bangkok.go.th/",
    )

    thresholds = settings.thresholds_for(SOURCE_KEY)
    health, freshness = classify_live_health(
        http_ok=True, record_count=1, freshest=measurement_at, thresholds=thresholds,
    )
    return LiveSourceState(
        source_key=SOURCE_KEY, endpoint=f"{adapter.active_base}datastore_search?resource_id={RESOURCE_ID}",
        fetch_started_at=started, fetch_finished_at=utcnow(), http_status=200, health=health,
        record_count=1, source_measurement_at=measurement_at, freshness=freshness,
        error=None, measurements=(measurement,),
    )
