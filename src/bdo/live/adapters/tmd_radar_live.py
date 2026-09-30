"""TMD radar live adapter — a product-availability check, not a value extraction.

Two Bangkok-area radar stations are operated by BMA under TMD's composite service (found by
crawling https://weather.tmd.go.th/THA_Z.php on 2026-09-30 — no separate API exists):

* Nong Chok  (page ``bma_nck.php``  → image ``https://weather.tmd.go.th/pic_bmanck.jpg``)
* Nong Khaem (page ``bma_nkm.php``  → image ``https://weather.tmd.go.th/pic_bmankm.jpg``)

Both confirmed reachable on inspection. TMD's own (currently HTML-commented-out) page disclaimer
states displayed times are UTC, but neither an HTTP ``Last-Modified`` header nor an in-page
product timestamp was found — so no ``measurement_at`` can be derived. Health is therefore
UNKNOWN whenever the images are reachable, never HEALTHY: per the milestone's health rule, HTTP
200 alone never means healthy, and here there is no data timestamp to consider at all.

Per milestone spec (§C/§E.4): radar is treated as an image/product layer only in v0.2 — no
rainfall-intensity estimation from pixels is performed, and none is planned for this module.
"""

from __future__ import annotations

import httpx

from bdo.config import Settings
from bdo.enums import FreshnessLabel
from bdo.live.base import LiveHealth, LiveSourceState, make_client, unavailable_state
from bdo.util.time import utcnow

SOURCE_KEY = "tmd_bangkok_radar"
PRODUCTS = {
    "nong_chok": "https://weather.tmd.go.th/pic_bmanck.jpg",
    "nong_khaem": "https://weather.tmd.go.th/pic_bmankm.jpg",
}


def fetch(settings: Settings, client: httpx.Client | None = None) -> LiveSourceState:
    started = utcnow()
    owns_client = client is None
    client = client or make_client(settings)
    reachable: dict[str, bool] = {}
    last_status = None
    try:
        for name, url in PRODUCTS.items():
            try:
                r = client.head(url)
                if r.status_code == 405:  # some static hosts reject HEAD
                    r = client.get(url, headers={"Range": "bytes=0-0"})
                reachable[name] = r.status_code < 400
                last_status = r.status_code
            except httpx.HTTPError:
                reachable[name] = False
    finally:
        if owns_client:
            client.close()

    ok_count = sum(reachable.values())
    if ok_count == 0:
        return unavailable_state(SOURCE_KEY, PRODUCTS["nong_chok"], started, "no Bangkok radar product reachable")

    health = LiveHealth.UNKNOWN if ok_count == len(PRODUCTS) else LiveHealth.DEGRADED
    return LiveSourceState(
        source_key=SOURCE_KEY, endpoint=PRODUCTS["nong_chok"], fetch_started_at=started,
        fetch_finished_at=utcnow(), http_status=last_status, health=health, record_count=ok_count,
        source_measurement_at=None, freshness=FreshnessLabel.UNKNOWN, error=None,
        context={
            "products": reachable,
            "note": "no machine-readable product timestamp is published by TMD for these images; "
                    "retrieval time is not measurement time and STATE UNKNOWN applies",
        },
    )
