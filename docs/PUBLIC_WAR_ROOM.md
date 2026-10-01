# Live Situation — Public War Room / Public Situation View (v0.3)

`src/bdo/ui/live_situation.py`, navigation entry "Live Situation" (`🛰️`). Additive to the existing
pages (Overview, Map, Stations, Event Archive, Research, Data Quality) — none of them were
restructured or removed to make room for it.

## What it is, and what it explicitly is not

It is a **public observability interface**: what the system currently knows, what has recently
changed, how fresh the observations are, where monitoring coverage exists, and where it doesn't.

It is **not** an operational control room, a flood forecast, a safety assessment, or a route
recommendation. The page's own banner says so on every load. "War Room" is the milestone's working
name for the feature; `live_situation.py` and its UI copy consistently use "Public Situation View"
as the user-facing term, reserving "war room" for this document and code comments.

## How Live Situation differs from Map

Both pages render the same canonical points (`bdo.ui.map_view.build_points()` /
`build_points_offline()`) and the same resolved observations
(`bdo.analytics.current_state.resolve_public_current_state()`) — **the same observation, same
answer, every time** (milestone §17A0). The difference is framing and audience:

| | Map | Live Situation |
|---|---|---|
| Audience | Analysts/researchers (existing v0.1/v0.2 page, unchanged) | General public |
| Filters | Full set: source, node type, freshness, status, district, 4 show/hide toggles | A smaller, simplified default set |
| Surrounding context | None beyond the page itself | Zones 1/3/4/5 below — summary, trends, confidence, events |
| Colour | By node type | By status category |

## The five zones

1. **Bangkok Now** — freshest measurement, its age, active-source count, reporting-station count,
   stale/unknown counts, and (when FloodBangkok is healthy) its own device-status counts —
   explicitly labelled with FloodBangkok's own measurement time, never merged into one city-wide
   number with any other source. A cross-source time-alignment table (milestone §17D) shows every
   source's latest measurement and age side by side, with a warning when the spread exceeds an
   hour, so a viewer can tell whether an apparent pattern is one synchronized snapshot or isn't.
2. **Live Bangkok Map** — the canonical map, coloured by status (`normal` / `malfunction` /
   `flooding` / `minor_flood` / a freshness label / `static`), never by a safety judgement.
3. **Recent Hydrological Trends** — 1h/3h/6h/12h/24h window selector, reading persisted history
   only (hidden with an explanation when the archive is unavailable). Trend labels are
   `RISING SINCE PREVIOUS OBSERVATION` / `FALLING...` / `UNCHANGED` / `INSUFFICIENT DATA`
   (`bdo.analytics.trends.descriptive_trend_label`), computed **only** from the two most recent
   observations sharing the same source, station, variable *and unit* — grouped by
   `(source_key, station, variable, unit)` before any comparison happens, so a rain-gauge reading
   is never compared against a water-level reading, and a metre reading never against a centimetre
   one. No smoothing, no model. If the earliest persisted measurement for these sources is newer
   than the selected window's start, the page says so explicitly ("Archival coverage is currently
   shorter than the selected window") instead of pretending the window is fully covered.
4. **Observability Coverage** — per-source health, latest measurement, age, last retrieval, HTTP
   status, record/stale/failed-sensor counts, and a short known-limitation note (the same
   empirical-timestamp caveats documented in `docs/SOURCE_ENDPOINTS.md`). An aggregate panel counts
   nodes with recent observations / stale / unknown / static-only. **This measures data
   availability, not physical flood coverage** — the page says this explicitly, and the panel is
   titled "Observability Coverage," never "Flood Coverage."
5. **Recent Changes** — a chronological feed from `bdo.analytics.events.recent_events()`: source
   health transitions and FloodBangkok device-status transitions, both derived strictly from two
   consecutive, already-persisted, already-timestamped records disagreeing. No causal explanation
   is generated for *why* something changed — only *that* it did, and when.

A "What do these terms mean?" expander (milestone §17E) gives the plain-language definitions of
STALE, UNKNOWN, NORMAL AT SENSOR, and OBSERVABILITY COVERAGE verbatim from the milestone brief.

## Vocabulary (milestone §17B)

The page never uses SAFE/UNSAFE as a status label (`tests/test_live_situation.py::test_no_safe_unsafe_status_labels`
checks the actual status categories and metric labels, not page prose — the disclaimer text itself
legitimately contains the word "safe" in sentences that *deny* a safety inference, e.g. "does not
guarantee ... is safe or dry," which is required copy, not a violation). Status vocabulary used
instead: `normal` (FloodBangkok's own term, shown as "device_status=normal"), `flooding`,
`minor_flood`, `malfunction`, or a freshness label (`LIVE`/`RECENT`/`STALE`/`VERY_STALE`/`UNKNOWN`)
when no discrete status concept exists for that source.

## Fallback behaviour (milestone §17H, §18)

| Archive | Live sources | Result |
|---|---|---|
| reachable | reachable | Full page: all 5 zones, resolver merges persisted + live. |
| reachable | some/all unavailable | Zones 1/2/4/5 show whatever is available, with "Live overlay unavailable this cycle: ..." noted; Zone 3 trends still work (archive-only). |
| **unreachable** | reachable | "Persistent archive unavailable — live read-through only" banner; Zones 1/2 use `live_only_observations()` + `build_points_offline()` (no DB access at all); Zones 3/5 (archive-dependent) are replaced with an explanatory message instead of attempted. |
| unreachable | all unavailable | Same as above, plus Zone 2 falls back further to the packaged static reference topology alone (`bdo.repository.reference`, file-based, no network and no database). |

Verified by `tests/test_live_situation.py` (`test_renders_with_archive_unavailable`,
`test_renders_with_all_live_sources_failed`) and manually against a deliberately-unreachable
`postgresql+psycopg://` URL (see docs/DATABASE_DEPLOYMENT.md's verification notes). SEED/DEMO data
is never substituted in any of these fallback paths — `resolve_public_current_state()` excludes it
unconditionally, archive-reachable or not.

## Performance (milestone §17G)

All live data behind every zone comes from `bdo.live.manager`'s process-wide TTL cache — one HTTP
request per source per TTL window, however many widgets or filters the viewer touches. The map and
trend table build from one `resolve_public_current_state()` / `current_observations()` call each,
not one query per chart or per marker. At the point counts observed during development
(~750–860 geographic nodes with both static and live layers populated), rendering stayed
responsive in manual testing.
