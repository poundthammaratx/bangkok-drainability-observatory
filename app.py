"""Bangkok Drainability Observatory — Streamlit entry point.

    streamlit run app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st  # noqa: E402

from bdo import NOT_A_WARNING_SERVICE  # noqa: E402
from bdo.ui import archive, data_quality, map_view, overview, research, stations  # noqa: E402
from bdo.ui.components import fmt, footer, get_settings_cached, now_utc  # noqa: E402

st.set_page_config(page_title="Bangkok Drainability Observatory — POUND", page_icon="🌊", layout="wide")

settings = get_settings_cached()

pages = [
    st.Page(overview.render, title="Overview", icon="🧭", default=True),
    st.Page(map_view.render, title="Map", icon="🗺️", url_path="map"),
    st.Page(stations.render, title="Stations", icon="📈", url_path="stations"),
    st.Page(archive.render, title="Event Archive", icon="🗄️", url_path="archive"),
    st.Page(research.render, title="Research (dev)", icon="🧪", url_path="research"),
    st.Page(data_quality.render, title="Data Quality", icon="🔍", url_path="data-quality"),
]
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("**POUND — Detector Technologies**  \nProject 001  \nBangkok Drainability Observatory  \n"
                "Public Alpha")
    st.caption(f"v{settings.version} · research observatory")
    st.caption(NOT_A_WARNING_SERVICE)
    st.caption(f"Now: {fmt(now_utc())}  \nTimes shown in {settings.display_timezone}; stored as UTC.")

nav.run()
footer()
