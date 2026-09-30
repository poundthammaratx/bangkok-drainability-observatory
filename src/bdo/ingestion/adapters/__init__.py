"""Adapter registry: sources.yaml ``adapter:`` key -> class."""

from bdo.ingestion.adapters.bkk_ckan import CKANAdapter
from bdo.ingestion.adapters.floodbangkok import FloodBangkokAdapter
from bdo.ingestion.adapters.manual_csv import ManualCSVAdapter
from bdo.ingestion.adapters.rid_report import RIDReportAdapter
from bdo.ingestion.adapters.thaiwater import ThaiWaterAdapter
from bdo.ingestion.adapters.tmd_radar import TMDRadarAdapter

ADAPTERS = {
    "ckan": CKANAdapter,
    "manual_csv": ManualCSVAdapter,
    "floodbangkok": FloodBangkokAdapter,
    "thaiwater": ThaiWaterAdapter,
    "tmd_radar": TMDRadarAdapter,
    "rid_report": RIDReportAdapter,
}

#: adapters that genuinely retrieve and parse data automatically in v0.1
IMPLEMENTED_AUTOMATIC = {"ckan"}

__all__ = ["ADAPTERS", "IMPLEMENTED_AUTOMATIC", "CKANAdapter", "ManualCSVAdapter"]
