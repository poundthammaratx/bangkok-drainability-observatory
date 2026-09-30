"""Recovery-dynamics placeholders (Phase 4). Deliberately not implemented in v0.1.

Planned quantities (see docs/research_scope.md):

* ``T_recover``  — time from peak ponding/water level to return below a reference state.
* ``V_residual`` — residual stored volume after a defined interval.
* ``R_drain``    — effective drainage rate of a catchment/sub-system.
* ``dh_dt``      — validated rate of change of a state variable.

These require validated station datums, event segmentation rules, catchment geometry and
authority review. Returning a number before that would be an unsupported operational signal,
so every function raises ``NotValidatedError``.
"""

from __future__ import annotations

METHOD_STATUS = "NOT_VALIDATED"
PLANNED_METRICS = {
    "T_recover": "Recovery time from event peak to reference state [h]",
    "V_residual": "Residual stored volume after interval [m^3]",
    "R_drain": "Effective drainage rate [m^3/s or mm/h]",
    "dh_dt": "Validated rate of change of a state variable [m/h]",
}


class NotValidatedError(RuntimeError):
    """Raised when a derived hydraulic metric is requested before validation."""


def _refuse(name: str):
    raise NotValidatedError(f"{name} is not implemented: derived hydraulic metrics are not yet validated.")


def t_recover(*_, **__):
    _refuse("T_recover")


def v_residual(*_, **__):
    _refuse("V_residual")


def r_drain(*_, **__):
    _refuse("R_drain")


def dh_dt(*_, **__):
    _refuse("dh_dt")
