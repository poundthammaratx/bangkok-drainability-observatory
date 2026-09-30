"""Run ingestion for one source (--source) or all enabled (--all).

Usage: see README.md (`python scripts/ingest.py --help`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from bdo.cli import ingest_cmd  # noqa: E402

if __name__ == "__main__":
    typer.run(ingest_cmd)
