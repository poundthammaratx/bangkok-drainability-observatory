# Deploying to Streamlit Community Cloud

Bangkok Drainability Observatory — POUND · Public Alpha

This is the exact procedure for tonight's public alpha deployment. It does not change the
architecture described in `docs/architecture.md`; it only prepares this repository for a
container that has no shell access and no guaranteed persistent filesystem.

## 0. What is different on Streamlit Community Cloud

* **No persistent runtime filesystem.** `data/bdo.sqlite` and anything else written after the
  container starts can disappear on the next reboot or redeploy. Do **not** treat it as durable
  archival — see the TODO at the end of this document.
* **No shell access.** You cannot run `python scripts/seed_sources.py` against the deployed
  instance. The app bootstraps its own baseline data at startup instead — see §3.
* Set `PUBLIC_DEPLOYMENT=true` (§4) so the UI hides admin/diagnostic controls and internal file
  paths, and so ingestion / field-observation import refuse to run against this instance.

## 1. Push to GitHub

```bash
git init                                   # if this checkout isn't a git repo yet
git add .
git commit -m "Public alpha deployment patch v0.1.1"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

Confirm `data/manual/*/SEED_*.csv` are tracked (they are **not** covered by `.gitignore`) and
`data/*.sqlite` is **not** tracked (it is — see `.gitignore`; it is a local research artifact, not
a deployment asset).

## 2. Create the Streamlit Community Cloud app

1. Go to <https://share.streamlit.io/> → **New app**.
2. Repository: your pushed repo. Branch: `main`.
3. **Main file path: `app.py`** (this is the entrypoint; do not point at a file under `src/`).
4. Python version: 3.12 (declared in `runtime.txt`; also selectable in the Cloud UI's "Advanced
   settings").
5. Dependencies: Cloud installs from `requirements.txt` at the repo root automatically (kept in
   sync with `pyproject.toml`).

## 3. Startup behaviour (no manual seeding step)

`bdo.ui.components.get_settings_cached()` calls `bdo.bootstrap.ensure_seeded()` on first load,
which:

1. creates the SQLite schema (idempotent `CREATE TABLE IF NOT EXISTS`);
2. registers the 7 sources from `config/sources.yaml`, **only if the database has none yet**;
3. loads the packaged `data/manual/*/SEED_*.csv` demonstration records — nothing else, and never
   over the network.

So a fresh container becomes usable immediately. Nothing to run by hand.

## 4. Set secrets / environment (public/private setting)

In the app's **Settings → Secrets**, add:

```toml
PUBLIC_DEPLOYMENT = "true"
```

(Streamlit Cloud injects TOML secrets as environment variables, which `bdo.config.load_settings`
reads directly — no code change needed.) Leave `BDO_DATABASE_URL`, `BDO_DATA_DIR` and
`BDO_CONFIG_DIR` unset so the app uses its packaged repo-relative defaults.

Set the app's visibility to **Public** (this is a public-interest alpha; do not deploy it private
and then link it publicly, since data provenance and the read-only guarantees depend on
`PUBLIC_DEPLOYMENT=true` being active on whatever is reachable).

## 5. Deploy

Click **Deploy**. First build typically takes 2–5 minutes (installing `requirements.txt`).

## 6. Post-deploy checks

Open the deployed URL and confirm, in order:

1. **Overview** — POUND header renders; Public Alpha notice and Help Now panel are visible near
   the top; "Current verified state" shows either genuine LIVE cards or the exact
   "No verified live measurement is currently available…" message (it will show the latter on a
   fresh deploy, because only historical SEED data is packaged — this is correct, not a bug).
2. **Map** — Public Alpha notice visible; no exception.
3. **Stations**, **Event Archive**, **Research**, **Data Quality** — all load without exception.
4. **Data Quality** — "Raw archive integrity" shows the disabled notice, not the verify button
   (confirms `PUBLIC_DEPLOYMENT=true` took effect).
5. Footer shows the POUND copyright line and the Data Sources / Methodology / Limitations
   pop-overs on every page.
6. Open browser dev tools → trigger an error path (e.g. malformed URL query) if convenient, and
   confirm no stack trace or file path is shown (governed by `.streamlit/config.toml`
   `client.showErrorDetails = "none"`).
7. `curl -I <url>` returns `200`.

## 7. Rollback

Streamlit Community Cloud keeps the previous deploy's image until the next successful build.

* **Fast rollback:** in the app dashboard, use **Reboot app** after reverting the branch tip
  (`git revert <bad-commit>` and push), or point the app at a known-good commit/tag via
  **Settings → Advanced → Branch**.
* **Full stop:** **Settings → Delete app** removes the public deployment immediately if a safety
  issue is found; the GitHub repository and local data are untouched.
* Because the runtime filesystem is not durable, rollback never needs to restore data — the next
  boot re-bootstraps from the packaged SEED files (§3).

## TODO — durable archival

`PUBLIC_DEPLOYMENT` mode explicitly does not promise persistent storage: any measurements
ingested against a deployed instance's SQLite file can be lost on redeploy. Before relying on the
Cloud deployment for anything beyond the packaged historical baseline, migrate `database_url` to
an external PostgreSQL/PostGIS instance (the migration path already assumed by
`bdo.config.Settings.database_url`, e.g. `postgresql+psycopg://...`) and re-point
`BDO_DATABASE_URL` via a Cloud secret. Tonight's alpha does not attempt this.
