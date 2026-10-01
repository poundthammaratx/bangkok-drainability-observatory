# Source Cadence (v0.3)

Cadence is **configuration-driven**, not hard-coded in the collector: the recommended intervals
below are implemented as `config/settings.yaml`'s `live.ttl_seconds` (read by
`Settings.live_ttl_for()`), which also governs how often the Streamlit read-through layer re-polls
each source. A scheduler (`docs/COLLECTOR_ARCHITECTURE.md`) should run `bdo collect` no faster
than the matching TTL — running faster just re-fetches within the cache window and wastes a
request; running the collector itself on a longer interval than the TTL is always safe.

| Source | Recommended cadence | Current `live.ttl_seconds` | Rationale |
|---|---|---|---|
| `bma_floodbangkok` | ~5 min | 180s | Sensors report every 5–10 minutes (observed `interval` field); matches milestone target. |
| `thaiwater_bangkok` | 5–10 min | 300s | Rain is hourly-aggregated; water level observed ~15–30 min old — 5 min polling is conservative, not wasteful, since dedup means an unchanged reading costs nothing to persist. |
| `tmd_bangkok_radar` | ~5 min, **only because no timestamp exists to misinterpret** | 300s | No machine-readable product timestamp was found (see docs/SOURCE_ENDPOINTS.md) — polling faster would not make the *data* any more interpretable, only increase request volume. Health is never HEALTHY regardless of cadence. |
| `rid_water_situation` | 30–60 min, or the source's own (daily bulletin) cadence | 1800s (30 min) | No structured endpoint is parsed in v0.3 (page-reachability only) — see docs/SOURCE_ENDPOINTS.md §5. Never poll faster than a daily bulletin actually updates. |
| `bkk_open_data_dds` | **Do not poll as if live** | 300s | The "5-minute" water-level dataset's newest record was found dated 2023-09-30 at inspection — over two years stale despite the CKAN API answering HTTP 200. `bkk_live` polls defensively (to catch the day the dataset *does* resume) but reports `STALE`/`VERY_STALE` honestly rather than ever assuming freshness from cadence. |
| Static reference topology (`data/reference/*.csv`) | Daily, or an explicit manual refresh | N/A — not polled automatically | Regenerate with `python scripts/export_reference_registry.py` when you want a fresher snapshot. Not run automatically at any cadence in v0.3; the packaged CSVs are the snapshot the deployment ships with. |

## Access-control honesty (milestone §11)

No circumvention technique (header spoofing beyond an honest `User-Agent`, proxy rotation, CAPTCHA
bypass, etc.) is used or planned. If a source's endpoint starts rejecting collection from a given
IP range (e.g. a cloud provider's egress), the correct response is to record `UNAVAILABLE`/
`DEGRADED` (which the existing health model already does automatically on any HTTP error) and
document the limitation here — not to work around the restriction. No such rejection has been
observed during v0.3 development; all five sources were reachable from the development
environment's network at every inspection.

## Changing the cadence

Edit `config/settings.yaml`:

```yaml
live:
  default_ttl_seconds: 300
  ttl_seconds:
    bma_floodbangkok: 180
    bkk_open_data_dds: 300
    tmd_bangkok_radar: 300
    thaiwater_bangkok: 300
    rid_water_situation: 1800
```

An unknown source key falls back to `default_ttl_seconds` — see `Settings.live_ttl_for()`'s
docstring. `BDO_RUNTIME_ROLE`/`BDO_DATABASE_URL` are environment variables (secrets-safe); cadence
is ordinary, non-secret configuration and lives in the committed YAML file instead.
