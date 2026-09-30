# Test fixtures

`ckan_*_records.json` contain **verbatim records** returned by the CKAN `datastore_search` API on
2026-09-30 (via data.go.th, which harvests BMA datasets under the same resource IDs):

| file | resource_id | records |
|---|---|---|
| ckan_dds011_records.json | 313cd96f-8610-495d-875e-f6d0ec08a3bc | `_id` 1–5 and 11–22 (only these were inspected) |
| ckan_telemetry_records.json | 638f3adb-2fca-4767-b8f0-7528035ce319 | `_id` 1–5 |
| ckan_frd_records.json | 95716a53-4544-4c9b-af07-63298ace7c07 | `_id` 1–3 |

They are used only for offline tests of the CKAN adapter. `fields` for telemetry/frd are
abbreviated (types omitted). They are not loaded into the observatory database.
