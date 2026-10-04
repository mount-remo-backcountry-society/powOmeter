"""Approval: frozen snapshots of reviewed data (design §8.3, review F4).

Approving a period writes a snapshot CSV (time_utc, value, quality,
qualifiers) under approved/, and records it with its sha256 in
config/approvals.yaml. On every build:

  * rows inside an approved period are published FROM THE SNAPSHOT, with
    approval "approved";
  * the freshly rebuilt values are compared with the snapshot; any
    difference becomes an alarm in status.json. Approved data never changes
    silently; investigating the alarm is a human decision.

Everything else has approval "working" (shown as "Provisional" on the site).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from . import ROOT
from .config import Config, as_utc

SNAP_COLS = ["time_utc", "site_id", "statistic", "value", "quality", "qualifiers"]


def _rows(df: pd.DataFrame, station: str, variable: str, a, b) -> pd.Series:
    m = (df.station_id == station) & (df.variable == variable) & (df.time_utc >= a)
    if b is not None:
        m &= df.time_utc < b
    return m


def write_snapshot(df: pd.DataFrame, station: str, variable: str, start, end, path: Path) -> str:
    """Freeze the current rows of one variable and period. Returns sha256."""
    a, b = as_utc(start), as_utc(end)
    snap = df[_rows(df, station, variable, a, b)][SNAP_COLS].sort_values(["time_utc", "statistic"])
    out = snap.assign(time_utc=snap.time_utc.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, lineterminator="\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(df: pd.DataFrame, cfg: Config, root: Path = ROOT) -> tuple[pd.DataFrame, list[str]]:
    df = df.copy()
    df["approval"] = "working"
    alarms: list[str] = []
    for ap in cfg.approvals:
        a, b = as_utc(ap["from"]), as_utc(ap.get("until"))
        m = _rows(df, ap["station"], ap["variable"], a, b)
        snap = pd.read_csv(root / ap["snapshot"], dtype={"qualifiers": str}, keep_default_na=False,
                           na_values={"value": [""]})
        snap["time_utc"] = pd.to_datetime(snap["time_utc"], utc=True)
        fresh = df[m][SNAP_COLS].sort_values(["time_utc", "statistic"]).reset_index(drop=True)
        ref = snap.sort_values(["time_utc", "statistic"]).reset_index(drop=True)
        same = len(fresh) == len(ref) and \
            (fresh["time_utc"].values == ref["time_utc"].values).all() and \
            ((fresh["value"] - ref["value"]).abs().fillna(0).max() if len(ref) else 0) < 1e-9 and \
            (fresh["quality"].values == ref["quality"].values).all()
        if not same:
            alarms.append(f"approved {ap['variable']} {a:%Y-%m-%d}..{b:%Y-%m-%d} differs from its snapshot "
                          f"({ap['snapshot']}); published data kept from the snapshot")
        # publish the snapshot values for the approved period
        keyed = ref.set_index(["time_utc", "site_id", "statistic"])
        idx = df[m].set_index(["time_utc", "site_id", "statistic"]).index
        for col in ("value", "quality", "qualifiers"):
            df.loc[m, col] = keyed.reindex(idx)[col].values
        df.loc[m, "approval"] = "approved"
    return df, alarms
