# Collector Architecture (v0.3)

## Why a separate package

v0.1/v0.2's write path (`bdo.ingestion.runner.run_source`) was always meant to be run by a
researcher's CLI, by hand, against a local SQLite file. v0.3 needs the *same kind* of operation —
fetch, archive, normalize, dedupe, persist — runnable unattended, on a schedule, independent of
whether Streamlit is even running. `src/bdo/collector/` is that layer:

```
src/bdo/collector/
    runner.py              collect_one() / collect_all() — the 8-step sequence
    persistence.py         LiveSourceState -> persisted rows (reuses existing repo writes)
    scheduler_contract.py  the stable surface an external scheduler calls
    status.py              read-only queries over collector runs / health history
```

**Nothing here reimplements source parsing.** Every collector run is: call the existing
`bdo.live.adapters.*` (the same functions the Streamlit read-through layer calls), then hand the
result to the existing dedup-aware repository writers. The only new code is the glue between them.

## The 8-step sequence

`bdo.collector.runner.collect_one(settings, source_key)`:

1. **Create collector run** — `IngestRun(mode="collector")`. Not a new `collector_runs` table: an
   `IngestRun` already records exactly this shape of information (started/finished, counts,
   status, parser version) — see its docstring in `bdo/models.py` for why a second table would
   have duplicated an existing concept.
2. **Fetch source** — `bdo.live.manager.get_live_state(settings, source_key, force=True)`, bypassing
   the read-through TTL cache (a collector run should always fetch fresh).
3. **Preserve raw snapshot** — every `RawPayloadCapture` the adapter populated (see
   `bdo.live.base.LiveSourceState.raw_payloads`) is archived via
   `bdo.repository.snapshots.save_snapshot_inline()` — inline in the database, not
   `data/raw/<file>`, because the collector may run on a different machine from the Streamlit
   viewer (e.g. GitHub Actions writing to a remote Postgres that Streamlit Cloud reads) and cannot
   assume a shared filesystem.
4. **Normalize observations** — `LiveMeasurement` → `bdo.schemas.NormalizedMeasurement`, the same
   validated object the CLI ingestion pipeline produces.
5–6. **Deduplicate / persist measurements** — `bdo.repository.measurements.insert_measurements()`,
   unchanged from v0.1. Also upserts station topology (`bdo.repository.stations.upsert`) from
   whatever identity the live reading itself carries — for FloodBangkok and ThaiWater this is the
   *only* place those networks' stations get a persisted row at all; they are not part of the
   packaged BKK CKAN reference registry.
7. **Persist source health** — `bdo.repository.source_health.record()`, every run, success or
   failure — see `docs/PERSISTENT_ARCHIVE.md` and `docs/SOURCE_CADENCE.md`.
8. **Close run with metrics** — `IngestRun.status`/`finished_at`/counts updated and committed.

A source-side failure (network error, bad JSON, stale data) never raises out of `collect_one()` —
it becomes a `FAILED`/`PARTIAL` run and an `UNAVAILABLE` health row, which *is* the useful signal
(`tests/test_collector.py::test_source_failure_creates_run_and_health_but_no_measurement`).

## Security: the VIEWER / COLLECTOR / DEVELOPMENT roles

`collect_one()`'s first line is `assert_writes_allowed(settings, "collection")`
(`bdo.util.access`). It raises `PublicDeploymentBlocked` when:

* `Settings.public_deployment` is true (the v0.1/v0.2 flag, unchanged), **or**
* `Settings.runtime_role is RuntimeRole.VIEWER`.

See `docs/DATABASE_DEPLOYMENT.md` for the full role model and the recommended credential
separation. In short: **the public Streamlit process should run as VIEWER and never hold a
collector-capable database credential at all** — this code-level guard is defense in depth, not
the only line of defense.

## CLI usage

```bash
bdo collect thaiwater_bangkok        # one source
bdo collect --all                    # every source with a live adapter
bdo collector-status                 # latest health per source + recent collector runs
bdo watchdog --source thaiwater_bangkok --max-age-minutes 30   # data-acquisition health gate
```

Exit code is non-zero if any collected source ended `FAILED`. Every one of these commands prints a
sanitized backend-identity header first (`Backend: ... / Archive: ... / Runtime role: ... /
Database target: ...`) — see docs/DATABASE_DEPLOYMENT.md's "CLI backend identity" section for why,
and for the fail-fast guard that stops a misconfigured COLLECTOR from silently writing to SQLite.

## Watchdog (v0.3 production hardening)

`bdo watchdog` (`bdo.collector.watchdog.check_source`) answers one question per source: *is
collection itself still happening on schedule?* It is deliberately narrow — **data-acquisition
health only**, never a hydraulic/flood inference from a stale or missing run:

| Status | Meaning | Exit |
|---|---|---|
| `HEALTHY` | Latest successful collector run is within `--max-age-minutes` | `0` |
| `STALE` | A successful run exists, but it's older than the threshold | non-zero |
| `NEVER_RUN` | No successful collector run exists for this source at all | non-zero |
| `FAILED` | The most recent attempt failed and no sufficiently recent success exists | non-zero |

Based strictly on persisted `IngestRun(mode="collector")` and `SourceHealthHistory` rows — nothing
here touches `LiveHealth` or measurement values directly (though the latest observed `LiveHealth`
and latest measurement timestamp are included in the report for context). Safe to use as the last
step of a scheduled job: a non-zero exit fails the job without suppressing anything
(`.github/workflows/collector-thaiwater.yml`).

## Registry status vs runtime health

`Source.status` (administrative, from `config/sources.yaml`) and `SourceHealthHistory.health`
(observational, from the most recent collector run) are deliberately kept as two separate
vocabularies that are never collapsed into one value — see docs/DATABASE_DEPLOYMENT.md for the
full explanation and why `bdo status` / `bdo collector-status` print both side by side.

## Scheduling (milestone §12)

Collectors are scheduler-agnostic by construction — nothing in `bdo.collector` knows it's running
under cron, a GitHub Actions runner, or a container orchestrator. `scheduler_contract.py` is the
one stable entry point external tooling should call, kept separate from the Typer CLI so its
argument parsing can evolve independently:

```bash
python -m bdo.collector.scheduler_contract --source thaiwater_bangkok
python -m bdo.collector.scheduler_contract --all
```

It is idempotent and safe to retry (see its docstring) — a scheduler-side timeout or retry never
risks a duplicate measurement.

### Production: ThaiWater only (`.github/workflows/collector-thaiwater.yml`)

This is a **real, active** workflow file (no `.example` suffix) — the v0.3 production-hardening
milestone's first deliberate activation, scoped to ThaiWater only (FloodBangkok, BKK DDS, TMD, RID,
and Traffy are intentionally not scheduled yet; see docs/BACKUP_AND_RECOVERY.md and
docs/PUBLIC_WAR_ROOM.md for the current production-readiness boundaries). It runs
`bdo collect thaiwater_bangkok` then `bdo watchdog --source thaiwater_bangkok --max-age-minutes 30`,
failing the job if either step fails, with `workflow_dispatch` for manual runs and a
`schedule: */10 * * * *` cron for the requested (not guaranteed-real-time) cadence.

**It does nothing until `BDO_DATABASE_URL` is added as a repository secret** — the workflow file
existing in the repo is inert without it, exactly like the generic template below. See the comments
at the top of the file for the exact activation order: add the secret, merge to the default branch
(GitHub Actions `schedule` only fires from the default branch), then run `workflow_dispatch`
manually at least once against the real production host before trusting the cron.

### Example / future sources: GitHub Actions

`.github/workflows/collector.yml.example` shows the intended shape for collecting *every* source
once more of them are production-ready. The `.example` suffix is
deliberate: GitHub Actions only discovers files ending exactly in `.yml`/`.yaml` directly inside
`.github/workflows/`, so this file is inert — present for discoverability, incapable of running:

```yaml
name: BDO Collector
on:
  workflow_dispatch: {}          # manual trigger only, until secrets are configured
  # schedule:
  #   - cron: "*/5 * * * *"      # uncomment once BDO_DATABASE_URL is a real secret
jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - env:
          BDO_DATABASE_URL: ${{ secrets.BDO_DATABASE_URL }}
          BDO_RUNTIME_ROLE: COLLECTOR
        run: python -m bdo.collector.scheduler_contract --all
```

Per milestone §12 ("do NOT activate a production scheduled workflow that requires secrets until
the deployment configuration is explicitly provided"), activating it is a deliberate, separate
step: rename it to `collector.yml` (dropping `.example`), add `BDO_DATABASE_URL` as a repository
secret, and uncomment the `schedule:` block.

### Other scheduler options

* **Managed cron service** (e.g. a provider's scheduled job) — same `scheduler_contract` command,
  with `BDO_DATABASE_URL` / `BDO_RUNTIME_ROLE=COLLECTOR` as environment variables.
* **Server crontab**: `*/5 * * * * cd /path/to/repo && BDO_RUNTIME_ROLE=COLLECTOR python -m bdo.collector.scheduler_contract --all >> collector.log 2>&1`
* **Container scheduler** (e.g. a Kubernetes CronJob): run the same command as the container's
  entrypoint.

No scheduler is active today. See docs/SOURCE_CADENCE.md for recommended intervals per source.
