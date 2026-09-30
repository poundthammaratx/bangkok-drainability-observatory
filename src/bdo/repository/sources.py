"""Source registry persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.config import SourceConfig
from bdo.enums import SourceStatus
from bdo.models import Source
from bdo.schemas import SourceHealth


def get_by_key(session: Session, source_key: str) -> Source | None:
    return session.scalar(select(Source).where(Source.source_key == source_key))


def require(session: Session, source_key: str) -> Source:
    src = get_by_key(session, source_key)
    if src is None:
        raise KeyError(f"source '{source_key}' not registered — run scripts/seed_sources.py")
    return src


def upsert_from_config(session: Session, cfg: SourceConfig) -> Source:
    """Insert or update registry metadata. An existing runtime status is preserved unless it is UNKNOWN."""
    src = get_by_key(session, cfg.source_key)
    if src is None:
        src = Source(source_key=cfg.source_key, status=cfg.status)
        session.add(src)
    src.agency = cfg.agency
    src.name = cfg.name
    src.base_url = cfg.base_url
    src.data_domain = cfg.data_domain
    src.access_mode = cfg.access_mode
    src.typical_freshness_minutes = cfg.typical_freshness_minutes
    src.priority = cfg.priority
    src.default_evidence_class = cfg.evidence_class
    src.adapter = cfg.adapter
    src.notes = (cfg.notes or "").strip() or None
    if src.status in (None, SourceStatus.UNKNOWN):
        src.status = cfg.status
    session.flush()
    return src


def record_health(session: Session, src: Source, health: SourceHealth) -> None:
    src.status = health.status
    src.last_healthcheck_at = health.checked_at
    src.last_health_message = health.message[:2000]
    session.flush()


def list_all(session: Session) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.id)))
