"""Shared fixtures: every test gets an isolated SQLite DB and data directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from bdo.config import load_settings  # noqa: E402
from bdo.database import init_db, session_factory, session_scope  # noqa: E402
from bdo.enums import EvidenceClass, SourceStatus  # noqa: E402
from bdo.ingestion.base import SourceAdapter  # noqa: E402
from bdo.repository import sources as source_repo  # noqa: E402
from bdo.schemas import (  # noqa: E402
    NormalizationResult,
    NormalizedMeasurement,
    RawFetchResult,
    RawPayload,
    SourceHealth,
)
from bdo.util.time import parse_timestamp, utcnow  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture()
def settings(tmp_path):
    data = tmp_path / "data"
    s = load_settings(database_url=f"sqlite:///{tmp_path / 'test.sqlite'}", data_dir=data, log_file=None)
    init_db(s)
    with session_scope(s) as session:
        for cfg in s.sources:
            source_repo.upsert_from_config(session, cfg)
    return s


@pytest.fixture()
def sessions(settings):
    return session_factory(settings)


@pytest.fixture()
def seeded(settings):
    """Load the repository's real SEED files into the isolated DB."""
    from bdo.ingestion.runner import run_source

    out = []
    for key in ("rid_water_situation", "bma_floodbangkok"):
        files = sorted((ROOT / "data" / "manual" / key).glob("SEED_*.csv"))
        assert files, f"seed file missing for {key}"
        out.append(run_source(settings, key, manual=True, paths=files, mode="seed"))
    return out


class FakeAdapter(SourceAdapter):
    """Deterministic in-memory adapter for runner tests."""

    parser_version = "fake/1"

    def __init__(self, cfg, settings, records, retrieved_at=None, fail_normalize=False, status=SourceStatus.ACTIVE):
        super().__init__(cfg, settings)
        self.records = records
        self.retrieved_at = retrieved_at or utcnow()
        self.fail_normalize = fail_normalize
        self.status = status

    def healthcheck(self):
        return SourceHealth(status=self.status, message="fake")

    def fetch(self):
        body = json.dumps(self.records, sort_keys=True).encode()
        return RawFetchResult(source_key=self.cfg.source_key, retrieved_at=self.retrieved_at,
                              payloads=[RawPayload(content=body, content_type="application/json",
                                                   label="fake", extension="json", http_status=200,
                                                   request_url="https://example.invalid/fake")])

    def normalize(self, raw):
        if self.fail_normalize:
            raise RuntimeError("simulated parser crash")
        recs = json.loads(raw.payloads[0].content)
        return NormalizationResult(records_seen=len(recs), measurements=[
            NormalizedMeasurement(
                variable=r["variable"], value_num=r.get("value"), value_text=r.get("text"),
                unit=r.get("unit"), measurement_at=parse_timestamp(r.get("t")) if r.get("t") else None,
                evidence_class=EvidenceClass(r.get("ev", "OFFICIAL_REPORTED")),
                station_external_id=r.get("station"), external_record_id=r.get("rid"),
            ) for r in recs])


@pytest.fixture()
def fake_adapter_factory(settings):
    def make(records, source_key="thaiwater_bangkok", **kw):
        return FakeAdapter(settings.source(source_key), settings, records, **kw)
    return make
