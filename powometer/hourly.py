"""On-the-hour series for comparison with forecasts and other stations.

The station's data are POINT readings (about every 17 min on SD, about
hourly by radio). Hourly values here are NOT means (review F7): each is
interpolated linearly to the whole hour from the nearest readings before and
after, if both lie within MAX_GAP of the hour. Statistic stays "point";
qualifier "interpolated". Quality, approval and level are the worse of the
two neighbours (a value is only as approved as the readings it comes from).
A reading exactly on the hour is used as it is, with its own labels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .qc import RANK

MAX_GAP = pd.Timedelta(minutes=90)
INV_RANK = {v: k for k, v in RANK.items()}
APPROVAL_RANK = {"working": 0, "in_review": 1, "approved": 2}    # min = worse
INV_APPROVAL = {v: k for k, v in APPROVAL_RANK.items()}
LEVEL_RANK = {"raw": 0, "qc": 1, "corrected": 2}                  # max = most changed
INV_LEVEL = {v: k for k, v in LEVEL_RANK.items()}
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _seconds(times: pd.Series) -> np.ndarray:
    """UTC times as float seconds since 1970 (pandas 3 stores microseconds)."""
    return ((times - EPOCH) / pd.Timedelta("1s")).to_numpy(dtype=float)


def to_hourly(best: pd.DataFrame) -> pd.DataFrame:
    out = []
    keys = ["station_id", "site_id", "variable", "statistic", "unit"]
    for key, g in best[best["value"].notna()].groupby(keys):
        g = g.sort_values("time_utc")
        t = _seconds(g["time_utc"])
        hours = pd.date_range(g["time_utc"].min().ceil("h"), g["time_utc"].max().floor("h"),
                              freq="h", tz="UTC")
        if len(hours) == 0:
            continue
        hv = _seconds(pd.Series(hours))
        j = np.searchsorted(t, hv, side="right")            # first reading after the hour
        i = j - 1                                           # last reading at/before the hour
        exact = (i >= 0) & (t[np.maximum(i, 0)] == hv)      # a reading on the hour itself
        ok = exact | ((i >= 0) & (j < len(t)))
        i, j, hv, exact = i[ok], j[ok], hv[ok], exact[ok]
        jj = np.where(exact, i, j)                          # on the hour: use the reading alone
        gap = MAX_GAP.total_seconds()
        near = exact | (((hv - t[i]) <= gap) & ((t[jj] - hv) <= gap))
        i, jj, hv, exact = i[near], jj[near], hv[near], exact[near]
        v = g["value"].to_numpy()
        span = t[jj] - t[i]
        frac = np.divide(hv - t[i], span, out=np.zeros(len(hv)), where=span != 0)
        val = v[i] + (v[jj] - v[i]) * frac
        hv = pd.to_datetime(hv, unit="s", utc=True)
        # labels: the worse of the two neighbours, or the reading itself on
        # an exact hour (review F13)
        qr = np.maximum(g["quality"].map(RANK).to_numpy()[i], g["quality"].map(RANK).to_numpy()[jj])
        ar = g["approval"].map(APPROVAL_RANK).to_numpy()
        lr = g["level"].map(LEVEL_RANK).to_numpy()
        df = pd.DataFrame({"time_utc": pd.to_datetime(hv, utc=True), "value": val,
                           "quality": [INV_RANK[r] for r in qr],
                           "approval": [INV_APPROVAL[r] for r in np.minimum(ar[i], ar[jj])],
                           "level": [INV_LEVEL[r] for r in np.maximum(lr[i], lr[jj])],
                           "qualifiers": np.where(exact, "", "interpolated")})
        for k, kv in zip(keys, key):
            df[k] = kv
        out.append(df)
    if not out:
        return best.iloc[0:0]
    h = pd.concat(out, ignore_index=True)
    h.loc[h.variable == "snow_depth", "value"] = h.loc[h.variable == "snow_depth", "value"].round()
    return h
