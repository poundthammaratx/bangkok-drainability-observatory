"""Time handling.

Rules:
* Internally every timestamp is timezone-aware UTC.
* A naive timestamp is never guessed silently: callers must pass the zone the source declares.
* Presentation is Asia/Bangkok (ICT, UTC+07:00) unless configured otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
BANGKOK = ZoneInfo("Asia/Bangkok")
_ZONE_ABBR = {"Asia/Bangkok": "ICT", "UTC": "UTC"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime | None, naive_zone: str | ZoneInfo | None = None) -> datetime | None:
    """Return ``dt`` as aware UTC.

    A naive ``dt`` requires ``naive_zone``; without it a ``ValueError`` is raised rather than
    assuming a zone.
    """
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        if naive_zone is None:
            raise ValueError(f"naive datetime {dt.isoformat()} without declared zone")
        zone = ZoneInfo(naive_zone) if isinstance(naive_zone, str) else naive_zone
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(UTC)


def parse_timestamp(value: str | datetime | None, naive_zone: str | None = None) -> datetime | None:
    """Parse ISO-8601 (with or without offset) to aware UTC. Empty -> None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value, naive_zone)
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "NULL", "NAN", "UNKNOWN", "NAT"}:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text), naive_zone)


def to_zone(dt: datetime | None, zone: str = "Asia/Bangkok") -> datetime | None:
    if dt is None:
        return None
    return ensure_utc(dt, "UTC").astimezone(ZoneInfo(zone))


def format_ts(dt: datetime | None, zone: str = "Asia/Bangkok", unknown: str = "UNKNOWN") -> str:
    """'30 Sep 2026 08:00 ICT'."""
    if dt is None:
        return unknown
    local = to_zone(dt, zone)
    abbr = _ZONE_ABBR.get(zone, local.strftime("%Z") or zone)
    return f"{local.day:d} {local.strftime('%b %Y %H:%M')} {abbr}"


def format_duration(delta: timedelta | None, unknown: str = "UNKNOWN") -> str:
    """'14 h 35 min', '2 d 3 h 0 min', '-5 min' (negative = future)."""
    if delta is None:
        return unknown
    total_min = int(delta.total_seconds() // 60) if delta.total_seconds() >= 0 else -int(
        (-delta.total_seconds()) // 60
    )
    sign = "-" if total_min < 0 else ""
    m = abs(total_min)
    days, rem = divmod(m, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{sign}{days} d {hours} h {minutes} min"
    if hours:
        return f"{sign}{hours} h {minutes} min"
    return f"{sign}{minutes} min"


def compact_utc_stamp(dt: datetime) -> str:
    """Deterministic filename stamp: 20260930T010000123456Z."""
    return ensure_utc(dt, "UTC").strftime("%Y%m%dT%H%M%S%fZ")
