# Public Alpha Go / No-Go Checklist

Bangkok Drainability Observatory — POUND, Project 001. Run through this immediately before and
after deployment. Every box must be checked before the link is shared publicly.

## Safety

- [ ] Overview's **Current verified state** section never shows SEED/DEMONSTRATION or stale data
      as current; with only packaged SEED data loaded it shows the exact
      "No verified live measurement is currently available…" message.
- [ ] `tests/test_public_alpha.py` passes (SEED never current; stale/unknown never LIVE; current
      state keyed on `measurement_at` not `retrieved_at`).
- [ ] Public Alpha notice ("independent research observatory, not an official flood-warning…")
      is visible on **Overview** and **Map**.
- [ ] Help Now panel (official links + "does not dispatch assistance" line) is visible near the
      top of Overview.
- [ ] No page infers emergency need, safety, or passability from sensor values — confirm nothing
      new was added to `analytics/` or the UI that does this.
- [ ] No hydraulic prediction, flood forecast, or routing recommendation exists anywhere in the
      app (Research page still says "computes nothing" and the test for that still passes).

## Branding / attribution

- [ ] Sidebar and Overview show "POUND — Detector Technologies · Project 001 · Bangkok
      Drainability Observatory · Public Alpha".
- [ ] "POUND Global →" link points to `https://pound-global-website.vercel.app/`.
- [ ] Footer copyright + non-affiliation line appears on every page.
- [ ] Branding is restrained: public-interest function still reads as primary, not a marketing
      page.

## Public read-only mode

- [ ] `PUBLIC_DEPLOYMENT=true` is set in the deployment's secrets/environment.
- [ ] Data Quality → "Raw archive integrity" shows the disabled notice, not the verify button.
- [ ] No table shows a real filesystem path (`raw_payload` / `payload_path` columns are blank).
- [ ] Ingestion (`bdo ingest`) and field-observation import (`bdo import-observations` /
      `import_csv`) raise `PublicDeploymentBlocked` when pointed at a `PUBLIC_DEPLOYMENT=true`
      settings object.
- [ ] No stack trace, file path, or environment variable is visible after forcing an error in the
      browser (`client.showErrorDetails = "none"` in `.streamlit/config.toml`).

## Tests and pages

- [ ] Full test suite passes: `pytest` (55+ tests).
- [ ] `streamlit run app.py` starts locally with no exception in the log.
- [ ] All six pages render with no exception: Overview, Map, Stations, Event Archive, Research,
      Data Quality.

## Deployment readiness

- [ ] `app.py` is the configured entrypoint on Streamlit Community Cloud.
- [ ] `requirements.txt` and `runtime.txt` are present at the repo root and committed.
- [ ] `data/manual/*/SEED_*.csv` are committed; `data/*.sqlite` is **not** committed.
- [ ] Fresh-container smoke test done (see `docs/DEPLOY_STREAMLIT.md` §6) against the actual
      deployed URL, not just localhost.
- [ ] Rollback path (§7 of the deploy doc) is understood by whoever is on call tonight.

## Go / No-Go

All boxes above checked → **GO**. Any box unchecked → **NO-GO**; fix or explicitly accept and
record the residual risk before sharing the link.
