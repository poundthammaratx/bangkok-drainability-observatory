"""Collector execution — independent of Streamlit. See docs/COLLECTOR_ARCHITECTURE.md.

A collector invocation is the 8-step sequence the milestone specifies:

1. create collector run    (``IngestRun(mode="collector")`` — see ``bdo.models.IngestRun``'s
                             docstring on why this is not a separate "collector_runs" table)
2. fetch source            (``bdo.live.manager.get_live_state``, forced past its TTL cache)
3. preserve raw snapshot   (``bdo.collector.persistence``)
4. normalize observations  (``LiveMeasurement`` -> ``NormalizedMeasurement``, in ``persistence.py``)
5. deduplicate/revision    (the existing fingerprint-based ``insert_measurements``)
6. persist measurements    (same call)
7. persist source-health   (``bdo.repository.source_health``)
8. close run with metrics  (this module)

Blocked outright under the VIEWER role (``PUBLIC_DEPLOYMENT=true`` or
``BDO_RUNTIME_ROLE=VIEWER``) via the same ``assert_writes_allowed`` guard as ``bdo ingest`` — a
collector accidentally pointed at the public deployment's database cannot write to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from bdo.collector.persistence import persist_state
from bdo.config import Settings
from bdo.database import guard_collector_backend, session_factory
from bdo.enums import IngestStatus
from bdo.live import manager as live_manager
from bdo.live.base import LiveHealth
from bdo.models import IngestRun
from bdo.repository import sources as source_repo
from bdo.util.access import assert_writes_allowed
from bdo.util.time import utcnow

_HEALTH_TO_STATUS = {
    LiveHealth.HEALTHY: IngestStatus.SUCCESS,
    LiveHealth.STALE: IngestStatus.SUCCESS,      # fetched and persisted fine; the *data* is stale,
    LiveHealth.DEGRADED: IngestStatus.PARTIAL,   # not the run
    LiveHealth.UNKNOWN: IngestStatus.PARTIAL,
    LiveHealth.UNAVAILABLE: IngestStatus.FAILED,
}


@dataclass
class CollectorResult:
    source_key: str
    run_id: int | None
    status: IngestStatus
    health: LiveHealth
    records_fetched: int = 0
    records_inserted: int = 0
    records_skipped: int = 0
    stations_upserted: int = 0
    snapshots_saved: int = 0
    message: str | None = None

    def line(self) -> str:
        return (f"[{self.source_key}] run={self.run_id} status={self.status.value} "
                f"health={self.health.value} fetched={self.records_fetched} "
                f"inserted={self.records_inserted} skipped_dup={self.records_skipped} "
                f"stations={self.stations_upserted} snapshots={self.snapshots_saved}"
                + (f" — {self.message}" if self.message else ""))


def collect_one(settings: Settings, source_key: str, sessions: sessionmaker | None = None) -> CollectorResult:
    """Run the full collect-and-persist sequence for one source. Never raises on a source-side
    failure (network error, bad JSON, ...) — that is recorded as a FAILED run and an UNAVAILABLE
    health row, which is itself the useful signal. It *does* raise ``PublicDeploymentBlocked`` if
    called under the VIEWER role, ``CollectorBackendMisconfigured`` if a COLLECTOR run with the
    archive flag on would silently write to SQLite (see ``bdo.database.guard_collector_backend``),
    and lets a genuine programming/database error propagate."""
    assert_writes_allowed(settings, "collection")
    guard_collector_backend(settings)
    sessions = sessions or session_factory(settings)
    session = sessions()
    t0 = utcnow()
    try:
        src = source_repo.require(session, source_key)
        run = IngestRun(source_id=src.id, mode="collector", status=IngestStatus.RUNNING, started_at=t0)
        session.add(run)
        session.commit()

        state = live_manager.get_live_state(settings, source_key, force=True)
        latency_ms = (utcnow() - t0).total_seconds() * 1000

        stats = persist_state(session, src, state, collector_run_id=run.id, latency_ms=latency_ms)

        status = _HEALTH_TO_STATUS[state.health]
        run.status = status
        run.finished_at = utcnow()
        run.records_retrieved = stats.records_retrieved
        run.records_inserted = stats.records_inserted
        run.records_skipped = stats.records_skipped
        run.stations_upserted = stats.stations_upserted
        run.snapshots_saved = stats.snapshots_saved
        run.error_message = state.error
        session.commit()

        return CollectorResult(
            source_key=source_key, run_id=run.id, status=status, health=state.health,
            records_fetched=stats.records_retrieved, records_inserted=stats.records_inserted,
            records_skipped=stats.records_skipped, stations_upserted=stats.stations_upserted,
            snapshots_saved=stats.snapshots_saved, message=state.error,
        )
    finally:
        session.close()


def collect_all(settings: Settings, sessions: sessionmaker | None = None) -> list[CollectorResult]:
    """Collect every source that has a live adapter (``bdo.live.manager.LIVE_SOURCE_KEYS``)."""
    return [collect_one(settings, key, sessions=sessions) for key in live_manager.LIVE_SOURCE_KEYS]
