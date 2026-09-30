"""Ingestion runner.

    start IngestRun -> healthcheck -> fetch -> save RawSnapshot(s) -> normalize -> validate
    -> deduplicate -> insert Measurements -> finish IngestRun

Invariants:
* Snapshots are written to disk and committed BEFORE normalisation starts, so a parser crash
  leaves them intact (tested in tests/test_provenance.py).
* Per-record failures do not abort the run; they are counted, summarised on the IngestRun and
  written in full to data/processed/ingest_errors/run_<id>.txt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from bdo.config import Settings
from bdo.database import session_factory
from bdo.enums import IngestStatus, QualityFlag, SourceStatus
from bdo.ingestion.adapters import ADAPTERS, ManualCSVAdapter
from bdo.ingestion.base import AdapterNotImplemented, SourceAdapter
from bdo.models import IngestRun, Source
from bdo.repository import measurements as meas_repo
from bdo.repository import snapshots as snap_repo
from bdo.repository import sources as source_repo
from bdo.repository import stations as station_repo
from bdo.util.hashing import sha256_bytes
from bdo.util.logging import get_logger
from bdo.util.time import utcnow

FUTURE_TOLERANCE = timedelta(minutes=10)


@dataclass
class RunSummary:
    source_key: str
    run_id: int
    status: IngestStatus
    snapshots_saved: int = 0
    records_retrieved: int = 0
    records_inserted: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    stations_upserted: int = 0
    message: str | None = None
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"[{self.source_key}] run {self.run_id}: {self.status.value} — snapshots={self.snapshots_saved} "
                f"retrieved={self.records_retrieved} inserted={self.records_inserted} "
                f"skipped_dup={self.records_skipped} failed={self.records_failed} "
                f"stations={self.stations_upserted}" + (f" — {self.message}" if self.message else ""))


def build_adapter(settings: Settings, source_key: str, manual: bool = False, paths=None) -> SourceAdapter:
    cfg = settings.source(source_key)
    if manual or cfg.adapter == "manual_csv":
        return ManualCSVAdapter(cfg, settings, paths=paths)
    try:
        cls = ADAPTERS[cfg.adapter]
    except KeyError as exc:
        raise KeyError(f"unknown adapter '{cfg.adapter}' for source '{source_key}'") from exc
    return cls(cfg, settings)


def _finish(session: Session, run: IngestRun, summary: RunSummary, status: IngestStatus, message: str | None):
    run.status = status
    run.finished_at = utcnow()
    run.error_message = message[:4000] if message else None
    summary.status, summary.message = status, message
    session.commit()


def run_source(
    settings: Settings,
    source_key: str,
    *,
    manual: bool = False,
    paths=None,
    adapter: SourceAdapter | None = None,
    sessions: sessionmaker | None = None,
    mode: str | None = None,
) -> RunSummary:
    sessions = sessions or session_factory(settings)
    adapter = adapter or build_adapter(settings, source_key, manual=manual, paths=paths)
    is_manual = isinstance(adapter, ManualCSVAdapter)
    mode = mode or ("manual" if is_manual else "automatic")

    session = sessions()
    try:
        src: Source = source_repo.require(session, source_key)
        run = IngestRun(source_id=src.id, status=IngestStatus.RUNNING, mode=mode,
                        parser_version=adapter.parser_version, started_at=utcnow())
        session.add(run)
        session.commit()
        log = get_logger("bdo.ingest", source=source_key, run_id=run.id)
        summary = RunSummary(source_key=source_key, run_id=run.id, status=IngestStatus.RUNNING)
        log.info("started (mode=%s, parser=%s)", mode, adapter.parser_version, stage="start")

        # -- healthcheck ----------------------------------------------------------------------
        try:
            health = adapter.healthcheck()
        except Exception as exc:  # healthcheck must never crash the runner
            from bdo.schemas import SourceHealth
            health = SourceHealth(status=SourceStatus.UNAVAILABLE, message=f"healthcheck error: {exc!s}"[:500])
        if is_manual:
            src.last_healthcheck_at, src.last_health_message = health.checked_at, f"[manual] {health.message}"[:2000]
            session.flush()
        else:
            source_repo.record_health(session, src, health)
        session.commit()
        log.info("%s — %s", health.status.value, health.message, stage="healthcheck")
        if not health.fetchable:
            _finish(session, run, summary, IngestStatus.SKIPPED, f"{health.status.value}: {health.message}")
            log.info("skipped", stage="finish")
            return summary

        # -- fetch ----------------------------------------------------------------------------
        try:
            raw = adapter.fetch()
        except AdapterNotImplemented as exc:
            _finish(session, run, summary, IngestStatus.SKIPPED, str(exc))
            log.info("adapter not implemented: %s", exc, stage="fetch")
            return summary
        except Exception as exc:
            _finish(session, run, summary, IngestStatus.FAILED, f"fetch failed: {exc!s}")
            log.error("fetch failed: %s", exc, stage="fetch")
            return summary
        fetch_errors = raw.context.get("fetch_errors") or {}
        log.info("%d payload(s), %d fetch error(s)", len(raw.payloads), len(fetch_errors), stage="fetch")

        # -- archive raw ----------------------------------------------------------------------
        snapshot_ids: list[int | None] = []
        skip_payload: set[int] = set()
        archive_once = bool(raw.context.get("archive_once"))
        for i, payload in enumerate(raw.payloads):
            if archive_once:
                prior = snap_repo.find_by_hash(session, src.id, sha256_bytes(payload.content))
                if prior is not None:
                    snapshot_ids.append(prior.id)
                    skip_payload.add(i)
                    log.info("payload '%s' identical to snapshot %d; not re-archived", payload.label, prior.id,
                             stage="snapshot")
                    continue
            try:
                snap = snap_repo.save_snapshot(session, settings, src, payload, raw.retrieved_at,
                                               ingest_run_id=run.id, parser_version=adapter.parser_version)
            except Exception as exc:
                run.snapshots_saved = summary.snapshots_saved
                session.commit()  # keep what was archived
                _finish(session, run, summary, IngestStatus.FAILED, f"snapshot archive failed: {exc!s}")
                log.error("snapshot archive failed: %s", exc, stage="snapshot")
                return summary
            snapshot_ids.append(snap.id)
            summary.snapshots_saved += 1
        run.snapshots_saved = summary.snapshots_saved
        session.commit()  # raw is now durable, independent of what follows
        log.info("%d snapshot(s) saved, %d unchanged", summary.snapshots_saved, len(skip_payload), stage="snapshot")

        if raw.payloads and len(skip_payload) == len(raw.payloads):
            _finish(session, run, summary, IngestStatus.SUCCESS, "no new payloads (all identical to archived snapshots)")
            return summary

        # -- normalize ------------------------------------------------------------------------
        try:
            norm = adapter.normalize(raw)
        except Exception as exc:
            _finish(session, run, summary, IngestStatus.FAILED,
                    f"normalization failed ({type(exc).__name__}: {exc!s}); raw snapshots preserved")
            log.error("normalization failed: %s", exc, stage="normalize")
            return summary
        log.info("%d measurement(s), %d station(s), %d record error(s)",
                 len(norm.measurements), len(norm.stations), len(norm.record_errors), stage="normalize")

        # -- stations -------------------------------------------------------------------------
        station_ids: dict[str, int] = {}
        for ns in norm.stations:
            if ns.payload_index in skip_payload:
                continue
            snap_id = snapshot_ids[ns.payload_index] if ns.payload_index < len(snapshot_ids) else None
            st = station_repo.upsert(session, src.id, ns, raw_snapshot_id=snap_id)
            station_ids[ns.external_station_id] = st.id
        summary.stations_upserted = len(station_ids)

        # -- validate + dedup + insert --------------------------------------------------------
        items = []
        for m in norm.measurements:
            if m.payload_index in skip_payload:
                continue
            retrieved = m.retrieved_at or raw.retrieved_at
            if m.measurement_at is not None and m.measurement_at > retrieved + FUTURE_TOLERANCE:
                m.quality_flags.add(QualityFlag.FUTURE_TIMESTAMP.value)
            sid = None
            if m.station_external_id:
                sid = station_ids.get(m.station_external_id)
                if sid is None:
                    sid = station_repo.ensure_minimal(session, src.id, m.station_external_id).id
                    station_ids[m.station_external_id] = sid
            snap_id = snapshot_ids[m.payload_index] if m.payload_index < len(snapshot_ids) else None
            items.append((m, sid, snap_id))

        stats = meas_repo.insert_measurements(session, src.id, items, raw.retrieved_at, run.id, adapter.parser_version)
        summary.records_retrieved = len(items)
        summary.records_inserted = stats.inserted
        summary.records_skipped = stats.skipped_duplicate
        summary.records_failed = len(norm.record_errors)
        run.records_retrieved = summary.records_retrieved
        run.records_inserted = summary.records_inserted
        run.records_skipped = summary.records_skipped
        run.records_failed = summary.records_failed
        run.stations_upserted = summary.stations_upserted
        session.commit()
        log.info("inserted=%d skipped_dup=%d failed=%d", stats.inserted, stats.skipped_duplicate,
                 len(norm.record_errors), stage="insert")

        errors = list(norm.record_errors) + [f"fetch {k}: {v}" for k, v in fetch_errors.items()]
        summary.errors = errors
        if errors:
            _write_error_file(settings, run.id, errors)
            msg = f"{len(errors)} issue(s); first: {errors[0]}"
            _finish(session, run, summary, IngestStatus.PARTIAL, msg)
        else:
            _finish(session, run, summary, IngestStatus.SUCCESS, None)
        log.info("finished %s", summary.status.value, stage="finish")
        return summary
    finally:
        session.close()
        adapter.close()


def _write_error_file(settings: Settings, run_id: int, errors: list[str]) -> None:
    d = settings.processed_dir / "ingest_errors"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"run_{run_id}.txt").write_text("\n".join(errors), encoding="utf-8")


def run_all(settings: Settings, sessions: sessionmaker | None = None) -> list[RunSummary]:
    """Run every source whose automatic adapter is ``enabled`` in sources.yaml."""
    out = []
    for cfg in settings.sources:
        if cfg.enabled:
            out.append(run_source(settings, cfg.source_key, sessions=sessions))
    return out
