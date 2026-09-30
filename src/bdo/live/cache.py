"""A minimal in-process TTL cache for read-through live fetches.

Deliberately not ``st.cache_data``: this needs to work the same way under pytest (no Streamlit
runtime) as it does in the deployed app, and needs an explicit "last known good" retention path
for source-fallback (see ``bdo.live.manager`` and item I of the v0.2 milestone — a failed live
fetch shows the last successful result, marked DEGRADED/STALE, rather than nothing).

Not process-shared and not thread-safe beyond the GIL; fine for a single Streamlit worker process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    stored_at: float


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}

    def get_or_set(self, key: str, ttl_seconds: float, factory: Callable[[], T]) -> T:
        """Return the cached value if younger than ``ttl_seconds``; otherwise call ``factory()``.

        If ``factory()`` raises, the stale cached value (if any) is re-raised as unavailable to the
        caller via re-raising the exception — callers that want graceful fallback should catch
        inside ``factory`` itself and return a DEGRADED/STALE state object instead of raising (this
        is what every live adapter does; see ``bdo.live.manager.get_live_state``).
        """
        now = time.monotonic()
        entry = self._store.get(key)
        if entry is not None and (now - entry.stored_at) < ttl_seconds:
            return entry.value
        value = factory()
        self._store[key] = _Entry(value=value, stored_at=now)
        return value

    def set(self, key: str, value: T) -> None:
        self._store[key] = _Entry(value=value, stored_at=time.monotonic())

    def last_known(self, key: str) -> T | None:
        entry = self._store.get(key)
        return entry.value if entry is not None else None

    def age_seconds(self, key: str) -> float | None:
        entry = self._store.get(key)
        return None if entry is None else time.monotonic() - entry.stored_at

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)
