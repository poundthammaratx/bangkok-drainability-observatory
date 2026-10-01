"""The stable contract an external scheduler relies on. See docs/COLLECTOR_ARCHITECTURE.md.

Collectors are scheduler-agnostic by design (milestone §12): nothing in ``bdo.collector`` assumes
it is running inside Streamlit, a specific cron daemon, or any particular container orchestrator.
This module is the one piece of surface external tooling (GitHub Actions, a managed cron service,
a bare server crontab) should call, kept separate from ``bdo.cli`` (the Typer CLI, whose argument
parsing is free to evolve) so that contract stays stable across CLI changes.

Contract:

* ``run(source_key=None)`` performs exactly one collection pass and returns a process exit code —
  ``0`` if every attempted source ended ``SUCCESS``, ``1`` otherwise. It never raises for a
  source-side failure (network error, stale data, parse error): those are recorded as a FAILED/
  PARTIAL run and reflected only in the exit code and printed summary. It *does* raise for a
  genuine misconfiguration (unknown ``source_key``, VIEWER role, database unreachable) — a
  scheduler should treat that as a crash, not a routine "source offline" signal.
* Idempotent and safe to retry: a collection run that duplicates a previous one inserts no
  duplicate measurements (see docs/PERSISTENT_ARCHIVE.md §Deduplication) and creates a fresh
  ``IngestRun`` + ``SourceHealthHistory`` row either way — re-running after a timeout or a
  scheduler-side retry is always safe.
* No state is kept between invocations beyond the database itself: every call is a fresh process,
  fresh settings, fresh engine. There is nothing to warm up or tear down.
* Cadence is the scheduler's responsibility, not this module's — see docs/SOURCE_CADENCE.md for
  the recommended interval per source and ``Settings.live_ttl_for`` for the matching read-through
  cache TTL.

Example (a plain cron line, a container command, or a GitHub Actions ``run:`` step)::

    python -m bdo.collector.scheduler_contract --source thaiwater_bangkok
    python -m bdo.collector.scheduler_contract --all
"""

from __future__ import annotations

import argparse
import sys

from bdo.collector.runner import collect_all, collect_one
from bdo.config import load_settings
from bdo.enums import IngestStatus


def run(source_key: str | None = None) -> int:
    settings = load_settings()
    results = collect_all(settings) if source_key is None else [collect_one(settings, source_key)]
    for r in results:
        print(r.line())
    return 0 if all(r.status in (IngestStatus.SUCCESS, IngestStatus.PARTIAL) for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="a single source_key to collect")
    group.add_argument("--all", action="store_true", help="collect every live-adapter source")
    args = parser.parse_args()
    sys.exit(run(None if args.all else args.source))


if __name__ == "__main__":
    main()
