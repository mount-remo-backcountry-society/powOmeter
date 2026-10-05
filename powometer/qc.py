"""Automatic quality control and snow-depth derivation (level "qc").

Sets `quality` (WaterML2 codes: good, suspect, estimate, poor, unchecked,
missing) and adds `qualifiers`. Never deletes a row. It changes a recorded
value in one case only: the sensor's two sentinel distances (no echo, about
5 m; too close, 0.50 m) are not measurements, so their value is set to
missing (NaN) and the qualifier says why.

Order (see pipeline.py): checks on the measured values -> manual
corrections -> snow depth derived from the corrected distance (inheriting
its labels) -> snow-depth checks.
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
    "enclosure_temperature": (-50.0, 80.0),     # in the box: can exceed air temperature in sun
    "battery_voltage": (2.0, 5.0),
}
NO_ECHO_M = (4.90, 5.20)          # sensor's own "no target" readings
TOO_CLOSE_M = 0.505               # MB7374 reports anything nearer than 50 cm as 0.50 m;
                                  # in storms these are echoes off falling snow (JK, 2026-10-04)
DEAD_TEMP = -99.0                 # firmware writes -99.9 for a dead SHT31
SPIKE_M = 0.30                    # distance departing this far from its neighbours
SPIKE_WINDOW = "2h"               # centred: neighbours within +/-1 h (review F13)
VISIT_STEP_M = 0.15               # distance step at a visit that suggests a re-mount
VISIT_STEP_WINDOW = "6h"          # median before / after the visit
BOUNDARY_SLACK = "12h"            # a mount change this close to the visit explains the step
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
            close = m & (df["value"] <= TOO_CLOSE_M)
            df.loc[close, "value"] = np.nan         # minimum-range sentinel (review F1)
            worsen(df, close, "missing")
            add_qualifier(df, close, "too_close")
            m = m & ~echo & ~close
        if var == "air_temperature":
            dead = m & (df["value"] <= DEAD_TEMP)
            worsen(df, dead, "missing")
            add_qualifier(df, dead, "sensor_fault")
            m = m & ~dead
        bad = m & ~df["value"].between(lo, hi)
        worsen(df, bad, "poor")
        add_qualifier(df, bad, "out_of_range")


def spike_checks(df: pd.DataFrame) -> None:
    """A distance more than SPIKE_M from the median of the readings within
    +/-1 h is a spike. The window is in time, not in rows, so the neighbours
    of a reading are never days away across a gap in the record."""
    hits = pd.Series(False, index=df.index)
    good = df[(df.variable == "distance_to_surface") & (df.quality == "good")]
    for _, grp in good.groupby(["site_id", "statistic"]):
        g = grp.sort_values("time_utc", kind="stable")
        s = pd.Series(g["value"].to_numpy(), index=pd.DatetimeIndex(g["time_utc"]))
        med = s.rolling(SPIKE_WINDOW, center=True, min_periods=3).median()
        spike = ((s - med).abs() > SPIKE_M).to_numpy()
        hits.loc[g.index[spike]] = True
    worsen(df, hits, "suspect")
    add_qualifier(df, hits, "spike")


def site_visits(df: pd.DataFrame, cfg: Config) -> None:
    for v in cfg.visits:
        d = v.get("disturbed")
        if not d:
            continue
        m = df["time_utc"].between(as_utc(d["from"]), as_utc(d["until"]), inclusive="left")
        worsen(df, m, "suspect")
        add_qualifier(df, m, "site_visit")


def visit_steps(df: pd.DataFrame, cfg: Config) -> list[dict]:
    """Unrecorded re-mounts (code review F9). At each field visit, compare
    the median distance in the VISIT_STEP_WINDOW before and after the visit
    (its `disturbed` window, else 08:00-20:00 BC time on the visit day). A
    step of VISIT_STEP_M or more with no mount change in mounts.yaml within
    BOUNDARY_SLACK means the snow-depth reference is probably wrong from
    then on. Fresh snow can also cause a step, so this raises an alarm for a
    person to look at; it never changes a reference itself."""
    d = df[(df.variable == "distance_to_surface") & (df.statistic == "median") & (df.quality == "good")]
    bounds = sorted(as_utc(m["from"]) for m in cfg.mounts)
    win, slack = pd.Timedelta(VISIT_STEP_WINDOW), pd.Timedelta(BOUNDARY_SLACK)
    out = []
    for v in cfg.visits:
        w = v.get("disturbed")
        if w:
            a, b = pd.Timestamp(as_utc(w["from"])), pd.Timestamp(as_utc(w["until"]))
        else:
            day = pd.Timestamp(v["date"]).tz_localize("UTC")
            a, b = day + pd.Timedelta(hours=15), day + pd.Timedelta(hours=27)
        if any(a - slack <= t <= b + slack for t in bounds):
            continue
        pre = d[(d.time_utc >= a - win) & (d.time_utc < a)]
        post = d[(d.time_utc >= b) & (d.time_utc < b + win)]
        if pre.empty or post.empty:
            continue
        step = float(post["value"].median() - pre["value"].median())
        if abs(step) >= VISIT_STEP_M:
            until = next((t for t in bounds if t > b), None)
            out.append({"date": str(v["date"]), "site": post["site_id"].mode().iloc[0],
                        "step_m": round(step, 2), "from": b, "until": until})
    return out


def derive_snow_depth(df: pd.DataFrame, cfg: Config, steps: list[dict] = ()) -> pd.DataFrame:
    """snow depth (cm, whole numbers) = mount reference - distance, for every
    distance row inside a mounting period. Labels are inherited. After an
    unrecorded re-mount (`steps`, from visit_steps) snow depth is labelled
    estimate until the next mount change."""
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
    for s in steps:
        m = (sd.site_id == s["site"]) & (sd.time_utc >= s["from"])
        if s["until"] is not None:
            m &= sd.time_utc < s["until"]
        worsen(sd, m, "estimate")
        add_qualifier(sd, m, "unrecorded_remount")
    neg = sd["value"] < NEGATIVE_DEPTH_CM
    worsen(sd, neg, "suspect")
    add_qualifier(sd, neg, "negative_depth")
    return pd.concat([df, sd], ignore_index=True)


def run(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Checks on the measured values. Snow depth is derived later, after the
    manual corrections (pipeline.py)."""
    df = df.copy()
    df["qualifiers"] = df["qualifiers"].fillna("")
    df["mount_id"] = None
    range_checks(df)
    spike_checks(df)
    site_visits(df, cfg)
    return df
