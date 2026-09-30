"""Generic record -> NormalizedMeasurement mapping, driven by sources.yaml resource config.

Transformations applied here are explicit and flagged:
* naive source timestamps get the zone declared in config  -> TZ_DECLARED_BY_CONFIG
* separate date + time fields are combined                   (conflicts -> DATE_TIME_FIELD_CONFLICT)
* optional day/month transposition handling                  -> DATE_DM_AMBIGUOUS / DATE_REPAIRED_DM_SWAP
* units declared in config, not in the source                -> UNIT_DECLARED_UNVERIFIED
The original, as-published value of any repaired field is kept in ``notes``; the raw payload
is never modified.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from bdo.enums import EvidenceClass, QualityFlag
from bdo.schemas import NormalizedMeasurement
from bdo.util.time import ensure_utc

_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*$")


@dataclass
class ParsedTime:
    local_date: date | None
    local_time: time | None
    aware: datetime | None          # if the source carried an explicit offset
    flags: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    day_plus_one: bool = False      # "24:00"


def _parse_time_text(value: Any) -> tuple[time | None, bool]:
    if value is None:
        return None, False
    m = _TIME_RE.match(str(value))
    if not m:
        return None, False
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h == 24 and mi == 0 and s == 0:
        return time(0, 0), True
    if h > 23 or mi > 59 or s > 59:
        return None, False
    return time(h, mi, s), False


def parse_record_time(record: dict, time_cfg: dict) -> ParsedTime:
    date_field = time_cfg.get("date_field")
    time_field = time_cfg.get("time_field")
    raw_date = record.get(date_field) if date_field else None
    if raw_date in (None, ""):
        return ParsedTime(None, None, None, {QualityFlag.MEASUREMENT_TIME_UNKNOWN.value},
                          [f"{date_field} empty"])
    text = str(raw_date).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)  # raises -> record error
    pt = ParsedTime(dt.date(), dt.time() if (dt.hour or dt.minute or dt.second) else None,
                    dt if dt.tzinfo is not None else None)

    if time_field:
        t, plus1 = _parse_time_text(record.get(time_field))
        if record.get(time_field) not in (None, "") and t is None:
            pt.notes.append(f"unparseable {time_field}={record.get(time_field)!r}")
        if t is not None:
            if pt.local_time is not None and pt.local_time != t:
                pt.flags.add(QualityFlag.DATE_TIME_FIELD_CONFLICT.value)
                pt.notes.append(f"{date_field} time {pt.local_time} != {time_field} {t}; used {time_field}")
            pt.local_time = t
            pt.day_plus_one = plus1
            if plus1:
                pt.notes.append(f"{time_field}=24:00 read as 00:00 next day")
    return pt


def _swap_dm(d: date) -> date | None:
    if d.day > 12 or d.day == d.month:
        return None
    try:
        return date(d.year, d.day, d.month)
    except ValueError:
        return None


def resolve_dm_ambiguity(dates: list[date | None], mode: str) -> list[tuple[date | None, set[str], str | None]]:
    """Resolve possible day/month transposition.

    mode:
      ``none``            leave as published.
      ``flag``            flag every date whose day <= 12 and day != month.
      ``nearest_forward`` records are assumed to be in chronological order (source sequence). For
                          each record choose, between as-published and DM-swapped, the candidate
                          that is the smallest non-negative step from the previous resolved date.
                          Repairs are flagged; if neither candidate is >= previous, keep published
                          and flag DATE_NON_MONOTONIC.
    """
    out: list[tuple[date | None, set[str], str | None]] = []
    prev: date | None = None
    for d in dates:
        if d is None:
            out.append((None, set(), None))
            continue
        alt = _swap_dm(d)
        if mode == "none" or alt is None:
            chosen, flags, note = d, set(), None
            if mode == "nearest_forward" and prev is not None and d < prev:
                flags = {QualityFlag.DATE_NON_MONOTONIC.value}
        elif mode == "flag":
            chosen, flags, note = d, {QualityFlag.DATE_DM_AMBIGUOUS.value}, None
        elif mode == "nearest_forward":
            if prev is None:
                chosen, flags, note = d, {QualityFlag.DATE_DM_AMBIGUOUS.value}, None
            else:
                cands = [c for c in (d, alt) if c >= prev]
                if not cands:
                    chosen, flags, note = d, {QualityFlag.DATE_NON_MONOTONIC.value, QualityFlag.DATE_DM_AMBIGUOUS.value}, None
                else:
                    chosen = min(cands, key=lambda c: (c - prev, c != d))
                    if chosen != d:
                        flags = {QualityFlag.DATE_REPAIRED_DM_SWAP.value}
                        note = f"as-published date {d.isoformat()} read as {chosen.isoformat()} (DM swap, nearest_forward)"
                    else:
                        flags, note = set(), None
        else:
            raise ValueError(f"unknown dm_swap_repair mode {mode!r}")
        out.append((chosen, flags, note))
        prev = chosen if chosen is not None else prev
    return out


def combine_to_utc(local_date: date, local_time: time | None, zone: str, day_plus_one: bool = False) -> datetime:
    dt = datetime.combine(local_date, local_time or time(0, 0))
    if day_plus_one:
        dt += timedelta(days=1)
    return ensure_utc(dt, zone)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        s = str(value).strip().replace(",", "")
        if s in {"-", "--", "n/a", "N/A", "NA"}:
            return None
        f = float(s)
    return f if math.isfinite(f) else None


def map_record_variables(
    record: dict,
    variables: list[dict],
    measurement_at: datetime | None,
    evidence_class: EvidenceClass,
    base_flags: set[str],
    base_notes: list[str],
    station_external_id: str | None,
    external_record_id: str | None,
    payload_index: int,
) -> tuple[list[NormalizedMeasurement], list[str]]:
    out: list[NormalizedMeasurement] = []
    errors: list[str] = []
    for v in variables:
        fld = v["field"]
        raw_val = record.get(fld)
        if raw_val is None or (isinstance(raw_val, str) and raw_val.strip() == ""):
            continue  # absent value: no record (not a zero)
        flags = set(base_flags)
        if v.get("unit") and v.get("unit_basis", "declared_unverified") == "declared_unverified":
            flags.add(QualityFlag.UNIT_DECLARED_UNVERIFIED.value)
        try:
            if v.get("value_type", "numeric") == "text":
                value_num, value_text = None, str(raw_val).strip()
            else:
                value_num, value_text = _num(raw_val), None
                if value_num is None:
                    continue
        except ValueError:
            errors.append(f"record {external_record_id}: field {fld}={raw_val!r} not numeric")
            continue
        notes = list(base_notes)
        notes.append(f"source field: {fld}")
        out.append(NormalizedMeasurement(
            variable=v["variable"], evidence_class=evidence_class, value_num=value_num, value_text=value_text,
            unit=v.get("unit"), measurement_at=measurement_at, station_external_id=station_external_id,
            external_record_id=external_record_id, quality_flags=flags, notes="; ".join(notes),
            payload_index=payload_index,
        ))
    return out, errors
