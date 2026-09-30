"""Trend utilities (v0.1: descriptive only, on actual records — no interpolation, no smoothing)."""

from __future__ import annotations

import pandas as pd


def finite_differences(df: pd.DataFrame, time_col: str = "measurement_at", value_col: str = "value_num") -> pd.DataFrame:
    """Backward finite differences between consecutive *actual* records.

    Returns columns: dt_hours, dvalue, rate_per_hour. Rows with unknown time are dropped, not
    filled. This is a descriptive diagnostic, not the validated dh/dt metric of Phase 4.
    """
    d = df.dropna(subset=[time_col, value_col]).sort_values(time_col).copy()
    if d.empty:
        return d.assign(dt_hours=pd.Series(dtype=float), dvalue=pd.Series(dtype=float),
                        rate_per_hour=pd.Series(dtype=float))
    d["dt_hours"] = d[time_col].diff().dt.total_seconds() / 3600.0
    d["dvalue"] = d[value_col].diff()
    d["rate_per_hour"] = d["dvalue"] / d["dt_hours"]
    return d
