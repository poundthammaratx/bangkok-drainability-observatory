# Data model

All timestamps are timezone-aware UTC in storage (`UTCDateTime`), presented in Asia/Bangkok (ICT).
Enums are stored as their literal names with CHECK constraints.

## Source (`sources`)
`id, source_key (unique), agency, name, base_url, data_domain, access_mode, typical_freshness_minutes,
priority, status {ACTIVE|MANUAL|UNAVAILABLE|DEGRADED|UNKNOWN}, default_evidence_class, adapter, notes,
last_healthcheck_at, last_health_message, created_at, updated_at`

## Station / SystemNode (`stations`)
`id, source_id, external_station_id (unique per source), name, node_type, latitude, longitude,
district, catchment, operator, state_variable, actuator_or_capacity, upstream_nodes_text,
downstream_nodes_text, validation_status {UNVERIFIED|TYPE_INFERRED|VERIFIED_DOCUMENT|VERIFIED_FIELD|DEPRECATED},
source_type_code (verbatim source classification), coordinate_evidence_class, raw_snapshot_id, notes,
created_at, updated_at`

Node types: `rain_gauge, water_level_station, river_gauge, road_flood_sensor, road_flood_sensor_network,
canal, drain, sewer, pump_station, gate, retention, dam, barrage, tunnel, river_boundary,
surface_low_point, road_node, transit_node, other`. Coordinates are only stored when a source
publishes them; their evidence class is recorded separately.

## RawSnapshot (`raw_snapshots`)
`id, source_id, retrieved_at, source_measurement_at, content_type, payload_path (relative to data dir,
unique), payload_hash (SHA-256), payload_bytes, http_status, request_url, parser_version,
ingest_run_id, label, notes, created_at`

## Measurement (`measurements`)
`id, source_id, station_id, external_record_id, variable, value_num, value_text, unit,
measurement_at (nullable = UNKNOWN), retrieved_at, evidence_class (NOT NULL), quality_flag
(';'-joined), raw_snapshot_id, ingest_run_id, parser_version, notes, dedup_key (unique), created_at`

`dedup_key = sha256(source_id | station_id | variable | measurement_at_utc_iso | canonical_value
[| external_record_id if measurement_at is unknown])`. Canonical value: numbers at 10 significant
digits, text whitespace-normalised.

### Quality flags
| Flag | Meaning |
|---|---|
| `SEED_DEMONSTRATION` | historical demonstration record; never LIVE |
| `MANUAL_TRANSCRIPTION` | transcribed by a person |
| `AGGREGATE_COUNT` | network-level count, not a point measurement |
| `TZ_DECLARED_BY_CONFIG` | source timestamp was naive; zone from config |
| `UNIT_DECLARED_UNVERIFIED` | unit from config, not stated by source |
| `DATE_DM_AMBIGUOUS` | day ≤ 12: day/month could be transposed |
| `DATE_REPAIRED_DM_SWAP` | transposition repaired; as-published date in notes |
| `DATE_NON_MONOTONIC` | breaks expected source order; not repaired |
| `DATE_TIME_FIELD_CONFLICT` | separate time field disagrees with timestamp |
| `MEASUREMENT_TIME_UNKNOWN` | no measurement time |
| `FUTURE_TIMESTAMP` | measurement_at more than 10 min after retrieved_at |

## FieldObservation (`field_observations`)
`id, external_observation_id (unique; CSV observation_id or content hash), observed_at, location_name,
latitude, longitude, district, evidence_class, phenomenon, water_depth_cm, passability, trend,
photo_ref, notes, source_url, raw_snapshot_id, created_at`

## IngestRun (`ingest_runs`)
`id, source_id, mode {automatic|manual|seed}, started_at, finished_at, status
{RUNNING|SUCCESS|PARTIAL|FAILED|SKIPPED}, records_retrieved, records_inserted, records_skipped,
records_failed, stations_upserted, snapshots_saved, error_message, parser_version`

## DerivedMetric (`derived_metrics`) — placeholder, empty in v0.1
`id, metric_name, station_id, period_start, period_end, value, unit, method_version,
evidence_class (default MODELLED), input_provenance_json, created_at`

## Evidence classes
| Class | Use |
|---|---|
| `OBSERVED` | seen or measured directly by the research team |
| `OFFICIAL_REPORTED` | published by the responsible agency (BMA, RID, TMD, HII) |
| `THIRD_PARTY_REPORTED` | citizens (Traffy), media, other non-responsible parties |
| `MODELLED` | output of a model (Phase 3+) |
| `ASSUMED` | researcher assumption or placeholder |
