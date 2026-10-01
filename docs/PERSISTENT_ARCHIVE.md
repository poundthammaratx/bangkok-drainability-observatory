# Persistent Archive (v0.3)

## What changed from v0.2

v0.2's live read-through layer (`src/bdo/live/`) was entirely transient: a GET against a public
endpoint, cached in memory for a few minutes, gone on process restart. If nobody opened the
Streamlit app, BDO never remembered anything. v0.3 adds a **collector** (`src/bdo/collector/`,
see `docs/COLLECTOR_ARCHITECTURE.md`) that runs the same live adapters independently of Streamlit
and persists what they return — so the observatory can build a continuous historical record
whether or not anyone is looking at it.

Nothing about the v0.1/v0.2 persisted-ingestion pipeline (`bdo.ingestion.*`, local CLI, SQLite by
default) changed. The collector is a **second caller** of the same repository write paths
(`bdo.repository.measurements.insert_measurements`, `bdo.repository.stations.upsert`,
`bdo.repository.snapshots.*`) — not a parallel, competing persistence mechanism.

## What is persisted, what is transient, what is raw, what is normalized

| | Persisted (survives restart) | Transient (per-process, TTL-bound) |
|---|---|---|
| Measurements | `measurements` table, via either CLI ingestion or the collector | `bdo.live.*` in-memory `LiveMeasurement` objects |
| Raw provenance | `raw_snapshots` — file-backed (local CLI) or inline `payload_text` (collector) | The HTTP response the adapter just received, discarded after parsing unless captured as a `RawPayloadCapture` |
| Station topology | `stations` table (ingested) + `data/reference/*.csv` (packaged snapshot) | Station fields embedded in a `LiveMeasurement` before the collector upserts them |
| Source health | `source_health_history` (new in v0.3) — one row per collector run | `LiveSourceState` — only the latest result, per-process |

"Normalized" means it has passed through `bdo.schemas.NormalizedMeasurement` validation (the same
Pydantic boundary the CLI ingestion pipeline has always used — see `docs/architecture.md`). "Raw"
means the original bytes as returned by the source, hashed and archived before normalization ever
touches them, exactly as the v0.1 provenance rules require.

## Third-party data and redistribution

Every persisted value remains attributable to its origin agency (BMA/DDS, HII/ThaiWater, TMD,
RID) via `Source.agency` and `Measurement.source_id`/`raw_snapshot_id` — unchanged from v0.1.
Inline-archived raw payloads (JSON API responses) are the same public data the source already
serves over an unauthenticated public endpoint; no credential-gated or licensed content is
captured. `reference_manifest.json` records the publisher's own stated licence for the static
registry (currently "License not specified" for the BKK CKAN dataset — not invented, carried
through verbatim). TMD's radar images are never stored (see §Raw snapshot preservation below) —
only their *availability* is checked — precisely to avoid any redistribution question around a
rendered visual product with unclear reuse terms.

## Deduplication and revisions (milestone §7)

**This is not new in v0.3** — it already existed in `bdo.repository.measurements` since v0.1, and
the collector simply reuses it (see `docs/COLLECTOR_ARCHITECTURE.md`). Restated here because the
milestone asks for it explicitly:

* **Natural key**: `(source_id, station_id, variable, measurement_at)`.
* **Fingerprint** (`bdo.util.hashing.measurement_dedup_key`): the natural key **plus** a
  canonicalised value (`num:1.2` / `txt:...`, so `1.20` and `1.2000000001` collide but `1.2` and
  `1.3` don't). When `measurement_at` is unknown, the source's own record id is folded in too, so
  two different undated records never collapse just because they happen to share a value.
* **Identical fingerprint already stored** → skipped as a duplicate. Re-running a collector twice
  in a row inserts nothing new (`tests/test_collector.py::test_identical_rerun_does_not_duplicate`).
* **Same natural key, different value** (a source correction) → a **second row**, its own
  `retrieved_at`, nothing overwritten
  (`tests/test_collector.py::test_source_correction_preserves_both_revisions`).

### Selecting the canonical current observation

`bdo.repository.measurements.current_observations()` is the v0.3 addition: one indexed SQL query
(a `ROW_NUMBER() OVER (PARTITION BY source_id, station_id, variable ORDER BY measurement_at DESC,
retrieved_at DESC)`) that picks, per natural key, the row with the greatest `measurement_at`,
tie-broken by the greatest `retrieved_at` — "the latest-retrieved version of the most recent
reading." This is what makes the next section's resolver fast: no pandas scan of the whole table.

## Current-state resolution (milestone §17A0)

`src/bdo/analytics/current_state.py` is the **one** place "what is current right now" is decided.
Overview, Map, and Live Situation all call `resolve_public_current_state()` (never their own
bespoke logic) so the same observation never disagrees across pages.

Rule, per `(source_key, external_station_id, variable)`:

1. **Persisted-only** → show the persisted canonical observation.
2. **Live-only** (no persisted row yet — e.g. right after a v0.3 deploy with no collector run
   yet) → show the live reading.
3. **Both** → whichever has the newer `measurement_at` wins **for display**. A transient live
   observation that happens to be newer is shown as current; it is **never written back** into
   the archive, and the archive is **never overwritten merely for display**
   (`tests/test_current_state_resolver.py::test_persisted_and_live_merge_prefers_newer_measurement`
   asserts the persisted row is untouched afterwards).

`resolve_public_current_state()` additionally drops SEED/DEMONSTRATION rows — the raw
`resolve_current_state()` includes them (with an explicit `demo` flag) for callers that
legitimately want full history, but every *public current-state* consumer must go through the
`_public` wrapper. This exists because of a real bug caught during v0.3 development: a SEED-only
natural key with nothing live superseding it would otherwise look exactly like a genuine current
reading (`tests/test_current_state_resolver.py::test_seed_demo_excluded_from_public_current_state`).

## Archive unavailability (milestone §18)

`bdo.database.archive_reachable()` is a cheap, short-lived `SELECT 1` that never raises. Every
archive-dependent page (`get_settings_cached()` at startup, Map, Live Situation) checks it (or
catches the query exception directly, in case the probe races a real failure) and degrades instead
of crashing:

* `get_settings_cached()` — bootstrap failure is logged and swallowed; the app still boots.
* Map — falls back to `bdo.ui.map_view.build_points_offline()` (packaged topology + live
  read-through, no database access at all).
* Live Situation — same fallback, plus an explicit "Persistent archive unavailable — live
  read-through only" banner; trends and the event strip (both archive-dependent) are hidden with
  an explanatory message rather than attempted.

Verified manually against an unreachable `postgresql+psycopg://` URL (see
docs/DATABASE_DEPLOYMENT.md's verification log) and in `tests/test_live_situation.py`.

## TODO

* PostGIS geometry column for `stations`/`field_observations` (plain lat/lon floats today, by
  design — see the portability note in `bdo/models.py`) — add once spatial queries are actually
  needed, not speculatively.
* Object-storage-backed raw snapshots for binary/large payloads (radar images) — currently these
  are recorded as metadata-only provenance (hash + size, no body); see
  `bdo.repository.snapshots.save_snapshot_inline`'s docstring.
