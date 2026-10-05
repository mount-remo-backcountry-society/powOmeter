"""Approval: frozen snapshots of reviewed data (design §8.3, review F4).

Approving a period writes a snapshot CSV (time_utc, value, quality,
qualifiers) under approved/, and records it with its sha256 in
config/approvals.yaml. On every build:

  * rows inside an approved period that are IN the snapshot are published
    from the snapshot, with approval "approved";
  * rows the rebuild produces that the snapshot lacks (e.g. from a newly
    found SD download) keep their fresh values and labels, with approval
    "working": nobody reviewed them (code review F4);
  * the rebuilt values are compared with the snapshot; any difference
    (changed, missing or extra rows) becomes an alarm in status.json.
    Approved data never changes silently; investigating the alarm is a human
    decision.

Everything else has approval "working" (shown as "Provisional" on the site).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from . import ROOT
from .config import Config, as_utc

SNAP_COLS = ["time_utc", "site_id", "statistic", "value", "quality", "qualifiers"]
KEY = ["time_utc", "site_id", "statistic"]


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
        keyed = snap.drop_duplicates(KEY).set_index(KEY)
        fresh_idx = df[m].set_index(KEY).index
        in_snap = fresh_idx.isin(keyed.index)
        ref = keyed.reindex(fresh_idx[in_snap])
        fresh = df[m][in_snap]
        fv, rv = fresh["value"].to_numpy(dtype=float), ref["value"].to_numpy(dtype=float)
        same_v = (np.isnan(fv) & np.isnan(rv)) | (np.abs(fv - rv) < 1e-9)
        same_q = fresh["quality"].to_numpy() == ref["quality"].to_numpy()
        n_changed = int((~(same_v & same_q)).sum())
        n_extra = int((~in_snap).sum())
        n_missing = int((~keyed.index.isin(fresh_idx)).sum())
        if n_changed or n_extra or n_missing:
            until = f"{b:%Y-%m-%d}" if b is not None else "open"
            alarms.append(
                f"approved {ap['variable']} {a:%Y-%m-%d}..{until} differs from its snapshot "
                f"({ap['snapshot']}): {n_changed} changed, {n_missing} missing from the rebuild, "
                f"{n_extra} not in the snapshot (published as provisional); approved values kept")
        # publish the snapshot for the rows it has; rows it lacks stay "working"
        rows = df.index[m][in_snap]
        for col in ("value", "quality", "qualifiers"):
            df.loc[rows, col] = ref[col].to_numpy()
        df.loc[rows, "approval"] = "approved"
    return df, alarms
