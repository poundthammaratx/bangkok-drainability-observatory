"""FloodBangkok live adapter — read-through against the public Directus-backed JSON API.

Discovered 2026-09-30 by inspecting the site's Next.js bundles (no official API documentation
exists): a public, unauthenticated JSON API at
``https://floodbangkok.bangkok.go.th/bkk/dds/services/api/floods/v1`` backs the dashboard shown
at https://floodbangkok.bangkok.go.th/. Full discovery trail: docs/SOURCE_ENDPOINTS.md.

Collections used:
* ``/items/sensor_profile`` — station registry: id, code, name, road, district, lat, long,
  device_status (normal | malfunction | flooding | minor_flood), main_road.
* ``/items/sensor_now`` — current per-sensor reading: sensor_profile (FK), flood_now (cm,
  declared-unverified unit), timestamp.

Timestamp semantics: ``sensor_now.timestamp`` carries no zone suffix. Empirically — compared
against the system clock at inspection time (2026-09-30, values ~5 minutes old) — it is UTC, not
Asia/Bangkok as most other BMA/DDS publications in this project are. This is inferred, not
documented by the source, and is flagged accordingly on every measurement.
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

SOURCE_KEY = "bma_floodbangkok"
BASE_URL = "https://floodbangkok.bangkok.go.th/bkk/dds/services/api/floods/v1"
TIMESTAMP_ZONE = "UTC"  # empirically inferred; see module docstring


def fetch(settings: Settings, client: httpx.Client | None = None) -> LiveSourceState:
    started = utcnow()
    owns_client = client is None
    client = client or make_client(settings)
    try:
        try:
            profiles_r = client.get(f"{BASE_URL}/items/sensor_profile", params={"limit": -1})
            profiles_r.raise_for_status()
            now_r = client.get(f"{BASE_URL}/items/sensor_now", params={"limit": -1})
            now_r.raise_for_status()
        except httpx.HTTPError as exc:
            return unavailable_state(SOURCE_KEY, BASE_URL, started, str(exc))

        try:
            profiles = {p["id"]: p for p in profiles_r.json().get("data", [])}
            readings = now_r.json().get("data", [])
        except ValueError as exc:
            return unavailable_state(SOURCE_KEY, BASE_URL, started, f"invalid JSON: {exc}")

        raw_payloads = (
            RawPayloadCapture(label="sensor_profile", content=profiles_r.content,
                              content_type="application/json", request_url=str(profiles_r.url),
                              http_status=profiles_r.status_code),
            RawPayloadCapture(label="sensor_now", content=now_r.content, content_type="application/json",
                              request_url=str(now_r.url), http_status=now_r.status_code),
        )
    finally:
        if owns_client:
            client.close()

    measurements: list[LiveMeasurement] = []
    parse_errors = False
    freshest: datetime | None = None
    status_counts: dict[str, int] = {}
    for p in profiles.values():
        status_counts[p.get("device_status") or "unknown"] = status_counts.get(p.get("device_status") or "unknown", 0) + 1

    for r in readings:
        profile = profiles.get(r.get("sensor_profile"))
        if profile is None:
            parse_errors = True
            continue
        measurement_at = None
        raw_ts = r.get("timestamp")
        if raw_ts:
            try:
                measurement_at = ensure_utc(datetime.fromisoformat(raw_ts), TIMESTAMP_ZONE)
            except ValueError:
                parse_errors = True
        if measurement_at is not None and (freshest is None or measurement_at > freshest):
            freshest = measurement_at
        try:
            value = float(r["flood_now"]) if r.get("flood_now") not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        flags = {QualityFlag.UNIT_DECLARED_UNVERIFIED.value}
        if raw_ts:
            flags.add(QualityFlag.TZ_DECLARED_BY_CONFIG.value)
        if measurement_at is None:
            flags.add(QualityFlag.MEASUREMENT_TIME_UNKNOWN.value)
        measurements.append(LiveMeasurement(
            source_key=SOURCE_KEY,
            external_station_id=profile.get("code"),
            station_name=profile.get("name"),
            node_type="road_flood_sensor",
            variable="road_flood_depth",
            value_num=value,
            value_text=None,
            unit="cm",
            measurement_at=measurement_at,
            evidence_class=EvidenceClass.OFFICIAL_REPORTED.value,
            quality_flags=frozenset(flags),
            latitude=profile.get("lat"),
            longitude=profile.get("long"),
            district=profile.get("district"),
            operator="Bangkok Metropolitan Administration — Department of Drainage and Sewerage",
            notes=f"device_status={profile.get('device_status')}",
            source_url="https://floodbangkok.bangkok.go.th/",
        ))

    thresholds = settings.thresholds_for(SOURCE_KEY)
    health, freshness = classify_live_health(
        http_ok=True, record_count=len(measurements), freshest=freshest, thresholds=thresholds,
        had_parse_errors=parse_errors,
    )
    return LiveSourceState(
        source_key=SOURCE_KEY, endpoint=f"{BASE_URL}/items/sensor_now", fetch_started_at=started,
        fetch_finished_at=utcnow(), http_status=now_r.status_code, health=health,
        record_count=len(measurements), source_measurement_at=freshest, freshness=freshness,
        error=None, measurements=tuple(measurements),
        context={"device_status_counts": status_counts, "sensor_count": len(profiles)},
        raw_payloads=raw_payloads,
    )
