# Source Endpoints — Discovery Notes (v0.2)

Inspected 2026-09-30 from an environment with outbound internet access. Every endpoint below was
queried directly with `curl`/`httpx` and the response is quoted or summarised from the actual
result, not from documentation (none of these four sites publish developer docs). Where no stable
structured endpoint exists, that is stated explicitly — this project does not claim an API where
only HTML/JS was found.

---

## 1. BKK Open Data — Department of Drainage and Sewerage (`bkk_open_data_dds`)

* **Official URL:** https://data.bangkok.go.th/ (mirrored on the national https://data.go.th/)
* **Structured endpoint:** CKAN Action API, already implemented (`bdo.ingestion.adapters.bkk_ckan`)
  and reused read-only by `bdo.live.adapters.bkk_live` and `scripts/export_reference_registry.py`.
* **Method:** `GET .../api/3/action/datastore_search?resource_id=...`
* **Cadence:** Per-resource. `dds_telemetry_stations` (station registry) is metadata, updated
  irregularly (`metadata_modified` 2024-07-31 at inspection). `dds011_pak_khlong_talat_wl_max` is
  nominally a frequent water-level series but is **batch-published historical data, not
  telemetry** — see below.
* **Timestamp semantics:** `wl_date` (dds011) is a full ISO-like timestamp with no offset;
  declared Asia/Bangkok by config (`sources.yaml`), flagged `TZ_DECLARED_BY_CONFIG`. `wl_time`
  duplicates its time-of-day component.
* **Fields consumed:** station registry — `station_code`, `station_name`, `station_lat/long`,
  `District`, `station_type`. dds011 — `wl_date`, `wl_time`, `wl_max`.
* **Freshness finding (milestone item E.1 — "do not assume it is live"):** queried
  `dds011_pak_khlong_talat_wl_max` sorted by `_id desc`; the newest of 273 records is dated
  **2023-09-30**, over two years before this inspection. `bkk_live.fetch()` reports this honestly
  as `STALE`/`VERY_STALE` — the CKAN API itself answers HTTP 200 the whole time.
* **New discovery beyond v0.1:** `dds_flood_risk_points` (resource id
  `8f1102d5-52a2-4494-9131-403e4f87a242`), previously `enabled: false` with the note "field schema
  not yet inspected", was inspected this round: 737 records with genuine `x`/`y` coordinates,
  `name`, `district`, `status_num`/`status_detail` (flood-risk-point status), several Thai-only
  field names. **Not wired into any pipeline yet** — flagging it here per the source-admission
  policy in `docs/source_policy.md` (§2) as a documented, schema-inspected candidate for a future
  resource entry, out of scope for this milestone's ~803/582 static-registry target.
* **Known limitations:** unchanged from v0.1 (units/datum unverified, day/month transposition
  repair on dds011, `station_type` codes undocumented).
* **Fallback strategy:** static topology from `data/reference/bma_telemetry_stations.csv` /
  `bma_drainage_assets.csv`; `bkk_live` degrades to `UNAVAILABLE` on request failure like any
  other adapter.

---

## 2. FloodBangkok (`bma_floodbangkok`)

* **Official URL:** https://floodbangkok.bangkok.go.th/
* **Structured endpoint found:** **Yes** — a public, unauthenticated Directus-style JSON REST API:
  `https://floodbangkok.bangkok.go.th/bkk/dds/services/api/floods/v1`. Discovered by downloading
  every `_next/static/chunks/*.js` bundle referenced from the site's Next.js shell and grepping
  for absolute URL literals; the route map (`sensor.flood`, `sensor.profile`, etc.) was found as a
  plain object literal in one bundle.
* **Method:** `GET /items/sensor_profile?limit=-1` (station registry, 254 rows at inspection: id,
  code, name, road, district, lat, long, device_status, main_road) and
  `GET /items/sensor_now?limit=-1` (current reading per station: sensor_profile FK, flood_now,
  flood_min/max, timestamp, door, interval). A `sensor_flood` collection (historical log) and a
  WebSocket (`wss://.../websocket`) also exist but are not used in v0.2.
* **Cadence:** Sensor `interval` fields observed at 300000–600000 ms (5–10 min); the freshest
  reading at inspection was ~3.5 minutes old. TTL set to 180s.
* **Timestamp semantics:** `sensor_now.timestamp` has no offset suffix (e.g.
  `"2026-09-30T17:40:00"`). System clock at inspection was `2026-09-30T17:45:55Z`; the value was
  ~6 minutes behind that in UTC terms and would have been ~7 hours behind if read as Asia/Bangkok
  — **empirically UTC**, not documented, flagged `TZ_DECLARED_BY_CONFIG` on every measurement.
* **Fields consumed:** `code`, `name`, `road`, `district`, `lat`, `long`, `device_status` (profile);
  `flood_now`, `timestamp`, `sensor_profile` FK (reading).
* **Status distribution at inspection:** `device_status` ∈ `normal` (221), `malfunction` (17),
  `flooding` (11), `minor_flood` (5) — this directly answers the milestone's "working/failed
  sensor counts, flooded/slight-ponding/normal counts" requirement; surfaced in
  `LiveSourceState.context["device_status_counts"]`.
* **Known limitations:** `flood_now`'s unit is not stated anywhere in the API; declared `"cm"`
  (consistent with the BKK CKAN road-flood-depth variable) and flagged
  `UNIT_DECLARED_UNVERIFIED`. No per-station history endpoint is used (only current state).
* **Fallback strategy:** on failure, `bdo.live.manager` serves the last successful fetch marked
  `DEGRADED`; if none exists yet, the map simply omits this layer (it has no static-topology
  counterpart — FloodBangkok's sensor network is not part of the BKK CKAN reference registry).

---

## 3. ThaiWater Bangkok (`thaiwater_bangkok`)

* **Official URL:** https://bangkok.thaiwater.net/
* **Structured endpoint found:** **Yes** — HII's national API, shared by every ThaiWater
  provincial site: `https://api-v3.thaiwater.net/api/v1/thaiwater30/`. Discovered by downloading
  `bangkok.thaiwater.net/dist/js/app.js` (a 3.1 MB bundled jQuery/React app) and grepping for
  `api-v3.thaiwater.net` string literals — dozens of endpoints are called from this one bundle;
  only the two relevant to Bangkok surface state are used here.
* **Method:** `GET provinces/rain24?include_zero=1&province_code=10` (rain gauges) and
  `GET provinces/waterlevel?province_code=10` (water-level stations); `10` is Thailand's
  standard province code for Bangkok (กรุงเทพมหานคร), confirmed from the `geocode.province_code`
  field echoed back in each response.
* **Cadence:** rain data is hourly-aggregated (`rainfall_24h`); water-level readings observed
  ~15–30 minutes old at inspection. TTL set to 300s.
* **Timestamp semantics:** both `rainfall_datetime` and `waterlevel_datetime` are naive
  `"YYYY-MM-DD HH:MM"` strings. System clock at inspection was `2026-09-30T17:45:55Z` =
  `2026-10-01 00:45:55` Asia/Bangkok; a `rainfall_datetime` of `"2026-10-01 00:00"` and a
  `waterlevel_datetime` of `"2026-10-01 00:30"` both align almost exactly with the Bangkok-local
  wall clock — **empirically Asia/Bangkok**, not documented, flagged `TZ_DECLARED_BY_CONFIG`.
* **Fields consumed:** rain — `rain_24h`, `rain_1h`, `rainfall_datetime`, `station.tele_station_*`.
  Water level — `waterlevel_msl`, `waterlevel_msl_previous`, `waterlevel_datetime`,
  `situation_level`, bank-level fields, `station.tele_station_*`.
* **Trend:** derived only by comparing `waterlevel_msl` to the source's own
  `waterlevel_msl_previous` — both HII-published values; this is not an inference beyond what HII
  itself reports on the same record.
* **Known limitations:** `situation_level` (an HII risk indicator, 1–5 observed) is captured in
  free-text notes only, not interpreted or acted on. Reservoir/dam context endpoints
  (`provinces/dam*`) exist in the same API but are out of scope for this milestone.
* **Fallback strategy:** same DEGRADED-on-failure pattern as FloodBangkok; no static-topology
  fallback (ThaiWater's station network is not part of the BKK CKAN reference registry either).

---

## 4. TMD Radar (`tmd_bangkok_radar`)

* **Official URL:** https://weather.tmd.go.th/THA_Z.php
* **Structured endpoint found:** **No.** The nationwide composite image referenced on that page
  (`compositeZC_VTBB_latest.png`) returned HTTP 404 when fetched directly from this build
  environment (possibly hotlink protection or a routing quirk — noted as a TODO in
  `docs/LIVE_DATA_ARCHITECTURE.md`). Two BMA-operated station pages, reachable directly, exist:
  * Nong Chok — page `bma_nck.php` → image `https://weather.tmd.go.th/pic_bmanck.jpg`
  * Nong Khaem — page `bma_nkm.php` → image `https://weather.tmd.go.th/pic_bmankm.jpg`
* **Method:** `HEAD` (falling back to a ranged `GET`) against the two image URLs above —
  availability only, no parsing.
* **Cadence:** unknown; TTL set to 300s as a reasonable poll interval for a weather-radar product.
* **Timestamp semantics:** THA_Z.php carries an HTML-comment disclaimer ("Date/time shown here is
  given as UTC") that is not currently rendered on the live page, and neither radar image returned
  an HTTP `Last-Modified` header at inspection. **No machine-readable product timestamp exists**;
  `measurement_at` is always `None` and health is `UNKNOWN` whenever the images are reachable —
  never `HEALTHY`, since there is no timestamp to judge freshness by.
* **Known limitations:** per milestone instruction, this is an image/product layer only — no
  rainfall-intensity estimation from radar pixels is performed or planned in v0.2.
* **Fallback strategy:** `UNAVAILABLE` only if *both* images fail; one reachable image is reported
  as `DEGRADED` (partial), matching the "never fully green, always qualified" health philosophy.

---

## 5. RID Water Situation (`rid_water_situation`)

* **Official URL:** https://www.rid.go.th/th/water-situation
* **Structured endpoint found:** **No stable public API for this bulletin.** The page is a
  server-rendered Nuxt app; its figures are embedded directly in the HTML as a serialized payload
  (`<script id="__NUXT_DATA__" type="application/json" data-nuxt-data="nuxt-app">`, ~22 KB, a
  compact array-referencing format rather than a conventional JSON API). Downloading and grepping
  every `_rid_main/*.js` Vite chunk found only two relative API routes:
  `/api/cache/irrigation` (national crop-area planting summary) and `/api/cache/reservoir`
  (national dam/reservoir aggregate) — both national-level, not Bangkok/Chao-Phraya-specific, and
  therefore not what this source is registered for (dam discharge / river discharge / Chao Phraya
  system context). Interestingly, both of those endpoints *do* self-report cache staleness
  (`{"stale": true, "age": 4714}` seconds observed at inspection) — a good precedent this project
  would like RID to extend to the bulletin itself, but it does not help build a Bangkok-scoped
  measurement today.
* **Method (v0.2):** plain `GET` of the page for reachability only
  (`bdo.live.adapters.rid_live`); no field is extracted, no measurement is produced.
* **Cadence:** daily bulletin; polled no faster than every 1800s (30 min) regardless.
* **Timestamp semantics:** not applicable — nothing is parsed from the page in v0.2.
* **Known limitations:** this is the least-developed of the five live adapters, deliberately —
  the milestone explicitly deprioritises RID ("treat as slower upstream/boundary condition
  context... capture only measurements with explicit authoritative timestamps"). Persisted data
  for this source remains MANUAL transcription (`data/manual/rid_water_situation/`), unchanged
  from v0.1.
* **Fallback strategy:** `DEGRADED` if the page is reachable (nothing to be "healthy" about —
  no data was extracted), `UNAVAILABLE` if not. Never blocks any other part of the app.

---

## Summary table

| Source | Structured endpoint? | Auth | Live in v0.2? | TTL |
|---|---|---|---|---|
| BKK Open Data (DDS) | Yes (CKAN, pre-existing) | None | Freshness-check only (see finding above) | 300s |
| FloodBangkok | **Yes (newly discovered)** | None | Full — 254 stations, ~3–15 min old | 180s |
| ThaiWater Bangkok | **Yes (newly discovered)** | None | Full — 24 stations, ~15–60 min old | 300s |
| TMD Radar | No (image only) | None | Product availability only, no timestamp | 300s |
| RID Water Situation | No (Nuxt SSR payload, not an API) | None | Reachability only | 1800s |
