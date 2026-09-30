"""Structured logging: timestamp | level | source | run | stage | message.

Payloads are never logged; they are archived under data/raw.
"""

from __future__ import annotations

import logging
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-7s | source=%(source)s | run=%(run_id)s | stage=%(stage)s | %(message)s"
_configured = False


class _ContextDefaults(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for attr in ("source", "run_id", "stage"):
            if not hasattr(record, attr):
                setattr(record, attr, "-")
        return True


def configure_logging(level: str = "INFO", log_file: str | Path | None = None) -> None:
    global _configured
    root = logging.getLogger("bdo")
    root.setLevel(level.upper())
    if _configured:
        return
    fmt = logging.Formatter(_FMT, datefmt="%Y-%m-%dT%H:%M:%S%z")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_ContextDefaults())
        root.addHandler(h)
    root.propagate = False
    _configured = True


class StageLogger(logging.LoggerAdapter):
    """Logger bound to a source and ingest run: ``log.info("ok", stage="fetch")``."""

    def process(self, msg, kwargs):
        extra = dict(self.extra)
        stage = kwargs.pop("stage", None)
        if stage is not None:
            extra["stage"] = stage
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str = "bdo", source: str = "-", run_id: int | str = "-") -> StageLogger:
    return StageLogger(logging.getLogger(name), {"source": source, "run_id": run_id, "stage": "-"})
