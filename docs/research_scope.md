# Research scope

## Question
How does Bangkok's drainage system — surface, drains/sewers, canals and storage, pumps/gates/tunnels,
and the Chao Phraya boundary — recover after rainfall events, and how is that recovery constrained by
upstream river state?

## Conceptual model (systems / control view)

```
Rainfall → Surface runoff / ponding → Drain / sewer network → Canals / storage
        → Pumps / gates / tunnels → Chao Phraya / downstream boundary

Upstream context:
Bhumibol / Sirikit / tributaries → C.2 Nakhon Sawan → Chao Phraya Dam / C.13
        → Ayutthaya / Bang Sai → Bangkok
```

State variables are water levels / ponding depths at nodes; actuators are pumps and gates;
disturbances are rainfall and the river boundary.

## Phases

1. **Event & Data Reconstruction** (v0.1) — provenance-preserving archive; reconstruct events from
   official, third-party and field records with explicit timing and evidence class.
2. **Drainability Architecture** — build the node/edge graph (catchments, drains, canals, pumps,
   gates, tunnels, boundaries) with capacities where documented.
3. **Reduced-order Hydraulic Model** — storage–flow model on that graph; outputs labelled MODELLED.
4. **Recovery Dynamics** — define and estimate:
   * `T_recover` — time from peak to reference state;
   * `V_residual` — residual stored volume after an interval;
   * `R_drain` — effective drainage rate;
   * `dh/dt` — validated rate of change.
5. **Operational Scenario Analysis** — counterfactuals, clearly separated from observations.
6. **Expert / authority validation** — review with BMA DDS, RID and domain experts.

## Out of scope until Phase 6
Any operational recommendation, warning or forecast.

## Prerequisites for Phase 4 metrics
* Known vertical datum per station (not stated in current BKK resources).
* Event segmentation rule and reference state per node.
* Sampling cadence adequate for the process time-scale (daily maxima are not).
* Documented outlier and gap policy; no interpolation without an explicit flag.
