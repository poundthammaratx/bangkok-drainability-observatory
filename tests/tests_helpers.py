"""Shared offline CKAN mock (verbatim records from the 2026-09-30 inspection)."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from bdo.ingestion.adapters.bkk_ckan import CKANAdapter

FIX = Path(__file__).parent / "fixtures"
RES = {
    "638f3adb-2fca-4767-b8f0-7528035ce319": "ckan_telemetry_records.json",
    "313cd96f-8610-495d-875e-f6d0ec08a3bc": "ckan_dds011_records.json",
    "95716a53-4544-4c9b-af07-63298ace7c07": "ckan_frd_records.json",
}


def handler(fail_host: str | None):
    def handle(request: httpx.Request) -> httpx.Response:
        if fail_host and request.url.host == fail_host:
            raise httpx.ConnectError("simulated outage", request=request)
        action = urlparse(str(request.url)).path.rstrip("/").split("/")[-1]
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if action == "status_show":
            return httpx.Response(200, json={"success": True, "result": {"ckan_version": "2.9"}})
        if action == "package_show":
            return httpx.Response(200, json={"success": True, "result": {"name": q["id"], "license_id": "cc-by"}})
        if action == "datastore_search":
            data = json.loads((FIX / RES[q["resource_id"]]).read_text(encoding="utf-8"))
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
            recs = data["records"][off:off + lim]
            return httpx.Response(200, json={"success": True, "result": {
                "resource_id": q["resource_id"], "fields": data["fields"], "records": recs,
                "total": len(data["records"]), "limit": lim, "offset": off}})
        return httpx.Response(404, json={"success": False})
    return handle


def ckan_adapter(settings, fail_host=None, page_size=5):
    cfg = settings.source("bkk_open_data_dds").model_copy(deep=True)
    for r in cfg.ckan["resources"]:
        r["page_size"] = page_size
    client = httpx.Client(transport=httpx.MockTransport(handler(fail_host)))
    return CKANAdapter(cfg, settings, client=client)
