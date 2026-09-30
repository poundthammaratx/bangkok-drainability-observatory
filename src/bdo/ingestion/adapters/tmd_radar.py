"""TMD Bangkok weather radar — adapter shell.

Status: UNAVAILABLE. v0.1 may archive the radar page (``archive_page: true``) so that image URLs
and page metadata are preserved byte-exact; it performs NO image parsing and NO computer-vision
rainfall estimation. Radar frame timestamps must come from the product itself, never from the
page retrieval time.
"""

from bdo.enums import SourceStatus
from bdo.ingestion.base import ShellAdapter


class TMDRadarAdapter(ShellAdapter):
    parser_version = "tmd-radar-shell/0.1"
    shell_status = SourceStatus.UNAVAILABLE
    reason = "radar image/metadata discovery not yet verified; no rainfall estimation in v0.1"
