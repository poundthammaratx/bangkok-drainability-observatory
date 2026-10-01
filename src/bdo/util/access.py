"""Guards for the read-only VIEWER runtime (public Streamlit).

The Streamlit UI never writes to the database (see docs/architecture.md). The write-capable
entry points are the CLI/scripts: ingestion (``bdo ingest``), field-observation import
(``bdo import-observations``), and, since v0.3, the collector (``bdo collect``). These all call
``assert_writes_allowed()``, which blocks when *either*:

* ``Settings.public_deployment`` is true (the v0.1/v0.2 flag — kept exactly as-is for backward
  compatibility: every existing PUBLIC_DEPLOYMENT=true deployment stays blocked with no config
  change), or
* ``Settings.runtime_role`` is ``RuntimeRole.VIEWER`` (the v0.3 explicit role, set via
  ``BDO_RUNTIME_ROLE=VIEWER`` or implied automatically whenever ``public_deployment`` is true —
  see ``bdo.config._resolve_runtime_role``).

so a script or collector invocation accidentally pointed at the public deployment's database
cannot write to it either way. The startup baseline load (``bdo.bootstrap.ensure_seeded``) is
exempt: it only loads the packaged, repository-tracked SEED_*.csv files into an otherwise-empty
database and never accepts external input. The live read-through layer (``bdo.live``) is also
exempt in spirit, but by construction rather than by this guard: it never calls any write path at
all, under any role — see docs/DATABASE_DEPLOYMENT.md.
"""

from __future__ import annotations

from bdo.config import Settings


class PublicDeploymentBlocked(RuntimeError):
    """Raised when a write-capable operation is attempted outside the DEVELOPMENT/COLLECTOR roles."""


def assert_writes_allowed(settings: Settings, action: str) -> None:
    if settings.is_viewer:
        reason = "PUBLIC_DEPLOYMENT=true" if settings.public_deployment else f"runtime_role={settings.runtime_role.value}"
        raise PublicDeploymentBlocked(f"{action} is disabled: {reason} (read-only viewer).")
