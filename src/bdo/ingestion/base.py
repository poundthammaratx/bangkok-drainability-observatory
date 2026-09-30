"""Adapter contract.

``fetch()`` and ``normalize()`` are separate on purpose: the runner archives every payload
returned by ``fetch()`` *before* calling ``normalize()``, so a parser crash can never lose the
source material.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from bdo.config import Settings, SourceConfig
from bdo.enums import SourceStatus
from bdo.schemas import NormalizationResult, RawFetchResult, SourceHealth


class AdapterNotImplemented(NotImplementedError):
    """Raised by adapter shells whose automatic retrieval is not yet verified."""


class SourceAdapter(ABC):
    #: bump whenever normalisation semantics change; stored on every snapshot and measurement
    parser_version: str = "0.0"

    def __init__(self, cfg: SourceConfig, settings: Settings, client: httpx.Client | None = None):
        self.cfg = cfg
        self.settings = settings
        self._client = client

    # -- HTTP -------------------------------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": self.settings.http_user_agent},
                follow_redirects=True,
                trust_env=self.settings.http_trust_env,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # -- contract ---------------------------------------------------------------------------
    @abstractmethod
    def healthcheck(self) -> SourceHealth: ...

    @abstractmethod
    def fetch(self) -> RawFetchResult: ...

    @abstractmethod
    def normalize(self, raw: RawFetchResult) -> NormalizationResult: ...


class ShellAdapter(SourceAdapter):
    """Honest placeholder: reports MANUAL/UNAVAILABLE, never fabricates data.

    If ``archive_page: true`` is set for the source, ``fetch()`` archives the landing page
    (byte-exact) without extracting values; ``normalize()`` returns nothing.
    """

    parser_version = "shell/0.1"
    shell_status: SourceStatus = SourceStatus.UNAVAILABLE
    reason: str = "automatic retrieval not implemented: no verified public machine-readable endpoint"

    def healthcheck(self) -> SourceHealth:
        if self.cfg.archive_page and self.cfg.base_url:
            try:
                r = self.client.head(self.cfg.base_url)
                ok = r.status_code < 400
                return SourceHealth(
                    status=SourceStatus.DEGRADED if ok else SourceStatus.UNAVAILABLE,
                    message=f"page-archive mode (no value extraction); HTTP {r.status_code}",
                    detail={"http_status": r.status_code},
                )
            except httpx.HTTPError as exc:
                return SourceHealth(status=SourceStatus.UNAVAILABLE, message=f"page unreachable: {exc!s}"[:500])
        return SourceHealth(status=self.shell_status, message=self.reason)

    def fetch(self) -> RawFetchResult:
        if not (self.cfg.archive_page and self.cfg.base_url):
            raise AdapterNotImplemented(self.reason)
        from bdo.schemas import RawPayload
        from bdo.util.time import utcnow

        retrieved_at = utcnow()
        r = self.client.get(self.cfg.base_url)
        ctype = r.headers.get("content-type", "application/octet-stream").split(";")[0]
        ext = "html" if "html" in ctype else "bin"
        return RawFetchResult(
            source_key=self.cfg.source_key, retrieved_at=retrieved_at,
            payloads=[RawPayload(content=r.content, content_type=ctype, label="landing-page", extension=ext,
                                 request_url=str(r.url), http_status=r.status_code,
                                 notes="page archive only; values not extracted")],
        )

    def normalize(self, raw: RawFetchResult) -> NormalizationResult:
        return NormalizationResult(records_seen=0)
