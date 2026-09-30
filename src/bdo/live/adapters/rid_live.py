"""RID live adapter — page-reachability check only; no structured public endpoint found.

RID's water-situation page (https://www.rid.go.th/th/water-situation) is a server-rendered Nuxt
app. Its bulletin figures are embedded directly in the HTML as a serialized Nuxt payload
(``<script id="__NUXT_DATA__">``, a compact array-referencing format, not a stable public JSON
API), and no separate ``/api/...`` route serving Chao Phraya / dam-discharge figures was found
during inspection (the two relative API routes that do exist, ``/api/cache/irrigation`` and
``/api/cache/reservoir``, are national crop/reservoir aggregates unrelated to the Bangkok /
Chao Phraya boundary condition this source is registered for — see docs/SOURCE_ENDPOINTS.md).

Per milestone spec item 5, RID is treated as slower upstream/boundary context and is polled no
faster than its configured (1800s) cadence; this module never fabricates a measurement from an
unparsed page. It only confirms the page is reachable, matching the existing ``ShellAdapter``
page-archive pattern used by the persisted ingestion pipeline (which stays MANUAL for RID).
"""

from __future__ import annotations

import httpx

from bdo.config import Settings
from bdo.enums import FreshnessLabel
from bdo.live.base import LiveHealth, LiveSourceState, make_client, unavailable_state
from bdo.util.time import utcnow

SOURCE_KEY = "rid_water_situation"
URL = "https://www.rid.go.th/th/water-situation"


def fetch(settings: Settings, client: httpx.Client | None = None) -> LiveSourceState:
    started = utcnow()
    owns_client = client is None
    client = client or make_client(settings)
    try:
        try:
            r = client.get(URL)
        except httpx.HTTPError as exc:
            return unavailable_state(SOURCE_KEY, URL, started, str(exc))
    finally:
        if owns_client:
            client.close()

    ok = r.status_code < 400
    return LiveSourceState(
        source_key=SOURCE_KEY, endpoint=URL, fetch_started_at=started, fetch_finished_at=utcnow(),
        http_status=r.status_code, health=LiveHealth.DEGRADED if ok else LiveHealth.UNAVAILABLE,
        record_count=0, source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN,
        error=None if ok else f"HTTP {r.status_code}",
        context={"note": "page reachable but not parsed in v0.2 — no verified structured endpoint "
                          "for Chao Phraya / dam-discharge figures; MANUAL transcription remains "
                          "the source of truth for persisted data (see data/manual/rid_water_situation/)"},
    )
