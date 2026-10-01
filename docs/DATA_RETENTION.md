# Data Retention and Scale (v0.3)

Order-of-magnitude projections only — **not observed data**. Nothing below has been measured
against a running production collector; see docs/COLLECTOR_ARCHITECTURE.md's "no scheduler is
active today" note. Recompute these once real collection history exists.

## Assumptions

* ~280 stations currently reachable across live-adapter sources observed during v0.3 development
  (254 FloodBangkok + ~24 ThaiWater; BKK CKAN and TMD/RID contribute far fewer *measurement* rows
  per cycle even though the static topology is larger — see docs/SOURCE_CADENCE.md).
* Collection cadence per docs/SOURCE_CADENCE.md: FloodBangkok every 5 min, ThaiWater every 5–10
  min, TMD every 5 min (product-availability checks only, no per-value rows), RID every 30–60 min,
  BKK CKAN polled defensively but not assumed live.
* Each collection cycle inserts roughly one `measurements` row per reporting station-variable
  (fewer when values are unchanged and deduplication skips them — see
  docs/PERSISTENT_ARCHIVE.md §Deduplication) plus 1–2 `raw_snapshots` rows and one
  `source_health_history` row per source per cycle, regardless of dedup outcome.

## Projection (FloodBangkok + ThaiWater only — the two sources with real per-station value rows)

| | Records/day | Records/month | Records/year |
|---|---|---|---|
| `measurements` (FloodBangkok, 254 stations × ~288 cycles/day) | ~73,000 | ~2.2M | ~27M |
| `measurements` (ThaiWater, ~24 stations × ~144–288 cycles/day) | ~3,500–7,000 | ~0.1–0.2M | ~1.3–2.6M |
| `raw_snapshots` (2 payloads/cycle × 2 sources × ~288 cycles/day) | ~1,150 | ~35,000 | ~420,000 |
| `source_health_history` (5 sources × ~288 cycles/day) | ~1,440 | ~43,000 | ~525,000 |

**These are upper bounds that assume every cycle inserts a full new row for every station** — in
practice, deduplication means a station reporting an unchanged value inserts nothing, so real
growth will be lower than this, especially for `measurements`. The `measurements` row count is the
dominant cost by at least an order of magnitude over the other tables.

At a rough 200–400 bytes/row (SQLAlchemy/Postgres row overhead plus the columns actually used),
`measurements` alone projects to roughly **5–10 GB/year** at the upper-bound estimate above —
modest for a single managed PostgreSQL instance, and nowhere near needing distributed storage.

## What this does *not* include

* BKK CKAN and RID contribute negligible measurement volume (BKK's live-checked dataset is a
  single freshness probe per cycle, not a station sweep; RID persists nothing yet — see
  docs/SOURCE_ENDPOINTS.md). TMD contributes no `measurements` rows at all (image-availability
  check only).
* Field observations and manual/seed data — tiny, human-paced volumes, irrelevant to this
  projection.
* Any future source added beyond the current five.

## Future options (not implemented — do not build ahead of need)

* **Time-based partitioning** of `measurements` (e.g. monthly Postgres partitions) once the table
  reaches tens of millions of rows and query latency on recent-data filters (which is most queries
  — see `current_observations()` and the Live Situation trend windows) starts to degrade. Not
  needed at the projected year-one scale above.
* **Compression** — Postgres TOAST already compresses large text fields (e.g. `payload_text`)
  automatically; explicit columnar compression is a later-stage option if `raw_snapshots` grows
  faster than expected.
* **Cold archival** — move `measurements`/`raw_snapshots` older than some retention window (e.g.
  2 years) to cheaper storage. No retention policy is defined or enforced in v0.3; nothing is ever
  deleted automatically.
* **Parquet export** — a periodic `COPY ... TO` / pandas export of `measurements` for
  analysis/backup outside the live database, independent of the live schema. Not implemented; the
  existing CSV export on the Event Archive page covers ad hoc research needs for now.

None of the above is implemented in v0.3. They are documented here because the milestone asks for
the options to be on record, not because current volume requires any of them.
