"""Validate and import a field-observation CSV.

Usage: see README.md (`python scripts/import_field_observations.py --help`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from bdo.cli import import_observations_cmd  # noqa: E402

if __name__ == "__main__":
    typer.run(import_observations_cmd)
