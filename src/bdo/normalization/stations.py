"""Station / system-node normalisation helpers."""

from __future__ import annotations

from string import Formatter
from typing import Any

from bdo.enums import EvidenceClass, NodeType, ValidationStatus
from bdo.schemas import NormalizedStation


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def render_template(template: str, record: dict) -> str:
    keys = [f for _, f, _, _ in Formatter().parse(template) if f]
    safe = {k: ("" if record.get(k) is None else str(record.get(k)).strip()) for k in keys}
    return template.format(**safe)


def infer_node_type(code: str | None, prefix_map: dict[str, str], default: str) -> tuple[NodeType, bool]:
    """Return (node_type, inferred). ``inferred`` is True when derived from a code prefix."""
    if code:
        for prefix, nt in prefix_map.items():
            if code.startswith(prefix):
                return NodeType(nt), True
    return NodeType(default), False


def station_from_registry_record(
    record: dict, fields: dict, prefix_map: dict[str, str], default_type: str,
    coord_evidence: EvidenceClass, payload_index: int,
) -> NormalizedStation:
    ext = record.get(fields["external_id"])
    if ext in (None, ""):
        raise ValueError("station registry record without external id")
    ext = str(ext).strip()
    lat = _float(record.get(fields.get("latitude")))
    lon = _float(record.get(fields.get("longitude")))
    # (0,0) and single-sided coordinates are treated as missing, not as a location.
    if lat is None or lon is None or (lat == 0 and lon == 0):
        lat = lon = None
    code = record.get(fields.get("type_code")) if fields.get("type_code") else None
    node_type, inferred = infer_node_type(ext, prefix_map, default_type)
    notes = []
    if inferred:
        notes.append("node_type inferred from station_code prefix")
    return NormalizedStation(
        external_station_id=ext,
        name=str(record.get(fields.get("name")) or ext).strip(),
        node_type=node_type,
        latitude=lat,
        longitude=lon,
        district=(str(record.get(fields.get("district"))).strip() if record.get(fields.get("district")) else None),
        validation_status=ValidationStatus.TYPE_INFERRED if inferred else ValidationStatus.UNVERIFIED,
        source_type_code=None if code is None else str(code),
        coordinate_evidence_class=coord_evidence if lat is not None else None,
        notes="; ".join(notes) or None,
        payload_index=payload_index,
    )


def station_from_template(record: dict, station_cfg: dict, payload_index: int) -> NormalizedStation:
    ext = render_template(station_cfg["id_template"], record)
    name = render_template(station_cfg.get("name_template", station_cfg["id_template"]), record)
    district_field = station_cfg.get("district_field")
    return NormalizedStation(
        external_station_id=ext,
        name=name.strip(" —-") or ext,
        node_type=NodeType(station_cfg.get("node_type", "other")),
        district=(str(record.get(district_field)).strip() if district_field and record.get(district_field) else None),
        validation_status=ValidationStatus.UNVERIFIED,
        notes="derived from per-record location fields; no coordinates published",
        payload_index=payload_index,
    )


def station_fixed(station_cfg: dict) -> NormalizedStation:
    return NormalizedStation(
        external_station_id=station_cfg["external_station_id"],
        name=station_cfg.get("name", station_cfg["external_station_id"]),
        node_type=NodeType(station_cfg.get("node_type", "other")),
        district=station_cfg.get("district"),
        validation_status=ValidationStatus.UNVERIFIED,
        notes=station_cfg.get("notes"),
    )
