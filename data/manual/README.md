# data/manual — human transcriptions and field observations

* `<source_key>/*.csv` — manual measurement transcriptions for a registered source, ingested with
  `python scripts/ingest.py --source <source_key> --manual`. Columns: see
  `src/bdo/ingestion/adapters/manual_csv.py`. Lines beginning with `#` are provenance comments.
* Files named `SEED_*` are the v0.1 demonstration records (historical; never current conditions).
* `field_observations_TEMPLATE.csv` — header for field-observation CSVs, imported with
  `python scripts/import_field_observations.py <file.csv>`.

Every file is archived byte-exact under `data/raw/<source_key>/...` when ingested. Editing a file
after ingestion creates a new snapshot on the next ingest; the earlier one is never overwritten.
