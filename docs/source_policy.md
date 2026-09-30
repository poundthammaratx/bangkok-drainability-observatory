# Source policy

## 1. Preference order
1. Documented API or downloadable dataset (CKAN, official JSON/CSV).
2. Manual transcription by the research team, with the transcription file archived.
3. Byte-exact page archive **without** value extraction (`archive_page: true`), for later review.

Scraping rendered dashboards for values is not done in v0.1.

## 2. Adapter admission
An automatic adapter is enabled only after its endpoint has been inspected and the following are
documented in `sources.yaml` or the adapter docstring: resource identifiers, field meanings,
where the measurement time comes from, the time zone of naive timestamps, and units (or that
they are unverified). Until then the adapter is a shell reporting `MANUAL` or `UNAVAILABLE`.

## 3. Time
* A number on a page does **not** inherit the page retrieval time.
* Where one page holds tables with different observation times (e.g. RID bulletins), each table's
  time must be carried separately.
* Naive timestamps get a zone only from explicit config, and are flagged.

## 4. Evidence classes by source
| source_key | default class | note |
|---|---|---|
| bkk_open_data_dds | OFFICIAL_REPORTED | BMA publication; mirror host does not change the class |
| bma_floodbangkok | OFFICIAL_REPORTED | dashboard values as published |
| rid_water_situation | OFFICIAL_REPORTED | bulletin values |
| tmd_bangkok_radar | OFFICIAL_REPORTED | radar products (no derived rainfall in v0.1) |
| thaiwater_bangkok | OFFICIAL_REPORTED | HII aggregation of agency data |
| traffy_bangkok | THIRD_PARTY_REPORTED | citizen reports: corroboration only |
| research_field_observations | OBSERVED | team observations; photos referenced by `photo_ref` |

## 5. Etiquette
* Identify the client (`User-Agent` in `settings.yaml`).
* Page through CKAN with `limit`/`offset`; respect `max_records`.
* No high-frequency polling in v0.1; ingests are run manually.
* Respect each source's terms of use and licence; the CKAN `package_show` payload (licence,
  modification time) is archived alongside each dataset fetch.

## 6. Personal data
Citizen reports and field photos may contain personal data. Store only what the research needs,
reference photos by path rather than embedding them, and do not publish raw citizen payloads.
