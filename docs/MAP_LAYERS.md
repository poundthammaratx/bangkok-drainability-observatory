# Map Layers (v0.2)

`bdo.ui.map_view` merges three independent point sources into one table (`build_points()`) before
plotting. See `docs/LIVE_DATA_ARCHITECTURE.md` for the static/live architectural split this
implements.

## Point sources

| `point_source` value | Where it comes from | Present with an empty database? | Present offline (no network)? |
|---|---|---|---|
| `bkk_open_data_dds` (as DB rows) | `stations` table, if ingestion has run locally | No | Yes (it's already local) |
| `bkk_open_data_dds` (as reference rows) | `data/reference/*.csv`, loaded whenever a DB row for the same `external_station_id` doesn't exist | **Yes** | **Yes** (no network needed) |
| `bma_floodbangkok` | `bdo.live.adapters.floodbangkok_live`, read-through | Yes, if the live fetch succeeds | No |
| `thaiwater_bangkok` | `bdo.live.adapters.thaiwater_live`, read-through | Yes, if the live fetch succeeds | No |
| `research_field_observations` | `field_observations` table (unchanged from v0.1) | No | Yes (it's already local) |

A DB station and a reference-CSV station with the same `external_station_id` are never both
plotted — the DB row (which may carry an actual ingested measurement) wins; see `_db_points()` /
`_reference_points(exclude_ids=...)`.

**This is what makes the map render on a fresh, empty, `PUBLIC_DEPLOYMENT=true` database**: with
zero rows in `stations`, `_db_points()` returns nothing, but `_reference_points()` still supplies
the full packaged BKK topology (verified: 582 coordinate-bearing points), and the two live
adapters add FloodBangkok's 254 and ThaiWater's ~24 points on top when reachable. Verified offline
(all live adapters forced to raise): the map still renders exactly the 582 static reference points.

## Layers (`layer` = `node_type`)

Colour encodes `layer` only — **never** a safety judgement, per milestone instruction. The legend
shows plain node-type names (`water_level_station`, `rain_gauge`, `road_flood_sensor`, `other`,
`road_node`, `field_observation`, …); none of them are labelled or coloured as "safe" or "danger".

| `node_type` | Populated by |
|---|---|
| `water_level_station` | BKK telemetry registry (static/DB) + ThaiWater live |
| `rain_gauge` | BKK telemetry registry (static/DB) + ThaiWater live |
| `road_flood_sensor` | FloodBangkok live (254 stations; not in the BKK CKAN registry at all) |
| `river_boundary` | BKK dds011 fixed station (rarely has coordinates published) |
| `other` | BKK telemetry registry, unmapped station-code prefixes |
| `field_observation` | Research team field observations (v0.1, unchanged) |
| `pump_station` / `gate` / `tunnel` / `retention` | Modelled in `bdo.enums.NodeType` but not yet populated by any adapter with coordinates — see `docs/SOURCE_ENDPOINTS.md` §1 (`dds_drainage_tunnels` has no published coordinates) |

**Nodes without a published latitude/longitude are never plotted**, regardless of layer — this
holds for both the static reference registry (213 of 795 packaged nodes have no coordinates) and
any live source.

## Hover fields

Every point's hover card shows, per milestone item C: `name`, `layer` (node type),
`point_source` (operator/network), `hover_state` (latest reported value, or the literal string
`"STATE UNKNOWN / NO RECENT MEASUREMENT"` when none is available), `measured` (source measurement
time — never retrieval time), `retrieved`, `age_now`, `freshness` (icon + label), `evidence_class`,
and `status`.

`status` is one of: a FloodBangkok `device_status` value (`normal` / `malfunction` / `flooding` /
`minor_flood`), a freshness label (`LIVE`/`RECENT`/`STALE`/`VERY_STALE`) for sources that only
report a value, `"static"` for a reference-only node with no live/DB value attached, or
`"observed"` for a field observation.

## Filters and toggles (milestone item J)

Multiselects: **Source**, **Node type**, **Freshness**, **Status**, **District** (when published).
Checkboxes, all **on by default** ("default should not hide uncertainty"): *Show stale*, *Show
failed sensors* (FloodBangkok `malfunction`), *Show unknown*, *Show static-only nodes*.

## Basemap fallback (milestone item C)

Three `map_style` choices are offered: `carto-positron` (default, needs internet in the *viewer's
browser*), `open-street-map`, and `white-bg` — a tile-free coordinate view that needs no external
resource at all. `BDO_MAP_STYLE` sets the default; the selector lets a viewer switch to `white-bg`
immediately if the tile layer fails to load, without reloading the page. Point rendering itself
(Plotly `scatter_map`) never depends on the tile layer succeeding.

## Performance (milestone item K)

All live data behind the map is served through `bdo.live.manager`'s process-wide TTL cache — one
HTTP request per source per TTL window, however many times the Map page reruns from filter/widget
interaction. At ~860 points (582 static + 254 FloodBangkok + 24 ThaiWater, observed 2026-09-30),
`scatter_map` renders in a single Plotly trace per layer; no per-marker Python objects are built.
