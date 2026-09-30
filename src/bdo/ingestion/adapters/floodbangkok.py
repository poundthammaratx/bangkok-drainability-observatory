"""FloodBangkok (BMA DDS) — adapter shell.

Status: MANUAL. As of v0.1 no stable, documented public machine endpoint has been verified for
road flood-depth sensors or CCTV. Scraping the dashboard's rendered page would be brittle and is
deliberately not done (source_policy.md §2).

Current practice: transcribe dashboard snapshots into
``data/manual/bma_floodbangkok/*.csv`` and ingest with
``python scripts/ingest.py --source bma_floodbangkok --manual``.

To implement: once an endpoint is verified, override ``healthcheck/fetch/normalize`` here; keep
``fetch`` byte-exact and put sensor timestamps (not page time) into ``measurement_at``.
"""

from bdo.enums import SourceStatus
from bdo.ingestion.base import ShellAdapter


class FloodBangkokAdapter(ShellAdapter):
    parser_version = "floodbangkok-shell/0.1"
    shell_status = SourceStatus.MANUAL
    reason = ("no verified public machine endpoint for FloodBangkok; "
              "transcribe snapshots to data/manual/bma_floodbangkok/ and ingest with --manual")
