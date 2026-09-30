"""Register sources and load SEED demonstration records.

Usage: see README.md (`python scripts/seed_sources.py --help`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from bdo.cli import seed_cmd  # noqa: E402

if __name__ == "__main__":
    typer.run(seed_cmd)
