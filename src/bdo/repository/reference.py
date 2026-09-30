"""Static reference topology (data/reference/*.csv), packaged with the repository.

A fresh PUBLIC_DEPLOYMENT container has an empty or freshly-bootstrapped database and never runs
ingestion, so the map cannot depend on a previously populated ``stations`` table. This module
loads the versioned CSV snapshot produced by ``scripts/export_reference_registry.py`` instead —
read-only, no network, no database.

The manifest and CSVs are tracked in git (unlike ``data/*.sqlite``, which is gitignored research
state); see ``docs/LIVE_DATA_ARCHITECTURE.md``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

REFERENCE_DIR = Path(__file__).resolve().parents[3] / "data" / "reference"


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    path = REFERENCE_DIR / "reference_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(name: str) -> pd.DataFrame:
    path = REFERENCE_DIR / name
    if not path.exists():
        return pd.DataFrame(columns=[
            "external_station_id", "name", "node_type", "latitude", "longitude", "district",
            "source_type_code", "validation_status", "coordinate_evidence_class", "notes",
        ])
    return pd.read_csv(path)


@lru_cache(maxsize=1)
def _load_topology_cached() -> pd.DataFrame:
    telemetry = _load_csv("bma_telemetry_stations.csv").assign(reference_dataset="bma_telemetry_stations")
    assets = _load_csv("bma_drainage_assets.csv").assign(reference_dataset="bma_drainage_assets")
    df = pd.concat([telemetry, assets], ignore_index=True)
    df["source_key"] = "bkk_open_data_dds"
    df["operator"] = "Bangkok Metropolitan Administration"
    return df


def load_topology() -> pd.DataFrame:
    """All packaged reference nodes (with and without coordinates); never fabricates positions."""
    return _load_topology_cached().copy()


def load_coordinate_topology() -> pd.DataFrame:
    """Only nodes with a published latitude/longitude — the set that may ever be plotted."""
    df = load_topology()
    return df.dropna(subset=["latitude", "longitude"])
