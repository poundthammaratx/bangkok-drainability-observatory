"""Immutable raw-payload archive.

Layout: ``<data_dir>/raw/<source_key>/<YYYY>/<MM>/<DD>/<UTCSTAMP>_<sha12>_<label>.<ext>``
(date folders are the UTC retrieval date, matching the UTC stamp in the filename).

Files are opened with mode ``xb``: an existing file is never overwritten. ``payload_path`` is
stored relative to the data directory so the archive can be relocated.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from bdo.config import Settings
from bdo.models import RawSnapshot, Source
from bdo.schemas import RawPayload
from bdo.util.hashing import sha256_bytes
from bdo.util.time import compact_utc_stamp, ensure_utc

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class SnapshotExistsError(FileExistsError):
    pass


def _safe_label(label: str) -> str:
    s = _SAFE.sub("-", label).strip("-.")
    return (s or "payload")[:80]


def build_relative_path(source_key: str, retrieved_at: datetime, payload_hash: str, label: str, ext: str) -> Path:
    t = ensure_utc(retrieved_at, "UTC")
    ext = _SAFE.sub("", ext.lstrip(".")) or "bin"
    name = f"{compact_utc_stamp(t)}_{payload_hash[:12]}_{_safe_label(label)}.{ext}"
    return Path("raw") / source_key / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}" / name


def save_snapshot(
    session: Session,
    settings: Settings,
    source: Source,
    payload: RawPayload,
    retrieved_at: datetime,
    ingest_run_id: int | None = None,
    parser_version: str | None = None,
) -> RawSnapshot:
    """Write payload bytes to the archive, then record metadata. Commit is the caller's decision,
    but the file is on disk before this returns."""
    digest = sha256_bytes(payload.content)
    rel = build_relative_path(source.source_key, retrieved_at, digest, payload.label, payload.extension)
    abs_path = settings.data_dir / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(abs_path, "xb") as fh:
            fh.write(payload.content)
    except FileExistsError as exc:  # identical stamp+hash+label: refuse rather than overwrite
        raise SnapshotExistsError(str(abs_path)) from exc

    snap = RawSnapshot(
        source_id=source.id,
        retrieved_at=ensure_utc(retrieved_at, "UTC"),
        source_measurement_at=payload.source_measurement_at,
        content_type=payload.content_type,
        payload_path=rel.as_posix(),
        payload_hash=digest,
        payload_bytes=len(payload.content),
        http_status=payload.http_status,
        request_url=payload.request_url,
        parser_version=parser_version,
        ingest_run_id=ingest_run_id,
        label=payload.label,
        notes=payload.notes,
    )
    session.add(snap)
    session.flush()
    return snap


def find_by_hash(session: Session, source_id: int, payload_hash: str) -> RawSnapshot | None:
    return session.scalar(
        select(RawSnapshot)
        .where(RawSnapshot.source_id == source_id, RawSnapshot.payload_hash == payload_hash)
        .order_by(RawSnapshot.id)
        .limit(1)
    )


def absolute_path(settings: Settings, snap: RawSnapshot) -> Path:
    return settings.data_dir / snap.payload_path


def read_payload(settings: Settings, snap: RawSnapshot) -> bytes:
    return absolute_path(settings, snap).read_bytes()


def verify(settings: Settings, snap: RawSnapshot) -> bool:
    p = absolute_path(settings, snap)
    return p.exists() and sha256_bytes(p.read_bytes()) == snap.payload_hash
