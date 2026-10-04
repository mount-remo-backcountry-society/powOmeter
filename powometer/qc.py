"""Automatic quality control and snow-depth derivation (level "qc").

Sets `quality` (WaterML2 codes: good, suspect, estimate, poor, unchecked,
missing) and adds `qualifiers`. Never deletes a row and never changes a
recorded value; it only labels.

Order: range checks -> spikes -> site visits -> snow depth (which inherits
the distance's labels) -> snow-depth checks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config, as_utc

RANGES = {      # variable: (min, max) plausible; outside -> poor
    "distance_to_surface": (0.30, 5.20),
    "air_temperature": (-50.0, 45.0),
    "relative_humidity": (0.0, 100.0),
    "air_pressure": (500.0, 1100.0),
    "battery_voltage": (2.0, 5.0),
}
NO_ECHO_M = (4.90, 5.20)          # sensor's own "no target" readings
DEAD_TEMP = -99.0                 # firmware writes -99.9 for a dead SHT31
SPIKE_M = 0.30                    # distance departing this far from its neighbours
SPIKE_WINDOW = 5
NEGATIVE_DEPTH_CM = -15           # below this, snow depth is suspect (bare-ground scatter is about ±8 cm)
DEPTH_STAT = {"min": "max", "max": "min", "median": "median"}   # min distance = max depth

RANK = {"good": 0, "unchecked": 1, "estimate": 2, "suspect": 3, "poor": 4, "missing": 5}


def add_qualifier(df: pd.DataFrame, mask: pd.Series, q: str) -> None:
    cur = df.loc[mask, "qualifiers"].fillna("")
    df.loc[mask, "qualifiers"] = np.where(cur == "", q, cur + ";" + q)


def worsen(df: pd.DataFrame, mask: pd.Series, quality: str) -> None:
    """Set quality to `quality` where it is worse than the current one."""
    cur = df.loc[mask, "quality"].map(RANK)
    df.loc[mask & (df["quality"].map(RANK) < RANK[quality]), "quality"] = quality


def range_checks(df: pd.DataFrame) -> None:
    df["quality"] = np.where(df["value"].isna(), "missing", "good")
    df["level"] = "qc"
    for var, (lo, hi) in RANGES.items():
        m = (df.variable == var) & df["value"].notna()
        if var == "distance_to_surface":
            echo = m & df["value"].between(*NO_ECHO_M)
            df.loc[echo, "value"] = np.nan          # no target: not a distance
            worsen(df, echo, "missing")
            add_qualifier(df, echo, "no_echo")
            m = m & ~echo
        if var == "air_temperature":
            dead = m & (df["value"] <= DEAD_TEMP)
            worsen(df, dead, "missing")
            add_qualifier(df, dead, "sensor_fault")
            m = m & ~dead
        bad = m & ~df["value"].between(lo, hi)
        worsen(df, bad, "poor")
        add_qualifier(df, bad, "out_of_range")


def spike_checks(df: pd.DataFrame) -> None:
    for (site, stat), grp in df[(df.variable == "distance_to_surface") & (df.quality == "good")] \
            .groupby(["site_id", "statistic"]):
        g = grp.sort_values("time_utc")
        med = g["value"].rolling(SPIKE_WINDOW, center=True, min_periods=3).median()
        spike = (g["value"] - med).abs() > SPIKE_M
        idx = g.index[spike.fillna(False).values]
        m = df.index.isin(idx)
        worsen(df, pd.Series(m, index=df.index), "suspect")
        add_qualifier(df, pd.Series(m, index=df.index), "spike")


def site_visits(df: pd.DataFrame, cfg: Config) -> None:
    for v in cfg.visits:
        d = v.get("disturbed")
        if not d:
            continue
        m = df["time_utc"].between(as_utc(d["from"]), as_utc(d["until"]), inclusive="left")
        worsen(df, m, "suspect")
        add_qualifier(df, m, "site_visit")


def derive_snow_depth(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """snow depth (cm, whole numbers) = mount reference - distance, for every
    distance row inside a mounting period. Labels are inherited."""
    dist = df[df.variable == "distance_to_surface"]
    parts = []
    for m in cfg.mounts:
        a, b = as_utc(m["from"]), as_utc(m.get("until"))
        sel = dist[(dist.site_id == m["site"]) & (dist.time_utc >= a) &
                   ((dist.time_utc < b) if b is not None else True)].copy()
        if sel.empty:
            continue
        sel["value"] = ((m["reference_m"] - sel["value"]) * 100).round()
        sel["variable"] = "snow_depth"
        sel["unit"] = "cm"
        sel["statistic"] = sel["statistic"].map(DEPTH_STAT)
        if m["quality"] == "estimate":
            est = sel["quality"] == "good"
            sel.loc[est, "quality"] = "estimate"
            add_qualifier(sel, sel["value"].notna(), "reference_estimate")
        sel["mount_id"] = m["id"]
        parts.append(sel)
    if not parts:
        return df
    sd = pd.concat(parts)
    neg = sd["value"] < NEGATIVE_DEPTH_CM
    worsen(sd, neg, "suspect")
    add_qualifier(sd, neg, "negative_depth")
    return pd.concat([df, sd], ignore_index=True)


def run(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = df.copy()
    df["qualifiers"] = df["qualifiers"].fillna("")
    df["mount_id"] = None
    range_checks(df)
    spike_checks(df)
    site_visits(df, cfg)
    return derive_snow_depth(df, cfg)
