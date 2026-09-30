# Live Data Architecture (v0.2)

## Why this exists

The v0.1 public alpha's map and "current state" depended on the local SQLite database having
been populated by a researcher running `scripts/ingest.py` by hand. Streamlit Community Cloud
gives a fresh, possibly-empty database on every deploy/reboot, and `PUBLIC_DEPLOYMENT=true`
intentionally disables ingestion (see `bdo.util.access`). A map or "current state" view that only
reads from that database would therefore be blank on a fresh public deployment.

v0.2 splits the problem in two, and neither half depends on the other being populated:

| | STATIC / REFERENCE TOPOLOGY | DYNAMIC / LIVE STATE |
|---|---|---|
| What | Station/system-node positions and identity | Current values, read fresh on each request |
| Source | `data/reference/*.csv`, packaged with the repo | Public official endpoints, GET on demand |
| Freshness | Effectively "as of the last export run" | Seconds to minutes old, per source TTL |
| Persisted? | Yes — tracked in git | **Never** — held in memory only, per process |
| Produced by | `scripts/export_reference_registry.py` | `src/bdo/live/` |
| Consumed by | `bdo.repository.reference` | `bdo.live.manager` |

The persisted ingestion pipeline (`bdo.ingestion.*`, `bdo.repository.*`, the `sources` /
`stations` / `measurements` tables) is unchanged and still the source of truth once a researcher
has actually run it locally — see `docs/architecture.md`. Live data is a **third**, independent
data plane, not a replacement.

## The static reference registry

`scripts/export_reference_registry.py` runs the existing, tested `CKANAdapter`
(`bkk_open_data_dds`) as a plain read-through: `fetch()` + `normalize()` only, no `RawSnapshot`
archiving, no database writes. It writes:

* `data/reference/bma_telemetry_stations.csv` — the coordinate-bearing telemetry point network
  (582 stations at last export: water level, rain gauge, and a handful of other telemetry types).
* `data/reference/bma_drainage_assets.csv` — everything else the same CKAN source produces (the
  dds011 fixed river-boundary station, per-record road-flood-statistics nodes) — mostly **without**
  published coordinates, included anyway so the catalogue is complete, never plotted on the map.
* `data/reference/reference_manifest.json` — provenance: retrieved_at, CKAN resource ids, record
  counts, a SHA-256 of each CSV, and the licence/attribution actually returned by `package_show`
  (never invented — BMA does not publish a licence for this dataset, and the manifest says so).

Re-run it whenever you want a fresher snapshot:

```bash
python scripts/export_reference_registry.py
```

It is **not** run automatically at Streamlit startup — the checked-in CSVs are the snapshot the
public deployment ships with. `bdo.repository.reference.load_topology()` /
`load_coordinate_topology()` read them with no network and no database.

## The live read-through layer (`src/bdo/live/`)

```
src/bdo/live/
    base.py       LiveMeasurement, LiveSourceState, LiveHealth, classify_live_health()
    cache.py      TTLCache — a minimal, framework-agnostic per-process cache
    manager.py    get_live_state() / get_all_live_states() — caching + fallback, the only
                  module the UI imports
    adapters/
        bkk_live.py            honesty check on the "5-minute" CKAN water-level dataset
        floodbangkok_live.py   real Directus JSON API (road flood sensors)
        thaiwater_live.py      real HII api-v3 JSON API (rain + water level, Bangkok)
        tmd_radar_live.py      radar product image reachability (no value extraction)
        rid_live.py            page-reachability only; no structured endpoint found
```

Every adapter exposes one function, `fetch(settings, client=None) -> LiveSourceState`, that:

1. never raises (`manager.py` also catches anything that slips through — an adapter bug must
   never crash a page);
2. never touches the database or filesystem;
3. classifies its own health from the **data timestamp**, not from the HTTP status alone
   (`classify_live_health()` in `base.py`, reusing `bdo.analytics.freshness.classify_age` and each
   source's configured thresholds — the same freshness logic the persisted pipeline uses).

### Health enum

| Value | Meaning |
|---|---|
| `HEALTHY` | Reachable, data extracted, freshest measurement is LIVE or RECENT |
| `DEGRADED` | Reachable with partial/parse issues, or serving a last-known-good result after a failure |
| `STALE` | Reachable, data extracted, but the freshest measurement is STALE or VERY_STALE |
| `UNAVAILABLE` | Request failed (network error, non-2xx, invalid JSON) |
| `UNKNOWN` | Reachable but no measurement timestamp exists to judge freshness (e.g. TMD's radar images) |

**HTTP 200 is never sufficient for `HEALTHY`.** `bdo_open_data_dds`'s "5-minute" water-level
dataset is a concrete example: the CKAN API answers 200 every time, but its newest record is
dated 2023-09-30 — `bkk_live` reports `STALE` (in practice `VERY_STALE`) for it, honestly.

### Caching and fallback (item I)

`manager.py` holds one process-wide `TTLCache`. `get_live_state(settings, source_key)`:

1. Returns the cached state if it is younger than that source's configured TTL
   (`Settings.live_ttl_for()`, from `config/settings.yaml` → `live.ttl_seconds`).
2. Otherwise calls the adapter. On success, the result is cached both as "current" (TTL-bound)
   and as "last known good" (kept indefinitely, overwritten only by the next success).
3. On failure, if a last-known-good result exists, it is returned **marked `DEGRADED`** with an
   explanatory `error` message ("showing last successful result from Ns ago"). If none exists yet,
   an `UNAVAILABLE` state is returned. **SEED/DEMO data is never substituted** — a failed live
   source shows either its own stale-but-real last result, or nothing.

This is also the performance mechanism (item K): repeated Streamlit reruns, widget interactions,
or multiple pages reading the same source within one TTL window issue exactly one HTTP request.

### Timestamp semantics (read before touching an adapter)

None of the four external sites document their timestamp timezone in machine-readable form.
Every zone below was **inferred empirically** (comparing a fetched value against the system clock
at inspection time, 2026-09-30) and is flagged `TZ_DECLARED_BY_CONFIG` on every measurement it
produces — treat it as a working assumption, not a documented fact. See
`docs/SOURCE_ENDPOINTS.md` for the inspection trail.

| Source | Field | Inferred zone |
|---|---|---|
| FloodBangkok | `sensor_now.timestamp` | UTC (no offset in the string) |
| ThaiWater | `rainfall_datetime`, `waterlevel_datetime` | Asia/Bangkok |
| BKK CKAN (dds011) | `wl_date` | Asia/Bangkok (matches the rest of this project's BKK sources) |
| TMD radar | — | No machine-readable timestamp found at all; `measurement_at` is always `None` |

## PUBLIC_DEPLOYMENT and the live layer

`PUBLIC_DEPLOYMENT=true` blocks **persistence** — `bdo.util.access.assert_writes_allowed()`,
called from `bdo.cli.ingest_cmd` and `bdo.repository.observations.import_csv`. It does **not**
touch `bdo.live`: a read-only `GET` against an official public endpoint is explicitly allowed
under PUBLIC_DEPLOYMENT (milestone item L) because it writes nothing anywhere — no
`RawSnapshot`, no database row, no file. `tests/test_live_data.py::test_public_deployment_permits_read_only_get_but_blocks_writes`
pins this boundary down.

## TODO

* Persist the live layer's observations somewhere durable (external PostgreSQL/PostGIS, per
  `docs/DEPLOY_STREAMLIT.md`'s TODO) once that migration happens — today every live value is
  genuinely transient and disappears when its TTL entry is evicted or the process restarts.
* RID: parse the Nuxt `__NUXT_DATA__` SSR payload on the water-situation page properly, or find an
  actual Bangkok/Chao-Phraya-scoped structured endpoint (`/api/cache/irrigation` and
  `/api/cache/reservoir` exist but are national aggregates, not the dam-discharge bulletin this
  source is registered for).
* TMD: investigate whether the nationwide composite product
  (`compositeZC_VTBB_latest.png`, referenced from `THA_Z.php`) can be fetched directly — it 404'd
  from this build environment while the two BMA-operated station images did not; may be a
  hotlink-protection or routing quirk worth revisiting.
