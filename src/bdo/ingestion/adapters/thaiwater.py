"""ThaiWater Bangkok (Hydro-Informatics Institute) — adapter interface.

Status: UNAVAILABLE. Automatic ingestion is to be implemented only once an official public API
endpoint (with documented station IDs and timestamps) is identified. Until then this shell reports
UNAVAILABLE and fabricates nothing. ``archive_page: true`` in sources.yaml enables byte-exact
archiving of the landing page only (no value extraction).
"""

from bdo.enums import SourceStatus
from bdo.ingestion.base import ShellAdapter


class ThaiWaterAdapter(ShellAdapter):
    parser_version = "thaiwater-shell/0.1"
    shell_status = SourceStatus.UNAVAILABLE
    reason = "no official public ThaiWater API endpoint verified for v0.1"
