# Architecture

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Configuration | `bdo.config` | `settings.yaml`, `sources.yaml`, `.env`; path resolution against repo root |
| Domain model | `bdo.models`, `bdo.enums` | ORM entities, controlled vocabularies, `UTCDateTime` |
| Validation boundary | `bdo.schemas` | Pydantic objects between adapters, runner and importers |
| Ingestion | `bdo.ingestion.base`, `.runner`, `.adapters.*` | adapter contract and pipeline |
| Normalisation | `bdo.normalization.*` | config-driven record → measurement/station mapping |
| Persistence | `bdo.repository.*` | sources, stations, snapshots (file + row), measurements (dedup), observations |
| Analytics | `bdo.analytics.freshness`, `.trends`, `.recovery` | freshness (implemented); trends (descriptive); recovery (refuses) |
| Presentation | `bdo.ui.*`, `app.py` | Streamlit, read-only |
| CLI | `bdo.cli`, `scripts/*.py` | Typer commands |

The UI never writes to the database. Ingestion never depends on the UI.

## Adapter contract

```python
class SourceAdapter(ABC):
    parser_version: str
    def healthcheck(self) -> SourceHealth: ...
    def fetch(self) -> RawFetchResult: ...          # byte-exact payloads, no parsing
    def normalize(self, raw) -> NormalizationResult: ...  # measurements + stations + record errors
```

`fetch()` returns one or more `RawPayload`s (e.g. one per CKAN page). `normalize()` returns
measurements and stations, each tagged with the `payload_index` it came from, so every record
points to the exact snapshot that contained it. Per-record errors are returned, not raised.

## Runner sequence

1. Create `IngestRun(status=RUNNING)` — committed.
2. `healthcheck()` → update `Source.status` (manual runs leave the registry status alone).
   Not fetchable → `SKIPPED`.
3. `fetch()` → `AdapterNotImplemented` → `SKIPPED`; any other error → `FAILED`.
4. Archive every payload (exclusive-create file + `RawSnapshot` row) — **committed**.
   Manual files identical to an archived snapshot are not re-archived or re-parsed.
5. `normalize()` — any exception → `FAILED`, snapshots untouched.
6. Upsert stations; resolve station IDs; flag future timestamps.
7. Dedup against DB and within batch; insert.
8. Finish: `SUCCESS`, or `PARTIAL` with an error file when any record failed.

## Migration path

* **PostgreSQL/PostGIS**: change `BDO_DATABASE_URL`. `UTCDateTime` maps to `TIMESTAMPTZ`; enums
  are VARCHAR+CHECK; dedup is a unique column. Add a `geometry(Point,4326)` column back-filled from
  lat/lon; the domain model does not change. Use Alembic from that point on.
* **FastAPI**: wrap `bdo.repository` functions; the UI's DataFrame builders in `ui/components.py`
  are the only presentation-specific queries.
* **Scheduled workers**: call `bdo.ingestion.runner.run_source` from cron/systemd/APScheduler.
  The runner is stateless apart from the DB and the raw archive.
* **Separate frontend**: consume the API; no business logic lives in Streamlit pages.
