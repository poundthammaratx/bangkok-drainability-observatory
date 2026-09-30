"""Headless render of every Streamlit page (seed data + offline CKAN load, so the map has coordinates)."""

import pytest
from streamlit.testing.v1 import AppTest

from bdo.ingestion.runner import run_source
from tests_helpers import ckan_adapter

PAGES = ["overview", "map_view", "stations", "archive", "research", "data_quality"]


@pytest.fixture()
def ui_env(seeded, settings, monkeypatch):
    run_source(settings, "bkk_open_data_dds", adapter=ckan_adapter(settings))
    monkeypatch.setenv("BDO_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("BDO_DATA_DIR", str(settings.data_dir))
    from bdo.ui import components
    components.get_settings_cached.clear()
    yield
    components.get_settings_cached.clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(ui_env, page):
    at = AppTest.from_string(f"from bdo.ui import {page}\n{page}.render()", default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    warnings = " ".join(w.value for w in at.warning)
    assert "not an official flood-warning service" in warnings


def test_overview_labels_seed_as_demonstration(ui_env):
    at = AppTest.from_string("from bdo.ui import overview\noverview.render()", default_timeout=60)
    at.run()
    df = at.dataframe[1].value  # latest-per-series table
    seed_rows = df[df["source_key"].isin(["rid_water_situation", "bma_floodbangkok"])]
    assert len(seed_rows) == 13
    assert set(seed_rows["demo"]) == {"SEED/DEMO"}
    assert not (seed_rows["freshness"] == "🟢 LIVE").any()
    assert {"measured", "retrieved", "age_at_retrieval"} <= set(df.columns)


def test_research_page_computes_nothing(ui_env):
    at = AppTest.from_string("from bdo.ui import research\nresearch.render()", default_timeout=60)
    at.run()
    assert any("Derived hydraulic metrics are not yet validated." in e.value for e in at.error)
