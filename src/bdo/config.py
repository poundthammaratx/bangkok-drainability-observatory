"""Configuration loading (config/settings.yaml, config/sources.yaml, environment/.env).

Relative paths resolve against the repository root so that scripts, Streamlit and tests behave
identically regardless of the working directory.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bdo.enums import EvidenceClass, RuntimeRole, SourceStatus

REPO_ROOT = Path(__file__).resolve().parents[2]


class FreshnessThresholds(BaseModel):
    live_minutes: float = 60
    recent_minutes: float = 360
    stale_minutes: float = 1440

    @field_validator("stale_minutes")
    @classmethod
    def _ordered(cls, v, info):
        d = info.data
        if not (d.get("live_minutes", 0) <= d.get("recent_minutes", 0) <= v):
            raise ValueError("freshness thresholds must satisfy live <= recent <= stale")
        return v


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_key: str
    name: str
    agency: str
    base_url: str | None = None
    data_domain: str | None = None
    access_mode: str = "manual"
    adapter: str = "manual_csv"
    enabled: bool = False
    priority: str = "medium"
    status: SourceStatus = SourceStatus.UNKNOWN
    evidence_class: EvidenceClass
    typical_freshness_minutes: int | None = None
    freshness: FreshnessThresholds | None = None
    archive_page: bool = False
    notes: str | None = None
    ckan: dict[str, Any] | None = None


class Settings(BaseModel):
    project_name: str = "Bangkok Drainability Observatory"
    research_title: str = ""
    version: str = "0.3.0-dev"
    display_timezone: str = "Asia/Bangkok"
    database_url: str = "sqlite:///data/bdo.sqlite"
    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    http_timeout_seconds: float = 30
    http_user_agent: str = "BangkokDrainabilityObservatory/0.1"
    http_trust_env: bool = True
    freshness_defaults: FreshnessThresholds = Field(default_factory=FreshnessThresholds)
    log_level: str = "INFO"
    log_file: Path | None = None
    # Public read-only alpha deployment (Streamlit Community Cloud). When true, the Streamlit UI
    # hides admin/diagnostic controls and internal file paths, and write-capable entry points
    # (ingestion, field-observation import) refuse to run. See docs/PUBLIC_ALPHA_CHECKLIST.md.
    public_deployment: bool = False
    # Runtime role (v0.3): see bdo.enums.RuntimeRole and docs/DATABASE_DEPLOYMENT.md. Independent
    # of public_deployment, which remains authoritative on its own — assert_writes_allowed() blocks
    # writes when *either* public_deployment is true *or* runtime_role is VIEWER.
    runtime_role: RuntimeRole = RuntimeRole.DEVELOPMENT
    # Whether a persistent archive (PostgreSQL/PostGIS in production; SQLite in dev) is expected to
    # be reachable. When false, archive-dependent UI sections degrade to live-read-through-only
    # rather than attempting a connection — see docs/PERSISTENT_ARCHIVE.md.
    archive_enabled: bool = False
    # Read-through live-source cache TTLs (seconds); see src/bdo/live/ and
    # docs/LIVE_DATA_ARCHITECTURE.md. Never affects writes — the live layer never writes.
    live_default_ttl_seconds: int = 300
    live_ttl_seconds: dict[str, int] = Field(default_factory=dict)
    sources: list[SourceConfig] = Field(default_factory=list)

    @property
    def is_viewer(self) -> bool:
        """True for the public Streamlit process — reads and live GETs only, never writes."""
        return self.public_deployment or self.runtime_role is RuntimeRole.VIEWER

    def live_ttl_for(self, source_key: str) -> int:
        """Cache TTL (seconds) for one live source's read-through fetch (see ``bdo.live.manager``).

        An unknown ``source_key`` (not present in ``live_ttl_seconds``) never raises — it fails
        safe by returning ``live_default_ttl_seconds`` (300s unless overridden), the same default
        applied when ``config/settings.yaml`` declares no ``live:`` block at all.
        """
        return self.live_ttl_seconds.get(source_key, self.live_default_ttl_seconds)

    # --- derived paths -------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def manual_dir(self) -> Path:
        return self.data_dir / "manual"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def source(self, source_key: str) -> SourceConfig:
        for s in self.sources:
            if s.source_key == source_key:
                return s
        raise KeyError(f"source '{source_key}' not in sources.yaml")

    def thresholds_for(self, source_key: str | None) -> FreshnessThresholds:
        if source_key:
            try:
                s = self.source(source_key)
                if s.freshness:
                    return s.freshness
            except KeyError:
                pass
        return self.freshness_defaults

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.manual_dir, self.processed_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)


def _parse_bool(v: str | bool) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_runtime_role(public_deployment: bool) -> RuntimeRole:
    """``BDO_RUNTIME_ROLE`` wins if set; otherwise VIEWER under PUBLIC_DEPLOYMENT, else
    DEVELOPMENT — matching v0.1/v0.2 behaviour for every existing local/test invocation."""
    raw = os.environ.get("BDO_RUNTIME_ROLE")
    if raw:
        try:
            return RuntimeRole(raw.strip().upper())
        except ValueError as exc:
            valid = ", ".join(r.value for r in RuntimeRole)
            raise ValueError(f"BDO_RUNTIME_ROLE={raw!r} is not one of: {valid}") from exc
    return RuntimeRole.VIEWER if public_deployment else RuntimeRole.DEVELOPMENT


def _resolve(p: str | Path, root: Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (root / p)


def _resolve_db_url(url: str, root: Path) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and url != "sqlite:///:memory:":
        rel = url[len(prefix):]
        return prefix + str(_resolve(rel, root))
    return url


def load_settings(config_dir: str | Path | None = None, root: Path | None = None, **overrides) -> Settings:
    root = root or REPO_ROOT
    load_dotenv(root / ".env", override=False)
    config_dir = _resolve(config_dir or os.environ.get("BDO_CONFIG_DIR", "config"), root)

    raw: dict[str, Any] = {}
    settings_path = config_dir / "settings.yaml"
    if settings_path.exists():
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    sources_raw: dict[str, Any] = {}
    sources_path = config_dir / "sources.yaml"
    if sources_path.exists():
        sources_raw = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}

    project = raw.get("project", {})
    paths = raw.get("paths", {})
    http = raw.get("http", {})
    logcfg = raw.get("logging", {})

    db_url = os.environ.get("BDO_DATABASE_URL", paths.get("database_url", "sqlite:///data/bdo.sqlite"))
    data_dir = os.environ.get("BDO_DATA_DIR", paths.get("data_dir", "data"))
    log_file = logcfg.get("file")

    values: dict[str, Any] = dict(
        project_name=project.get("name", "Bangkok Drainability Observatory"),
        research_title=project.get("research_title", ""),
        version=project.get("version", "0.3.0-dev"),
        display_timezone=raw.get("display_timezone", "Asia/Bangkok"),
        database_url=_resolve_db_url(db_url, root),
        data_dir=_resolve(data_dir, root),
        config_dir=config_dir,
        http_timeout_seconds=http.get("timeout_seconds", 30),
        http_user_agent=http.get("user_agent", "BangkokDrainabilityObservatory/0.1"),
        http_trust_env=http.get("trust_env", True),
        freshness_defaults=FreshnessThresholds(**raw.get("freshness_defaults", {})),
        log_level=os.environ.get("BDO_LOG_LEVEL", logcfg.get("level", "INFO")),
        log_file=_resolve(log_file, root) if log_file else None,
        public_deployment=_parse_bool(os.environ.get("PUBLIC_DEPLOYMENT", "false")),
        runtime_role=_resolve_runtime_role(_parse_bool(os.environ.get("PUBLIC_DEPLOYMENT", "false"))),
        archive_enabled=_parse_bool(os.environ.get("BDO_ARCHIVE_ENABLED", "false")),
        live_default_ttl_seconds=(raw.get("live", {}) or {}).get("default_ttl_seconds", 300),
        live_ttl_seconds=(raw.get("live", {}) or {}).get("ttl_seconds", {}) or {},
        sources=[SourceConfig(**s) for s in sources_raw.get("sources", [])],
    )
    values.update(overrides)
    return Settings(**values)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
