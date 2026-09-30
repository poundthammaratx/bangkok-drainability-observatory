"""Guards for the PUBLIC_DEPLOYMENT read-only mode.

The Streamlit UI never writes to the database (see docs/architecture.md). The write-capable
entry points are the CLI/scripts: ingestion (``bdo ingest``) and field-observation import
(``bdo import-observations``). When ``PUBLIC_DEPLOYMENT=true`` these refuse to run, so a script
accidentally pointed at the public deployment's database cannot write to it. The startup baseline
load (``bdo.bootstrap.ensure_seeded``) is exempt: it only loads the packaged, repository-tracked
SEED_*.csv files into an otherwise-empty database and never accepts external input.
"""

from __future__ import annotations

from bdo.config import Settings


class PublicDeploymentBlocked(RuntimeError):
    """Raised when a write-capable operation is attempted under PUBLIC_DEPLOYMENT=true."""


def assert_writes_allowed(settings: Settings, action: str) -> None:
    if settings.public_deployment:
        raise PublicDeploymentBlocked(
            f"{action} is disabled: PUBLIC_DEPLOYMENT=true (public read-only alpha)."
        )
