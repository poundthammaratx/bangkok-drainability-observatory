# Database Deployment (v0.3)

## Configuration variables

| Variable | Default | Meaning |
|---|---|---|
| `BDO_DATABASE_URL` | `sqlite:///data/bdo.sqlite` | SQLAlchemy URL. SQLite for local dev/tests; `postgresql+psycopg://user:pass@host/db` for production. **Never committed** — set via `.env` (gitignored) or a deployment secret. |
| `BDO_ARCHIVE_ENABLED` | `false` | Whether a persistent archive is expected to be reachable. Informational for now (`Settings.archive_enabled`); `bdo.database.archive_reachable()` is what UI code actually checks before trusting the archive. |
| `BDO_RUNTIME_ROLE` | unset → `VIEWER` if `PUBLIC_DEPLOYMENT=true`, else `DEVELOPMENT` | `VIEWER` \| `COLLECTOR` \| `DEVELOPMENT` — see below. |
| `PUBLIC_DEPLOYMENT` | `false` | Unchanged from v0.1/v0.2. Still independently sufficient to block all writes, regardless of `BDO_RUNTIME_ROLE`. |
| `BDO_ALLOW_SQLITE_COLLECTOR` | `false` | Explicit development override — see "CLI backend identity" below. |

No database password, token, or connection string is ever committed. `.env.example` documents the
variable names only, with placeholder/default values.

## Runtime roles (milestone §4)

| Role | DB reads | Live HTTP GET | DB/file writes | Intended process |
|---|---|---|---|---|
| `VIEWER` | ✅ | ✅ | ❌ (`PublicDeploymentBlocked`) | Public Streamlit app |
| `COLLECTOR` | ✅ | ✅ | ✅ | Scheduled collector run (cron / GitHub Actions / container) |
| `DEVELOPMENT` | ✅ | ✅ | ✅ | A researcher's own machine |

Enforced by `bdo.util.access.assert_writes_allowed()`, called from every write-capable entry point
(`bdo ingest`, `bdo import-observations`, `bdo collect`, `bdo.repository.observations.import_csv`).
`Settings.is_viewer` is `True` when `public_deployment` is true **or** `runtime_role is VIEWER` —
either one alone is sufficient to block writes; there is no way to "opt back in" from VIEWER by
also setting `PUBLIC_DEPLOYMENT=false` elsewhere, since the two checks are independent `or` terms.

**This is an application-level guard, not a substitute for real credential separation.** A Python
`if` statement cannot stop a database credential that has `DROP TABLE` rights from being used to
drop a table if something *else* in the process is compromised. The actual security boundary is
the credential itself:

### Recommended credential model

Provision **two** Postgres roles:

```sql
-- Read-only, for the public Streamlit (VIEWER) process.
CREATE ROLE bdo_viewer LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE bdo TO bdo_viewer;
GRANT USAGE ON SCHEMA public TO bdo_viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bdo_viewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bdo_viewer;

-- Read + write (INSERT/UPDATE only — no DDL, no DELETE), for the collector.
CREATE ROLE bdo_collector LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE bdo TO bdo_collector;
GRANT USAGE ON SCHEMA public TO bdo_collector;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO bdo_collector;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO bdo_collector;

-- Neither role gets DELETE, TRUNCATE, or any DDL privilege. Schema changes (migrations) run
-- under a third, separately-held admin/owner credential, applied manually or via a deploy step
-- that is not the collector and not the viewer.
```

**The public Streamlit deployment's `BDO_DATABASE_URL` secret must use `bdo_viewer`, never
`bdo_collector` or an owner/admin credential** — even though the application code also enforces
`is_viewer`, defense in depth means the credential itself should make a destructive write
impossible, not just discouraged.

## CLI backend identity (v0.3 production hardening)

**Incident this fixes:** with no `BDO_DATABASE_URL` set, every CLI command silently fell back to
the local SQLite default (`sqlite:///data/bdo.sqlite`). A local dev file holding 803 stations /
1389 measurements was briefly mistaken for the production PostgreSQL historian this way — nothing
in the output said which database a command had actually connected to.

Every operational command (`bdo status`, `bdo collect`, `bdo collector-status`, `bdo watchdog`) now
prints a sanitized backend-identity header before its own output:

```
Backend: PostgreSQL
Archive: ENABLED
Runtime role: COLLECTOR
Database target: remote PostgreSQL
```

or, for a local SQLite file:

```
Backend: SQLite
Archive: LOCAL
Runtime role: DEVELOPMENT
Database target: local file
```

`Archive` reflects `BDO_ARCHIVE_ENABLED` (`ENABLED`) vs not (`LOCAL`) — it does not reflect
reachability; see `bdo.database.archive_reachable()` for that separate, UI-facing check. The header
never prints a password, username, host, or full connection string (`bdo.database.BackendIdentity`
/ `backend_identity()`) — only the backend kind and a coarse locality label.

### Fail-fast: a COLLECTOR must never silently fall back to SQLite

`bdo.database.guard_collector_backend()`, called from `collect_one()` before any fetch happens,
raises `CollectorBackendMisconfigured` when **all** of the following hold:

* `BDO_RUNTIME_ROLE=COLLECTOR`,
* `BDO_ARCHIVE_ENABLED=true`,
* the resolved `database_url` is SQLite, and
* `BDO_ALLOW_SQLITE_COLLECTOR` is not `true`.

That combination is almost always a missing or mistyped `BDO_DATABASE_URL` in a production
collector context (GitHub Actions, a cron host) rather than an intentional local run — the
production PostgreSQL URL was meant to be set. Deliberate local collector testing against SQLite
remains possible by setting `BDO_ALLOW_SQLITE_COLLECTOR=true`.

### Watchdog (`bdo watchdog`)

```bash
bdo watchdog                                        # every live-adapter source, 30-minute threshold
bdo watchdog --source thaiwater_bangkok
bdo watchdog --source thaiwater_bangkok --max-age-minutes 30
```

Reports `HEALTHY` / `STALE` / `NEVER_RUN` / `FAILED` per source from persisted `IngestRun`
(`mode="collector"`) and `SourceHealthHistory` — **data-acquisition health only**, never a
hydraulic/flood inference. Exit code is `0` only when every checked source is `HEALTHY`, so it is
safe to use as a CI/scheduler gate (`docs/COLLECTOR_ARCHITECTURE.md` and
`.github/workflows/collector-thaiwater.yml`). See `bdo.collector.watchdog` for the exact state
machine (a "recent failure with no sufficiently recent success" is reported as `FAILED` even if an
older run once succeeded).

### Registry status vs runtime health

`Source.status` (`config/sources.yaml`, e.g. `ACTIVE`/`UNAVAILABLE`) is **configuration/
administrative** state — it only changes on re-seeding or an explicit admin action, never as a
side effect of a collector run succeeding or failing. `SourceHealthHistory.health` (the
`bdo.live.base.LiveHealth` vocabulary) is the **runtime, observational** state as of the most
recent collector run. These are deliberately never collapsed into one value — `bdo status` and
`bdo collector-status` print both side by side (`bdo.collector.status.registry_vs_runtime_health`),
e.g. a source can show `registry=UNAVAILABLE` (a stale config entry) while
`latest_collector_health=HEALTHY` (the endpoint is actually fine right now), or the reverse.

## Migrations (Alembic)

```bash
pip install alembic                      # dev dependency; see pyproject.toml
alembic upgrade head                     # apply every pending migration
alembic downgrade base                   # drop everything (dev/test only — see limitations below)
alembic current                          # what revision is this database at?
```

The URL comes from `bdo.config.load_settings()` (the same resolution the application itself
uses) — `migrations/env.py` never reads a URL from `alembic.ini`. Set `BDO_DATABASE_URL` the same
way you would for the app, or override just this invocation: `alembic -x db_url=postgresql+psycopg://... upgrade head`.

### Revisions

* **`0001_baseline_schema`** — the full schema as of v0.3 (every table from `bdo/models.py`,
  including the new `source_health_history` and the relaxed `raw_snapshots.payload_path`/new
  `payload_text`). This is the *first* Alembic revision adopted onto a project that previously
  used `Base.metadata.create_all()` directly — there is no "pre-v0.3" migration because nothing
  was migrated before this.

  **Incident note:** the revision as originally autogenerated failed on first real PostgreSQL
  deployment (Supabase, PostgreSQL 17) with `psycopg.errors.DuplicateObject: check constraint
  "source_status" already exists`. Autogenerate had emitted an explicit `CheckConstraint` for
  every `sa.Enum(..., create_constraint=True)` column in addition to the constraint that flag
  already attaches — two same-named constraints in one `CREATE TABLE`. SQLite does not enforce
  unique constraint names per table, so the SQLite round-trip test never caught it; PostgreSQL
  does. Fixed in place (this is an unreleased baseline on a feature branch, not a released
  revision) by removing the duplicate explicit declarations. See
  `tests/test_migrations.py::test_no_duplicate_constraint_or_index_names_on_postgresql`, which
  renders the real PostgreSQL DDL for every revision (offline mode, no live database or driver
  needed) and fails on any repeated constraint/index name — run it after hand-editing or
  regenerating any future autogenerated revision.

* **`0002_source_ts_raw`** (file: `0002_measurement_source_timestamp_raw.py`) — adds
  `measurements.source_timestamp_raw` (nullable `TEXT`), preserving the original source timestamp
  representation (e.g. ThaiWater's naive `"2026-10-01 17:39"`) alongside the normalized,
  timezone-aware `measurement_at`. Purely additive — no existing row or column is altered. Written
  as a standalone revision rather than folded into `0001` because `0001` is already applied to the
  production Supabase database; `alembic upgrade head` on that database applies only this one new
  column.

  **Incident note:** the id was originally `0002_measurement_source_timestamp_raw` (37 characters),
  which doesn't fit the deployed `alembic_version.version_num` column (`VARCHAR(32)`) —
  `psycopg.errors.StringDataRightTruncation` on the first deployment attempt, rolled back by
  PostgreSQL's transactional DDL before any schema change landed. Corrected in place (this revision
  was never successfully applied anywhere) to `0002_source_ts_raw` (18 characters). See
  `tests/test_migrations.py::test_revision_ids_fit_alembic_version_column`.

### Upgrading an existing pre-v0.3 SQLite database

A local `data/bdo.sqlite` created by v0.1/v0.2's `init_db()` (`create_all()`) has the old tables
but is missing `source_health_history` and the new `raw_snapshots` columns — and `create_all()`
never alters existing tables. Running `alembic upgrade head` against it directly will fail (`0001`
tries to `CREATE TABLE sources`, which already exists). Two options:

1. **Recreate it** (simplest, and fine for a local dev/scratch database — rerun
   `python scripts/seed_sources.py`).
2. **Reconcile it by hand** for a database you want to keep: `ALTER TABLE raw_snapshots ADD COLUMN
   payload_text TEXT;`, create `source_health_history` via `Base.metadata.create_all()` (it only
   creates *missing* tables, so this is safe to run even on an existing database), then
   `alembic stamp 0001_baseline_schema` to tell Alembic this database is now at that revision
   without re-running its `CREATE TABLE`s. SQLite cannot drop the old `payload_path NOT NULL`
   constraint in place; a database reconciled this way can still *insert* new file-backed
   snapshots (`payload_path` set) but not the collector's inline ones until fully recreated.

### Downgrade limitations

`0001`'s `downgrade()` drops every table — there is no partial downgrade path, by design: it is
the adoption boundary, not an incremental change. Treat `alembic downgrade base` as "wipe and
start over," appropriate for local development only, never for a database holding real archived
measurements.

### Deployment steps (PostgreSQL/PostGIS)

1. Provision a PostgreSQL instance (PostGIS extension optional for v0.3 — no geometry column is
   used yet; see `docs/PERSISTENT_ARCHIVE.md`'s TODO).
2. Create the `bdo_viewer` / `bdo_collector` roles and an admin/owner role (see above).
3. As the admin/owner role: `alembic upgrade head`.
4. Set the public Streamlit deployment's `BDO_DATABASE_URL` to the `bdo_viewer` connection string,
   `PUBLIC_DEPLOYMENT=true` (as in v0.2), and `BDO_ARCHIVE_ENABLED=true`.
5. Set the collector's `BDO_DATABASE_URL` to the `bdo_collector` connection string and
   `BDO_RUNTIME_ROLE=COLLECTOR` — see docs/COLLECTOR_ARCHITECTURE.md for running it via GitHub
   Actions, cron, or a container scheduler.
6. Verify: `bdo collector-status` (as COLLECTOR) should show every source; the Streamlit app
   (as VIEWER) should show Live Situation's "Archive status: reachable."

### Fallback if PostgreSQL is unreachable

See `docs/PERSISTENT_ARCHIVE.md` §Archive unavailability. The application is designed to boot and
serve a degraded (live-read-through-only) experience, not crash, if `BDO_DATABASE_URL` points at
an unreachable database — verified manually against a deliberately-bad `postgresql+psycopg://`
URL (connection refused immediately) and covered by `tests/test_live_situation.py`.

## Indexing (milestone §19)

Already present (either from v0.1/v0.2 or added in the v0.3 migration):

* `measurements.dedup_key` — unique constraint (the dedup mechanism itself).
* `measurements(station_id, variable, measurement_at)` and `measurements(measurement_at)` —
  support the natural-key window-function query in `current_observations()` and time-range scans
  (Event Archive, Stations, Live Situation trends).
* `raw_snapshots.payload_hash` — supports `find_by_hash` (identical-payload short-circuit).
* `source_health_history(source_id, checked_at)` — supports both "latest per source" (the
  observability panel) and "history for one source over a window" (event derivation).

No PostGIS spatial index exists yet because no spatial *query* exists yet (`stations.latitude`/
`longitude` are plain floats, scanned in Python/pandas for the map). Add a geometry column and a
GiST index only once an actual spatial query (e.g. "stations within N km of a point") is needed —
see `docs/DATA_RETENTION.md` for the broader "don't pre-optimize" stance.
