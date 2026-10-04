"""On-the-hour series for comparison with forecasts and other stations.

The station's data are POINT readings (about every 17 min on SD, about
hourly by radio). Hourly values here are NOT means (review F7): each is
interpolated linearly to the whole hour from the nearest readings before and
after, if both lie within MAX_GAP of the hour. Statistic stays "point";
qualifier "interpolated"; quality is the worse of the two neighbours.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .qc import RANK

MAX_GAP = pd.Timedelta(minutes=90)
INV_RANK = {v: k for k, v in RANK.items()}


def to_hourly(best: pd.DataFrame) -> pd.DataFrame:
    out = []
    keys = ["station_id", "site_id", "variable", "statistic", "unit"]
    for key, g in best[best["value"].notna()].groupby(keys):
        g = g.sort_values("time_utc")
        t = g["time_utc"].to_numpy()
        hours = pd.date_range(g["time_utc"].min().ceil("h"), g["time_utc"].max().floor("h"),
                              freq="h", tz="UTC")
        if len(hours) == 0:
            continue
        hv = hours.to_numpy()
        j = np.searchsorted(t, hv, side="right")            # first reading after the hour
        i = j - 1                                           # last reading at/before the hour
        ok = (i >= 0) & (j < len(t))
        i, j, hv = i[ok], j[ok], hv[ok]
        near = ((hv - t[i]) <= MAX_GAP.to_timedelta64()) & ((t[j] - hv) <= MAX_GAP.to_timedelta64())
        i, j, hv = i[near], j[near], hv[near]
        v = g["value"].to_numpy()
        frac = (hv - t[i]) / (t[j] - t[i])
        val = v[i] + (v[j] - v[i]) * frac
        exact = t[i] == hv
        val[exact] = v[i][exact]
        qr = np.maximum(g["quality"].map(RANK).to_numpy()[i], g["quality"].map(RANK).to_numpy()[j])
        appr = np.where((g["approval"].to_numpy()[i] == "approved") &
                        (g["approval"].to_numpy()[j] == "approved"), "approved", "working")
        df = pd.DataFrame({"time_utc": pd.to_datetime(hv, utc=True), "value": val,
                           "quality": [INV_RANK[r] for r in qr], "approval": appr,
                           "level": g["level"].to_numpy()[j], "qualifiers": "interpolated"})
        for k, kv in zip(keys, key):
            df[k] = kv
        out.append(df)
    if not out:
        return best.iloc[0:0]
    h = pd.concat(out, ignore_index=True)
    h.loc[h.variable == "snow_depth", "value"] = h.loc[h.variable == "snow_depth", "value"].round()
    return h
