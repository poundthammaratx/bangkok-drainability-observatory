"""Create the database schema and data directories.

Usage: see README.md (`python scripts/init_db.py --help`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import typer  # noqa: E402

from bdo.cli import init_db_cmd  # noqa: E402

if __name__ == "__main__":
    typer.run(init_db_cmd)
