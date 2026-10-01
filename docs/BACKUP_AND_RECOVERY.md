# Backup & Recovery (v0.3 production hardening)

**Status: no backup is currently configured or running.** This document defines the minimum viable
procedure to put one in place; it does not claim one exists yet. Do not treat the production
PostgreSQL archive (Supabase) as durable against accidental deletion, a bad migration, or a
provider-side incident until the steps below are actually scheduled and the restore procedure has
been verified at least once.

## Why this matters now

Before this milestone, the archive held nothing operationally important — it was dev/test SQLite
or an empty Supabase instance. It now holds real, continuously-collected ThaiWater measurements,
source-health history, and raw-snapshot provenance that cannot be re-fetched after the fact (the
source does not serve historical data — only current readings). Losing the database loses that
history permanently, not just an ingestion convenience.

## Minimum viable procedure

### 1. Regular logical export

Supabase (managed PostgreSQL) supports `pg_dump` against the connection string directly; this does
not require special Supabase-side configuration:

```bash
pg_dump --format=custom --file="bdo_$(date +%Y%m%d_%H%M).dump" "$BDO_DATABASE_URL_ADMIN"
```

Use the **admin/owner** credential for the dump (read access to everything, including sequences/
constraints), never the `bdo_viewer` or `bdo_collector` role. Recommended cadence: **daily**, given
the current data volume (one source, ~25 measurements per run, every 10 minutes) — a daily dump
bounds worst-case loss to under 24 hours of collection, which is acceptable while only one source
is live. Revisit the cadence once more sources are scheduled (docs/COLLECTOR_ARCHITECTURE.md).

A scheduled GitHub Actions job (parallel to, not inside, `collector-thaiwater.yml`) or Supabase's
own built-in daily backup feature (check current plan tier — not assumed enabled here) are both
reasonable ways to run this; neither is wired up yet.

### 2. Off-database copy

The dump file must not live only inside the same database/project it was taken from. At minimum:
upload each dump to a separate storage location (e.g. a private GitHub Actions artifact, a cloud
storage bucket, or the operator's own machine) — anywhere that survives the Supabase project itself
being deleted or becoming unreachable. Do not commit a dump file to the git repository: it contains
real collected data and (depending on export method) potentially connection metadata.

### 3. Retention recommendation

* Keep **daily** dumps for the most recent **14 days**.
* Keep **one dump per week** for the most recent **90 days**.
* Beyond 90 days, retain at the operator's discretion based on actual storage cost and research
  value — no archival/cold-storage tier is set up yet.

This is a starting point, not a compliance requirement; revisit once data volume or source count
grows (see docs/DATA_RETENTION.md for the related but distinct question of how long *within* the
live database old measurements are kept — currently: indefinitely, nothing is deleted).

### 4. Restore verification procedure

A backup that has never been restored is unverified. At minimum, periodically (recommended: after
the first real dump, then quarterly):

```bash
createdb bdo_restore_test
pg_restore --dbname=bdo_restore_test "bdo_YYYYMMDD_HHMM.dump"
```

Then, against that restored database:

```bash
BDO_DATABASE_URL="postgresql+psycopg://.../bdo_restore_test" alembic current
BDO_DATABASE_URL="postgresql+psycopg://.../bdo_restore_test" bdo status
```

Confirm `alembic current` shows the expected head revision and `bdo status` reports a non-zero
`measurements` count matching (approximately) what was expected at dump time. Drop
`bdo_restore_test` afterward. **Do not perform this against the production database** — always a
separate, disposable one.

## What is explicitly out of scope here

* Point-in-time recovery (PITR) / continuous WAL archiving — not configured; evaluate only if
  Supabase's plan tier includes it and the operational cost is justified by data volume.
* Automated backup monitoring/alerting (e.g. "page someone if the daily dump didn't run") — not
  built; the `bdo watchdog` mechanism covers *collection* health, not *backup* health, and the two
  should not be conflated.
* Cross-region replication — not needed at current scale.

## Explicit non-claim

No part of this document should be read as "backups are active." They are **documented, not yet
scheduled**. Treat the production archive as not-yet-backed-up until a scheduled export job exists
and step 4 has been run at least once against a real dump.
