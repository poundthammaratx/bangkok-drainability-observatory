"""ThaiWater Bangkok live adapter — read-through against HII's public api-v3.thaiwater.net.

Discovered 2026-09-30 from ``bangkok.thaiwater.net/dist/js/app.js`` (no official API
documentation exists): a public, unauthenticated JSON API at
``https://api-v3.thaiwater.net/api/v1/thaiwater30/`` backs every ThaiWater provincial dashboard,
including Bangkok (``province_code=10``). Full discovery trail: docs/SOURCE_ENDPOINTS.md.

Endpoints used (province-scoped to Bangkok):
* ``provinces/rain24?include_zero=1&province_code=10`` — rain gauges: ``rain_24h``, ``rain_1h``,
  ``rainfall_datetime``.
* ``provinces/waterlevel?province_code=10`` — water-level stations: ``waterlevel_msl`` (+
  ``waterlevel_msl_previous``), ``waterlevel_datetime``, ``situation_level``, bank levels.

Timestamp semantics: both fields are naive ``"YYYY-MM-DD HH:MM"`` strings. Empirically — compared
against the system clock at inspection time (2026-09-30) — these are Asia/Bangkok local time, not
UTC. This is inferred, not documented by HII, and is flagged accordingly.

Trend: derived only from ``waterlevel_msl`` vs the source's own ``waterlevel_msl_previous`` field
— both published by HII, so comparing them is not an inference beyond what the source reports.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from bdo.config import Settings
from bdo.enums import EvidenceClass, QualityFlag
from bdo.live.base import (
    LiveMeasurement,
    LiveSourceState,
    RawPayloadCapture,
    classify_live_health,
    make_client,
    unavailable_state,
)
from bdo.util.time import ensure_utc, utcnow

SOURCE_KEY = "thaiwater_bangkok"
BASE_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/"
BANGKOK_PROVINCE_CODE = "10"
TIMESTAMP_ZONE = "Asia/Bangkok"  # empirically inferred; see module docstring


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return ensure_utc(datetime.strptime(raw, "%Y-%m-%d %H:%M"), TIMESTAMP_ZONE)
    except ValueError:
        return None


def fetch(settings: Settings, client: httpx.Client | None = None) -> LiveSourceState:
    started = utcnow()
    owns_client = client is None
    client = client or make_client(settings)
    try:
        try:
            rain_r = client.get(f"{BASE_URL}provinces/rain24",
                                params={"include_zero": 1, "province_code": BANGKOK_PROVINCE_CODE})
            rain_r.raise_for_status()
            wl_r = client.get(f"{BASE_URL}provinces/waterlevel", params={"province_code": BANGKOK_PROVINCE_CODE})
            wl_r.raise_for_status()
        except httpx.HTTPError as exc:
            return unavailable_state(SOURCE_KEY, BASE_URL, started, str(exc))

        try:
            rain_rows = rain_r.json().get("data", []) or []
            wl_rows = wl_r.json().get("data", []) or []
        except ValueError as exc:
            return unavailable_state(SOURCE_KEY, BASE_URL, started, f"invalid JSON: {exc}")

        raw_payloads = (
            RawPayloadCapture(label="rain24", content=rain_r.content, content_type="application/json",
                              request_url=str(rain_r.url), http_status=rain_r.status_code),
            RawPayloadCapture(label="waterlevel", content=wl_r.content, content_type="application/json",
                              request_url=str(wl_r.url), http_status=wl_r.status_code),
        )
    finally:
        if owns_client:
            client.close()

    measurements: list[LiveMeasurement] = []
    parse_errors = False
    freshest: datetime | None = None

    def _track(dt: datetime | None) -> None:
        nonlocal freshest
        if dt is not None and (freshest is None or dt > freshest):
            freshest = dt

    for r in rain_rows:
        station = r.get("station") or {}
        name = (station.get("tele_station_name") or {}).get("en") or (station.get("tele_station_name") or {}).get("th")
        measurement_at = _parse_dt(r.get("rainfall_datetime"))
        parse_errors = parse_errors or (r.get("rainfall_datetime") and measurement_at is None)
        _track(measurement_at)
        flags = {QualityFlag.TZ_DECLARED_BY_CONFIG.value}
        if measurement_at is None:
            flags.add(QualityFlag.MEASUREMENT_TIME_UNKNOWN.value)
        measurements.append(LiveMeasurement(
            source_key=SOURCE_KEY, external_station_id=station.get("tele_station_oldcode") or str(station.get("id")),
            station_name=name, node_type="rain_gauge", variable="rainfall_24h",
            value_num=r.get("rain_24h"), value_text=None, unit="mm", measurement_at=measurement_at,
            evidence_class=EvidenceClass.OFFICIAL_REPORTED.value,
            source_timestamp_raw=r.get("rainfall_datetime"), quality_flags=frozenset(flags),
            latitude=station.get("tele_station_lat"), longitude=station.get("tele_station_long"),
            district=((r.get("geocode") or {}).get("amphoe_name") or {}).get("en"),
            operator="Hydro-Informatics Institute", notes=f"rain_1h={r.get('rain_1h')} mm",
            source_url="https://bangkok.thaiwater.net/",
        ))

    for r in wl_rows:
        station = r.get("station") or {}
        name = (station.get("tele_station_name") or {}).get("en") or (station.get("tele_station_name") or {}).get("th")
        measurement_at = _parse_dt(r.get("waterlevel_datetime"))
        parse_errors = parse_errors or (r.get("waterlevel_datetime") and measurement_at is None)
        _track(measurement_at)
        flags = {QualityFlag.TZ_DECLARED_BY_CONFIG.value}
        if measurement_at is None:
            flags.add(QualityFlag.MEASUREMENT_TIME_UNKNOWN.value)
        current, previous = r.get("waterlevel_msl"), r.get("waterlevel_msl_previous")
        trend = "unknown"
        try:
            if current is not None and previous is not None:
                cur_f, prev_f = float(current), float(previous)
                trend = "rising" if cur_f > prev_f else ("falling" if cur_f < prev_f else "stable")
        except (TypeError, ValueError):
            pass
        try:
            value = float(current) if current is not None else None
        except (TypeError, ValueError):
            value = None
        measurements.append(LiveMeasurement(
            source_key=SOURCE_KEY, external_station_id=station.get("tele_station_oldcode") or str(station.get("id")),
            station_name=name, node_type="water_level_station", variable="water_level_msl",
            value_num=value, value_text=None, unit="m", measurement_at=measurement_at,
            evidence_class=EvidenceClass.OFFICIAL_REPORTED.value,
            source_timestamp_raw=r.get("waterlevel_datetime"), quality_flags=frozenset(flags),
            latitude=station.get("tele_station_lat"), longitude=station.get("tele_station_long"),
            district=((r.get("geocode") or {}).get("amphoe_name") or {}).get("en"),
            operator="Hydro-Informatics Institute",
            notes=f"trend={trend} (previous={previous}); situation_level={r.get('situation_level')}",
            source_url="https://bangkok.thaiwater.net/",
        ))

    thresholds = settings.thresholds_for(SOURCE_KEY)
    health, freshness = classify_live_health(
        http_ok=True, record_count=len(measurements), freshest=freshest, thresholds=thresholds,
        had_parse_errors=parse_errors,
    )
    return LiveSourceState(
        source_key=SOURCE_KEY, endpoint=f"{BASE_URL}provinces/{{rain24,waterlevel}}",
        fetch_started_at=started, fetch_finished_at=utcnow(), http_status=wl_r.status_code,
        health=health, record_count=len(measurements), source_measurement_at=freshest,
        freshness=freshness, error=None, measurements=tuple(measurements),
        context={"rain_stations": len(rain_rows), "waterlevel_stations": len(wl_rows)},
        raw_payloads=raw_payloads,
    )
