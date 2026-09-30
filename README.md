# Bangkok Drainability Observatory — v0.1

Working research title: **Urban Drainability Architecture and Recovery Dynamics of Bangkok**

> **This system is a research observatory and is not an official flood-warning service.**
>
> This software is an independent research project. It is not an official Bangkok Metropolitan
> Administration, Royal Irrigation Department, Thai Meteorological Department, or Hydro-Informatics
> Institute service.

## What it is

A local, research-grade observatory that:

1. aggregates heterogeneous Bangkok / Lower Chao Phraya flood and drainage information;
2. preserves source provenance and byte-exact historical snapshots;
3. keeps **measurement time** (what the source says the value represents) separate from
   **retrieval time** (when we obtained it);
4. normalises data without touching the raw source;
5. visualises system state and its history;
6. is the software foundation for later drainability / hydraulic modelling.

## What it is NOT

* Not a flood-warning, forecasting or alerting service.
* Not a source of operational recommendations (pump, gate or evacuation decisions). No hydraulic
  metric is computed before model validation (Phase 3–6).
* Not an official channel of any agency. Values are reproduced from public sources with provenance;
  the originating agency remains authoritative.

## Architecture (v0.1)

```
config/sources.yaml ──► adapter.healthcheck() ─► adapter.fetch() ─► RawSnapshot (data/raw, immutable)
                                                                         │ committed BEFORE parsing
                                                  adapter.normalize() ◄──┘
                                                         │ pydantic validation
                                                         ▼
                                       dedup (source, station, variable, measurement_at, value)
                                                         ▼
                                     SQLite: sources · stations · measurements · field_observations
                                             raw_snapshots · ingest_runs · derived_metrics (empty)
                                                         ▼
                                         Streamlit (read-only): 6 pages
```

Package layout: `src/bdo/` — `models.py` (ORM), `schemas.py` (validation), `enums.py`,
`ingestion/` (runner + adapters), `normalization/`, `repository/`, `analytics/` (freshness; trends;
recovery placeholders), `ui/`, `util/`. Details: [docs/architecture.md](docs/architecture.md),
[docs/data_model.md](docs/data_model.md), [docs/source_policy.md](docs/source_policy.md),
[docs/research_scope.md](docs/research_scope.md).

## Installation

Requires Python ≥ 3.12.

```bash
cd bangkok-drainability-observatory
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                 # optional; defaults work without it
```

## Initialise the database and load sources

```bash
python scripts/init_db.py            # create data/bdo.sqlite and data/ folders (idempotent)
python scripts/seed_sources.py       # register the 7 sources + load SEED demonstration records
python scripts/seed_sources.py --no-demo   # register sources only
```

The SEED records (RID bulletin 2026-09-30 08:00 ICT; FloodBangkok dashboard 2026-09-30 13:01 ICT)
live in `data/manual/*/SEED_*.csv`. They are labelled `SEED_DEMONSTRATION`, are **historical**, and
are never shown as LIVE; their freshness is their true age.

## Run the app

```bash
streamlit run app.py                 # http://localhost:8501
```

Pages: Overview · Map · Stations · Event Archive · Research (dev) · Data Quality.

## Ingest sources

```bash
python scripts/ingest.py --source bkk_open_data_dds       # CKAN (the only automatic adapter in v0.1)
python scripts/ingest.py --all                            # every source with enabled: true
python scripts/ingest.py --source bma_floodbangkok --manual           # data/manual/bma_floodbangkok/*.csv
python scripts/ingest.py --source rid_water_situation --file path/to/transcription.csv
bdo discover bkk_open_data_dds --q "สำนักการระบายน้ำ"      # list CKAN datasets + resource IDs
bdo status                                                # table counts + source status
```

`bdo` is the same CLI installed as a console script (`bdo --help`). Every run writes an
`ingest_runs` row; per-record problems are listed in `data/processed/ingest_errors/run_<id>.txt`;
logs go to the console and `data/processed/bdo.log`.

To add a CKAN dataset: run `bdo discover`, then add a `resources:` entry to
`config/sources.yaml` (resource ID, field mapping, units, time handling). No Python change needed.

## Import field observations

```bash
python scripts/import_field_observations.py path/to/observations.csv
python scripts/import_field_observations.py path/to/observations.csv --strict   # all-or-nothing
```

Header template: `data/manual/field_observations_TEMPLATE.csv`

```
observation_id,observed_at,location_name,latitude,longitude,district,evidence_class,phenomenon,
water_depth_cm,passability,trend,photo_ref,notes,source_url
```

* `observed_at` without offset is read as Asia/Bangkok.
* `evidence_class` ∈ `OBSERVED | OFFICIAL_REPORTED | THIRD_PARTY_REPORTED | MODELLED | ASSUMED` (exact).
* `passability` ∈ `normal | pedestrian_only | motorcycle_only | high_clearance_only | impassable | unknown`.
* `trend` ∈ `rising | stable | falling | dry | unknown`.
* Invalid rows are rejected and listed in `data/exports/field_obs_import_errors_<UTC>.csv`; the file
  itself is archived as a raw snapshot regardless. Re-importing is idempotent.

## Data and provenance rules

1. **Raw first.** Every retrieved payload is written to
   `data/raw/<source_key>/<YYYY>/<MM>/<DD>/<UTCSTAMP>_<sha12>_<label>.<ext>` (UTC date folders) with
   exclusive-create; files are never overwritten. SHA-256 is stored and can be verified from the Data
   Quality page.
2. **Two clocks.** `measurement_at` = time the source says the value represents (NULL if unknown;
   never back-filled). `retrieved_at` = when this system (or a human transcriber) obtained it.
3. **Freshness = now − measurement_at**, with per-source thresholds; unknown time → `UNKNOWN`;
   future time → `UNKNOWN` + flag; SEED data never `LIVE`.
4. **Evidence class** is mandatory (NOT NULL + CHECK) and never converted.
5. **Transformations are flagged**, the as-published value kept in `notes`: declared time zone
   (`TZ_DECLARED_BY_CONFIG`), declared units (`UNIT_DECLARED_UNVERIFIED`), day/month transposition
   (`DATE_DM_AMBIGUOUS`, `DATE_REPAIRED_DM_SWAP`), etc.
6. **Dedup** on (source, station, variable, measurement_at, canonical value) — never on retrieval
   time. A revised value for the same time is kept and shown as a conflict.
7. **No invented data.** No coordinates, units, datums or timestamps are made up. Adapters that
   cannot retrieve reliably report `MANUAL`/`UNAVAILABLE` and insert nothing.

## Source status in v0.1

| source_key | adapter | v0.1 state |
|---|---|---|
| `bkk_open_data_dds` | `ckan` | **Implemented** (package_search / package_show / datastore_search, mirror fallback). 3 resources configured from a 2026-09-30 inspection; 2 more listed but disabled until their schemas are inspected. Verified offline against verbatim records; not yet run against the live API from this build environment. |
| `bma_floodbangkok` | shell | MANUAL — no verified machine endpoint; transcribe to `data/manual/bma_floodbangkok/` |
| `rid_water_situation` | shell | MANUAL — bulletin structure not yet inspected; transcribe with explicit `measurement_at` |
| `tmd_bangkok_radar` | shell | UNAVAILABLE — optional page archive only (`archive_page: true`); no rainfall estimation |
| `thaiwater_bangkok` | shell | UNAVAILABLE — no official public API verified |
| `traffy_bangkok` | manual CSV | MANUAL — THIRD_PARTY_REPORTED corroboration only |
| `research_field_observations` | importer | field observations CSV |

## Tests

```bash
pytest                               # 48 tests: timestamps, freshness, dedup, provenance, importers, CKAN, UI
```

## Known limitations

* CKAN ingestion has not been exercised against the live BKK/data.go.th API from the build
  environment (outbound access was blocked there); it is tested against verbatim captured records
  with a mock transport. Run `python scripts/ingest.py --source bkk_open_data_dds` locally first and
  check the Data Quality page.
* The BMA `dds011` Pak Khlong Talat series publishes day/month transposed for day ≤ 12. The repair
  (`nearest_forward`) assumes source order is chronological and is flagged per record; review it
  before using that series quantitatively.
* Units and vertical datums of BKK datasets are **not stated in the resources**; the units in
  `sources.yaml` are declared, flagged `UNIT_DECLARED_UNVERIFIED`, and datums are unknown.
* `station_type` codes in the telemetry registry are undocumented; node types are inferred from the
  station-code prefix and flagged `TYPE_INFERRED`. The dds011 series is not linked to a telemetry
  station (a plausible match, WL.PKG.01, is noted but unverified).
* SEED rows have no recorded original page-view time; their `retrieved_at` is the seed ingest time
  (stated in each row's notes). Edit the SEED CSVs if the true time is known — that creates a new
  snapshot; the old one is kept.
* FloodBangkok, RID, TMD and ThaiWater have no automatic adapters yet.
* Single-user SQLite; no scheduler (run ingests manually or via cron).
* The map base layer needs internet in the browser; choose **white-bg** on the Map page (or set `BDO_MAP_STYLE=white-bg`) to work offline. Markers need WebGL.

## Research roadmap

| Phase | Name | v0.1 status |
|---|---|---|
| 1 | Event & Data Reconstruction | **this milestone** |
| 2 | Drainability Architecture | node model + upstream/downstream text fields only |
| 3 | Reduced-order Hydraulic Model | schema placeholder (`derived_metrics`) |
| 4 | Recovery Dynamics (T_recover, V_residual, R_drain, dh/dt) | placeholders raise `NotValidatedError` |
| 5 | Operational Scenario Analysis | not started |
| 6 | Expert / authority validation | not started; prerequisite for any operational use |

Details in [docs/research_scope.md](docs/research_scope.md).
