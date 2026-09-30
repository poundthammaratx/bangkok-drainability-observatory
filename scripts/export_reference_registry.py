"""Build the packaged static reference topology under data/reference/.

Runs the existing, tested BKK CKAN adapter (bkk_open_data_dds) read-through — fetch + normalize
only, no RawSnapshot archiving, no database writes — and writes the resulting station/system-node
metadata to versioned CSV files plus a manifest recording provenance (retrieved_at, resource ids,
record counts, content hash, licence/attribution, schema version).

This is the "static / reference topology" half of the v0.2 architectural split (see
docs/LIVE_DATA_ARCHITECTURE.md): the public Streamlit deployment loads these packaged files when
the database has no coordinate-bearing stations of its own (a fresh PUBLIC_DEPLOYMENT container
never runs ingestion), so the map renders without depending on a previously populated local
SQLite database.

Usage: python scripts/export_reference_registry.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bdo.config import load_settings  # noqa: E402
from bdo.ingestion.adapters.bkk_ckan import CKANAdapter  # noqa: E402
from bdo.schemas import NormalizedStation  # noqa: E402
from bdo.util.time import compact_utc_stamp, utcnow  # noqa: E402

SCHEMA_VERSION = "1.0"

STATION_COLUMNS = [
    "external_station_id", "name", "node_type", "latitude", "longitude", "district",
    "source_type_code", "validation_status", "coordinate_evidence_class", "notes",
]

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "reference"


def _station_row(ns: NormalizedStation) -> dict:
    return {
        "external_station_id": ns.external_station_id,
        "name": ns.name,
        "node_type": ns.node_type.value,
        "latitude": ns.latitude,
        "longitude": ns.longitude,
        "district": ns.district,
        "source_type_code": ns.source_type_code,
        "validation_status": ns.validation_status.value,
        "coordinate_evidence_class": ns.coordinate_evidence_class.value if ns.coordinate_evidence_class else None,
        "notes": ns.notes,
    }


def _write_csv(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=STATION_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    settings = load_settings()
    cfg = settings.source("bkk_open_data_dds")
    adapter = CKANAdapter(cfg, settings)
    retrieved_at = utcnow()
    try:
        raw = adapter.fetch()
        norm = adapter.normalize(raw)
    finally:
        adapter.close()

    # station_registry-kind resources are the telemetry point network (coordinate-bearing);
    # everything else (dds011's fixed river-boundary station, frd per-record road nodes) is
    # "other drainage assets" — mostly without published coordinates, and never invented here.
    # NormalizedStation doesn't carry its source resource key, so classify provenance by
    # re-grouping the raw payload records per resource (same logic CKANAdapter.normalize() uses)
    # rather than by adapter output alone.
    seen: set[str] = set()
    telemetry: list[NormalizedStation] = []
    assets: list[NormalizedStation] = []
    by_key = {r["key"]: r for r in cfg.ckan.get("resources", [])}
    grouped: dict[str, list[tuple[int, dict]]] = {}
    mapping = raw.context.get("payload_resource") or [None] * len(raw.payloads)
    for idx, (payload, key) in enumerate(zip(raw.payloads, mapping)):
        if key is None:
            continue
        body = json.loads(payload.content.decode("utf-8"))
        if not body.get("success"):
            continue
        for rec in body["result"].get("records", []):
            grouped.setdefault(key, []).append((idx, rec))

    from bdo.enums import EvidenceClass
    from bdo.normalization import stations as st_mod

    evidence = EvidenceClass(cfg.evidence_class)
    for key, rows in grouped.items():
        res = by_key[key]
        kind = res.get("kind", "records")
        if kind == "station_registry":
            for idx, rec in rows:
                try:
                    ns = st_mod.station_from_registry_record(
                        rec, res["fields"], res.get("node_type_by_code_prefix", {}),
                        res.get("default_node_type", "other"), evidence, idx)
                except (ValueError, KeyError):
                    continue
                if ns.external_station_id in seen:
                    continue
                seen.add(ns.external_station_id)
                telemetry.append(ns)
        elif kind == "records":
            station_cfg = res.get("station", {"mode": "none"})
            if station_cfg.get("mode") == "fixed":
                ns = st_mod.station_fixed(station_cfg)
                if ns.external_station_id not in seen:
                    seen.add(ns.external_station_id)
                    assets.append(ns)
            elif station_cfg.get("mode") == "per_record":
                for idx, rec in rows:
                    try:
                        ns = st_mod.station_from_template(rec, station_cfg, idx)
                    except (KeyError, ValueError):
                        continue
                    if ns.external_station_id in seen:
                        continue
                    seen.add(ns.external_station_id)
                    assets.append(ns)

    telemetry_path = OUT_DIR / "bma_telemetry_stations.csv"
    assets_path = OUT_DIR / "bma_drainage_assets.csv"
    telemetry_hash = _write_csv(telemetry_path, [_station_row(s) for s in telemetry])
    assets_hash = _write_csv(assets_path, [_station_row(s) for s in assets])

    coord_bearing = sum(1 for s in telemetry + assets if s.latitude is not None)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at": retrieved_at.isoformat(),
        "generated_by": "scripts/export_reference_registry.py",
        "source": {
            "source_key": cfg.source_key,
            "name": cfg.name,
            "agency": cfg.agency,
            "base_url": cfg.base_url,
            "license": "License not specified by the publishing agency (inspected via CKAN "
                       "package_show; not invented here)",
            "attribution": f"{cfg.agency} — published via data.go.th / data.bangkok.go.th CKAN",
        },
        "datasets": [
            {
                "file": "bma_telemetry_stations.csv",
                "resource_key": "dds_telemetry_stations",
                "resource_id": "638f3adb-2fca-4767-b8f0-7528035ce319",
                "kind": "station_registry",
                "record_count": len(telemetry),
                "sha256": telemetry_hash,
            },
            {
                "file": "bma_drainage_assets.csv",
                "resource_keys": ["dds011_pak_khlong_talat_wl_max", "frd_dds_road_flood_statistics"],
                "kind": "derived station list (fixed + per-record, mostly no coordinates)",
                "record_count": len(assets),
                "sha256": assets_hash,
            },
        ],
        "totals": {
            "total_nodes": len(telemetry) + len(assets),
            "coordinate_bearing_nodes": coord_bearing,
        },
        "notes": (
            "No coordinates are fabricated: a node with an unpublished position is included with "
            "latitude/longitude empty and is never plotted on the map. Re-run this script to "
            "refresh the snapshot; it never writes to the project database."
        ),
    }
    manifest_path = OUT_DIR / "reference_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"telemetry stations: {len(telemetry)} ({sum(1 for s in telemetry if s.latitude is not None)} with coordinates)")
    print(f"drainage assets:    {len(assets)} ({sum(1 for s in assets if s.latitude is not None)} with coordinates)")
    print(f"total nodes:        {manifest['totals']['total_nodes']} "
          f"({manifest['totals']['coordinate_bearing_nodes']} with coordinates)")
    print(f"wrote {telemetry_path}, {assets_path}, {manifest_path}")


if __name__ == "__main__":
    main()
