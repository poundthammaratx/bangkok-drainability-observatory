"""RID water-situation bulletin — adapter shell.

Status: MANUAL. The official bulletin structure has not yet been inspected programmatically, so no
parser exists. Each RID figure refers to the bulletin's stated observation time (commonly 06:00,
07:00 or 08:00 ICT), which differs from the page retrieval time and may differ between tables on
the same page. A future parser must carry a per-table measurement time.

Current practice: transcribe into ``data/manual/rid_water_situation/*.csv`` with an explicit
``measurement_at`` and ``measurement_timezone`` for every row.
"""

from bdo.enums import SourceStatus
from bdo.ingestion.base import ShellAdapter


class RIDReportAdapter(ShellAdapter):
    parser_version = "rid-shell/0.1"
    shell_status = SourceStatus.MANUAL
    reason = ("RID bulletin structure not yet inspected; "
              "transcribe to data/manual/rid_water_situation/ with explicit measurement_at")
