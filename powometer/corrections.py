"""Apply the manual corrections in config/corrections.yaml (level "corrected").

Operations, in file order:
  delete        quality -> poor (value kept, so the record stays auditable;
                best.csv leaves poor values out)
  spike_filter  values departing more than max_step from the rolling median
                over `window` readings -> poor
  threshold     values outside [min, max] -> poor
  gap_fill      missing values in gaps up to max_gap (e.g. "2h") are
                interpolated, quality estimate, qualifier gap_filled
  offset        value + params.value
  drift         value + a linear ramp from params.start (at 'from') to
                params.end (at 'until')

Every affected row gets level "corrected" and the correction id as a
qualifier.

Two phases (independent code review F2): corrections to measured variables
run BEFORE snow depth is derived, so a fix to the distance reaches the snow
depth; corrections to snow_depth itself run after. offset and drift change
values in the variable's own unit, so they must name one variable
(config.validate refuses `variable: all` for them).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config, as_utc
from .qc import add_qualifier, worsen


def _target(df: pd.DataFrame, c: dict) -> pd.Series:
    m = df["station_id"] == c["station"]
    if c["variable"] != "all":
        m &= df["variable"] == c["variable"]
    if "message" in c:
        m &= df["message_utc"] == as_utc(c["message"])
    else:
        a, b = as_utc(c.get("from")), as_utc(c.get("until"))
        if a is not None:
            m &= df["time_utc"] >= a
        if b is not None:
            m &= df["time_utc"] < b
    return m


def apply(df: pd.DataFrame, cfg: Config, phase: str = "measured",
          matched: set | None = None) -> pd.DataFrame:
    """Apply the corrections of one phase: "measured" (all but snow_depth)
    or "derived" (snow_depth). Ids of corrections that matched rows are added
    to `matched`; the build reports the ones that matched nothing in either
    phase (a typo in a time would otherwise fail silently)."""
    if phase not in ("measured", "derived"):
        raise ValueError(f"unknown correction phase {phase!r}")
    df = df.copy()
    for c in cfg.corrections:
        if (c["variable"] == "snow_depth") != (phase == "derived"):
            continue
        m = _target(df, c)
        if not m.any():
            continue
        if matched is not None:
            matched.add(c["id"])
        op, p = c["op"], c.get("params") or {}
        if op == "delete":
            df.loc[m, "quality"] = "poor"
        elif op == "threshold":
            bad = m & ~df["value"].between(p["min"], p["max"])
            worsen(df, bad, "poor")
            m = bad
        elif op == "spike_filter":
            hits = pd.Series(False, index=df.index)
            for _, g in df[m].groupby(["site_id", "variable", "statistic"]):
                g = g.sort_values("time_utc")
                med = g["value"].rolling(int(p["window"]), center=True, min_periods=2).median()
                hits.loc[g.index[((g["value"] - med).abs() > p["max_step"]).fillna(False).values]] = True
            worsen(df, hits, "poor")
            m = hits
        elif op == "offset":
            df.loc[m, "value"] = df.loc[m, "value"] + p["value"]
        elif op == "drift":
            a, b = as_utc(c["from"]), as_utc(c["until"])
            frac = (df.loc[m, "time_utc"] - a) / (b - a)
            df.loc[m, "value"] = df.loc[m, "value"] + p["start"] + frac * (p["end"] - p["start"])
        elif op == "gap_fill":
            filled = pd.Series(False, index=df.index)
            limit = pd.Timedelta(p["max_gap"])
            for _, g in df[m].groupby(["site_id", "variable", "statistic"]):
                g = g.sort_values("time_utc")
                ok = g[g["value"].notna()]
                for i in g.index[g["value"].isna()]:
                    t = g.at[i, "time_utc"]
                    before, after = ok[ok.time_utc < t].tail(1), ok[ok.time_utc > t].head(1)
                    if before.empty or after.empty:
                        continue
                    t0, t1 = before.time_utc.iloc[0], after.time_utc.iloc[0]
                    if t1 - t0 > limit:
                        continue
                    v0, v1 = before.value.iloc[0], after.value.iloc[0]
                    df.at[i, "value"] = v0 + (v1 - v0) * ((t - t0) / (t1 - t0))
                    df.at[i, "quality"] = "estimate"
                    filled.at[i] = True
            add_qualifier(df, filled, "gap_filled")
            m = filled
        df.loc[m, "level"] = "corrected"
        add_qualifier(df, m, c["id"])
    return df
