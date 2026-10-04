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


def apply(df: pd.DataFrame, cfg: Config, unmatched: list | None = None) -> pd.DataFrame:
    """Apply all corrections. Ids of corrections that match no row are
    appended to `unmatched` (reported in status.json: a typo in a time would
    otherwise fail silently)."""
    df = df.copy()
    for c in cfg.corrections:
        m = _target(df, c)
        if not m.any():
            if unmatched is not None:
                unmatched.append(c["id"])
            continue
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
