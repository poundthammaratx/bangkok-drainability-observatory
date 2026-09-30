"""Generic CKAN adapter (package_search / package_show / datastore_search).

All dataset specifics (resource IDs, field names, units, time handling) come from the
``ckan:`` block of the source in config/sources.yaml. Nothing dataset-specific lives here.

Each datastore page and each package_show response is archived as its own RawSnapshot.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import httpx

from bdo.enums import EvidenceClass, QualityFlag, SourceStatus
from bdo.ingestion.base import SourceAdapter
from bdo.normalization import measurements as nm_mod
from bdo.normalization import stations as st_mod
from bdo.schemas import NormalizationResult, RawFetchResult, RawPayload, SourceHealth
from bdo.util.time import ensure_utc, utcnow


class CKANError(RuntimeError):
    pass


class CKANAdapter(SourceAdapter):
    parser_version = "ckan/0.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        ck = self.cfg.ckan or {}
        self.api_bases: list[str] = [b if b.endswith("/") else b + "/" for b in ck.get("api_base_urls", [])]
        if not self.api_bases and self.cfg.base_url:
            self.api_bases = [self.cfg.base_url.rstrip("/") + "/api/3/action/"]
        self.resources: list[dict] = ck.get("resources", [])
        self.active_base: str | None = None

    # -- low-level API ----------------------------------------------------------------------
    def _url(self, base: str, action: str, params: dict[str, Any]) -> str:
        return f"{base}{action}?{urlencode(params)}" if params else f"{base}{action}"

    def _get(self, action: str, params: dict[str, Any], base: str | None = None) -> tuple[httpx.Response, str]:
        bases = [base] if base else ([self.active_base] if self.active_base else self.api_bases)
        last_exc: Exception | None = None
        for b in bases:
            url = self._url(b, action, params)
            try:
                r = self.client.get(url)
                if r.status_code == 200:
                    self.active_base = b
                    return r, url
                last_exc = CKANError(f"HTTP {r.status_code} from {url}")
            except httpx.HTTPError as exc:
                last_exc = exc
        raise CKANError(str(last_exc) if last_exc else "no CKAN base URL configured")

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        body = r.json()
        if not body.get("success", False):
            raise CKANError(f"CKAN success=false: {str(body.get('error'))[:300]}")
        return body["result"]

    def package_search(self, q: str, rows: int = 50) -> dict:
        r, _ = self._get("package_search", {"q": q, "rows": rows})
        return self._json(r)

    def package_show(self, package_id: str) -> dict:
        r, _ = self._get("package_show", {"id": package_id})
        return self._json(r)

    def datastore_search(self, resource_id: str, limit: int = 100, offset: int = 0, sort: str | None = "_id asc") -> dict:
        params: dict[str, Any] = {"resource_id": resource_id, "limit": limit, "offset": offset}
        if sort:
            params["sort"] = sort
        r, _ = self._get("datastore_search", params)
        return self._json(r)

    # -- contract ---------------------------------------------------------------------------
    def enabled_resources(self) -> list[dict]:
        return [r for r in self.resources if r.get("enabled", True)]

    def healthcheck(self) -> SourceHealth:
        if not self.api_bases:
            return SourceHealth(status=SourceStatus.UNAVAILABLE, message="no CKAN api_base_urls configured")
        host_errors: dict[str, str] = {}
        for base in self.api_bases:
            try:
                r, _ = self._get("status_show", {}, base=base)
                self._json(r)
                self.active_base = base
                break
            except (CKANError, ValueError, httpx.HTTPError) as exc:
                host_errors[base] = str(exc)[:300]
        if self.active_base is None:
            return SourceHealth(status=SourceStatus.UNAVAILABLE, message="no CKAN host reachable",
                                detail={"host_errors": host_errors})
        res_ok, res_err = [], {}
        for res in self.enabled_resources():
            try:
                self.datastore_search(res["resource_id"], limit=0, sort=None)
                res_ok.append(res["key"])
            except (CKANError, ValueError, httpx.HTTPError) as exc:
                res_err[res["key"]] = str(exc)[:300]
        if not res_err:
            status, msg = SourceStatus.ACTIVE, f"{len(res_ok)} resource(s) reachable via {self.active_base}"
        elif res_ok:
            status, msg = SourceStatus.DEGRADED, f"{len(res_ok)} ok, {len(res_err)} failing via {self.active_base}"
        else:
            status, msg = SourceStatus.UNAVAILABLE, f"host up ({self.active_base}) but no resource reachable"
        return SourceHealth(status=status, message=msg,
                            detail={"active_base": self.active_base, "resources_ok": res_ok,
                                    "resource_errors": res_err, "host_errors": host_errors})

    def fetch(self) -> RawFetchResult:
        retrieved_at = utcnow()
        payloads: list[RawPayload] = []
        payload_resource: list[str | None] = []
        fetch_errors: dict[str, str] = {}

        for res in self.enabled_resources():
            key, rid = res["key"], res["resource_id"]
            # dataset metadata (license, metadata_modified) — provenance, not measurements
            if res.get("package"):
                try:
                    r, url = self._get("package_show", {"id": res["package"]})
                    payloads.append(RawPayload(content=r.content, content_type="application/json",
                                               label=f"{key}_package_show", extension="json",
                                               request_url=url, http_status=r.status_code))
                    payload_resource.append(None)
                except CKANError as exc:
                    fetch_errors[f"{key}:package_show"] = str(exc)[:300]

            page_size = int(res.get("page_size", 1000))
            max_records = int(res.get("max_records", 50000))
            offset, page = 0, 0
            while offset < max_records:
                limit = min(page_size, max_records - offset)
                params = {"resource_id": rid, "limit": limit, "offset": offset, "sort": "_id asc"}
                try:
                    r, url = self._get("datastore_search", params)
                    result = self._json(r)
                except (CKANError, ValueError, httpx.HTTPError) as exc:
                    fetch_errors[f"{key}:offset={offset}"] = str(exc)[:300]
                    break
                payloads.append(RawPayload(content=r.content, content_type="application/json",
                                           label=f"{key}_p{page:04d}", extension="json",
                                           request_url=url, http_status=r.status_code))
                payload_resource.append(key)
                n = len(result.get("records", []))
                total = result.get("total")
                offset += n
                page += 1
                if n < limit or (total is not None and offset >= int(total)):
                    break

        return RawFetchResult(
            source_key=self.cfg.source_key, retrieved_at=retrieved_at, payloads=payloads,
            context={"payload_resource": payload_resource, "fetch_errors": fetch_errors,
                     "active_base": self.active_base},
        )

    def normalize(self, raw: RawFetchResult) -> NormalizationResult:
        result = NormalizationResult()
        by_key = {r["key"]: r for r in self.resources}
        mapping: list[str | None] = raw.context.get("payload_resource") or [None] * len(raw.payloads)

        # gather records per resource, preserving page order; remember payload index per record
        grouped: dict[str, list[tuple[int, dict]]] = {}
        for idx, (payload, key) in enumerate(zip(raw.payloads, mapping)):
            if key is None:
                continue
            body = json.loads(payload.content.decode("utf-8"))
            if not body.get("success"):
                result.record_errors.append(f"{key}: payload {idx} success=false")
                continue
            for rec in body["result"].get("records", []):
                grouped.setdefault(key, []).append((idx, rec))

        evidence = EvidenceClass(self.cfg.evidence_class)
        for key, rows in grouped.items():
            res = by_key[key]
            kind = res.get("kind", "records")
            result.records_seen += len(rows)
            if kind == "raw_only":
                continue
            if kind == "station_registry":
                for idx, rec in rows:
                    try:
                        result.stations.append(st_mod.station_from_registry_record(
                            rec, res["fields"], res.get("node_type_by_code_prefix", {}),
                            res.get("default_node_type", "other"), evidence, idx))
                    except (ValueError, KeyError) as exc:
                        result.record_errors.append(f"{key} _id={rec.get('_id')}: {exc}")
                continue
            if kind == "records":
                self._normalize_records(res, rows, evidence, result)
                continue
            result.record_errors.append(f"{key}: unknown resource kind {kind!r}")
        return result

    def _normalize_records(self, res: dict, rows: list[tuple[int, dict]], evidence: EvidenceClass,
                           result: NormalizationResult) -> None:
        key = res["key"]
        time_cfg = res.get("time", {})
        zone = time_cfg.get("source_timezone", self.settings.display_timezone)
        station_cfg = res.get("station", {"mode": "none"})
        id_field = res.get("record_id_field", "_id")

        fixed_station = None
        if station_cfg.get("mode") == "fixed":
            fixed_station = st_mod.station_fixed(station_cfg)
            result.stations.append(fixed_station)
        station_seen: set[str] = set()

        parsed: list[tuple[int, dict, nm_mod.ParsedTime | None, str | None]] = []
        for idx, rec in rows:
            try:
                parsed.append((idx, rec, nm_mod.parse_record_time(rec, time_cfg), None))
            except (ValueError, TypeError) as exc:
                parsed.append((idx, rec, None, f"time parse: {exc}"))

        resolved = nm_mod.resolve_dm_ambiguity(
            [p.local_date if p else None for _, _, p, _ in parsed], time_cfg.get("dm_swap_repair", "none"))

        for (idx, rec, pt, err), (rdate, dm_flags, dm_note) in zip(parsed, resolved):
            rec_id = None if rec.get(id_field) is None else f"{key}:{rec.get(id_field)}"
            flags: set[str] = set(dm_flags)
            notes: list[str] = [f"resource {key}"]
            measurement_at = None
            if err:
                result.record_errors.append(f"{rec_id}: {err} — stored with measurement time UNKNOWN")
                flags.add(QualityFlag.MEASUREMENT_TIME_UNKNOWN.value)
            elif pt is not None:
                flags |= pt.flags
                notes += pt.notes
                if dm_note:
                    notes.append(dm_note)
                if pt.aware is not None:
                    measurement_at = ensure_utc(pt.aware.replace(
                        year=rdate.year, month=rdate.month, day=rdate.day) if rdate else pt.aware)
                elif rdate is not None:
                    measurement_at = nm_mod.combine_to_utc(rdate, pt.local_time, zone, pt.day_plus_one)
                    flags.add(QualityFlag.TZ_DECLARED_BY_CONFIG.value)
                    if pt.local_time is None:
                        notes.append("no time of day published; 00:00 local used")

            station_ext = None
            if fixed_station is not None:
                station_ext = fixed_station.external_station_id
            elif station_cfg.get("mode") == "per_record":
                try:
                    ns = st_mod.station_from_template(rec, station_cfg, idx)
                    station_ext = ns.external_station_id
                    if station_ext not in station_seen:
                        station_seen.add(station_ext)
                        result.stations.append(ns)
                except (KeyError, ValueError) as exc:
                    result.record_errors.append(f"{rec_id}: station template: {exc}")

            try:
                ms, errs = nm_mod.map_record_variables(
                    rec, res.get("variables", []), measurement_at, evidence, flags, notes,
                    station_ext, rec_id, idx)
            except ValueError as exc:  # pydantic validation of a mapped record
                result.record_errors.append(f"{rec_id}: {exc}")
                continue
            result.measurements.extend(ms)
            result.record_errors.extend(errs)
