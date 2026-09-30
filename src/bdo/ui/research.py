"""Research page (development) — explains planned quantities; computes nothing."""

from __future__ import annotations

import streamlit as st

from bdo.analytics.recovery import PLANNED_METRICS
from bdo.ui import components as c

CONCEPT = """
Rainfall
  ↓
Surface runoff / ponding
  ↓
Drain / sewer network
  ↓
Canals / storage
  ↓
Pumps / gates / tunnels
  ↓
Chao Phraya / downstream boundary

Upstream context:
Bhumibol / Sirikit / tributaries → C.2 Nakhon Sawan → Chao Phraya Dam / C.13 → Ayutthaya / Bang Sai → Bangkok
"""


def render() -> None:
    st.title("Research (development)")
    st.error("**Derived hydraulic metrics are not yet validated.** Nothing on this page is computed from data, "
             "and nothing here is an operational recommendation.", icon="🧪")
    c.banner()

    st.subheader("Conceptual system model")
    st.code(CONCEPT, language=None)

    st.subheader("Planned quantities (Phase 3–4)")
    st.markdown("""
| Quantity | Meaning | Minimum prerequisites before it may be computed |
|---|---|---|
| **T_recover** | Time from event peak (ponding depth or water level) back to a defined reference state | Event segmentation rule; per-node reference state; continuous records at event cadence |
| **V_residual** | Stored volume remaining after a defined interval | Stage–storage relation for the catchment/retention; validated datum |
| **R_drain** | Effective drainage rate of a node or sub-catchment | Inflow estimate (rainfall + upstream), outflow (pump/gate records), storage change |
| **dh/dt** | Rate of change of a state variable | Known vertical datum; sampling cadence well below process time-scale; outlier policy |
""")
    st.caption("Schema placeholder: table `derived_metrics` (metric_name, station_id, period_start, period_end, value, "
               "unit, method_version, evidence_class=MODELLED, input_provenance_json). It is empty in v0.1 by design.")
    st.json({k: {"description": v, "status": "NOT_VALIDATED"} for k, v in PLANNED_METRICS.items()})

    st.subheader("Research roadmap")
    st.markdown("""
1. **Event & Data Reconstruction** — this milestone: provenance-preserving archive, event reconstruction.
2. **Drainability Architecture** — system-node graph: catchments, drains, canals, pumps, gates, tunnels, boundaries.
3. **Reduced-order Hydraulic Model** — lumped/semi-distributed storage–flow model on that graph.
4. **Recovery Dynamics** — T_recover, V_residual, R_drain, dh/dt with stated uncertainty.
5. **Operational Scenario Analysis** — counterfactual scenarios, explicitly labelled MODELLED.
6. **Expert / authority validation** — review with BMA DDS, RID and domain experts before any operational use.
""")
