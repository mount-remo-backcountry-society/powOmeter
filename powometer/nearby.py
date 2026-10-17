"""Nearby stations (BC Ministry of Transportation & Infrastructure, via
Avalanche Canada's API): fetch, load, quality control.

Only stations that are `enabled` in config/stations.yaml are fetched, and
only `publish` ones reach the published files. Both need the owner's written
permission on file (config.validate; CLAUDE.md rule 3).

Raw archive: each fetch that finds rows not seen before writes them to a NEW
file raw/avcan/<fetch time>_avcan-api.jsonl, one JSON object per line:
{"station_id", "fetched_utc", "row"} with the API row exactly as received.
Files are never appended to or rewritten. The API keeps about 7 days, so a
fetch can fail for days without losing anything. If Avalanche Canada later
revises a value, the revised row is archived too and the newest fetch wins.

Quality control (labels only; nothing is deleted):
  range       physically implausible -> poor, out_of_range
  spike       temperature jumping away from the hours either side -> suspect
  stuck       temperature unchanged for STUCK_HOURS -> suspect
  neighbour   temperature that agrees with NO other station, each moved to
              this station's elevation with the standard lapse rate, within
              NEIGHBOUR_C -> suspect. In a winter inversion the ridges are
              warmer than the valleys, so a higher station may be up to
              INVERSION_C warmer than a lower neighbour suggests (and a valley
              that much colder). The Kasiks High fault of 2026-09-25 (-25 to
              -48.8 °C while the others read about +5 °C) fails this check; a
              -5 °C ridge over -20 °C valleys passes. POW-O-METER's hourly
              series counts as a neighbour.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import RAW, approval, corrections
from .config import Config, as_utc
from .decoders import avalanche_canada as decoder
from .qc import add_qualifier, worsen

TYPE = "avalanche_canada"
API = "https://weather.prod.avalanche.ca/stations/{id}/measurements"
FETCH_DAYS = 7
RAW_DIR = "avcan"

RANGES = {
    "air_temperature": (-50.0, 45.0),
    "snow_depth": (-20.0, 1500.0),
    "wind_speed": (0.0, 250.0),
    "wind_from_direction": (0.0, 360.0),
    "precipitation_amount": (0.0, 100.0),
}
PRECIP_NOISE_MM = -1.0          # negative amounts down to this are gauge noise (suspect, not poor)
SPIKE_C = 8.0                  # temperature jumping this far from the hours either side
SPIKE_GAP = pd.Timedelta(hours=3)   # ... which must be this close
STUCK_HOURS = 12                # temperature identical for this long
LAPSE_C_PER_M = 0.0065          # standard atmosphere, to compare stations at different heights
NEIGHBOUR_C = 10.0
INVERSION_C = 20.0              # extra room: a higher station may be this much warmer than its
                                # lower neighbours suggest (a valley this much colder)
NEIGHBOUR_MIN = 2               # other stations needed for the neighbour check

COLUMNS = ["station_id", "site_id", "time_utc", "variable", "statistic", "value", "unit",
           "source", "time_method", "message_utc", "qualifiers"]


def stations(cfg: Config, flag: str = "enabled") -> list[dict]:
    return [s for s in cfg.stations if s.get("type") == TYPE and s.get(flag) is True]


# --- fetch ------------------------------------------------------------------

def download(url: str, timeout: int = 30) -> list[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "powometer (MRBS station data)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected a list of measurements, got {type(data).__name__}")
    return data


def _key(station_id: str, row: dict) -> tuple:
    return (station_id, row.get("measurementDateTime"), json.dumps(row, sort_keys=True))


def load_raw(raw_dir: Path = RAW) -> list[dict]:
    """Every archived record, oldest fetch first."""
    out = []
    for p in sorted((raw_dir / RAW_DIR).glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            out += [json.loads(line) for line in f if line.strip()]
    return out


def fetch(cfg: Config, raw_dir: Path = RAW, now: datetime | None = None,
          get: Callable[[str], list[dict]] = download) -> tuple[Path | None, dict]:
    """Fetch every enabled station and archive the rows not seen before.
    Returns (new file or None, report per station). A station that fails is
    reported and skipped; it never stops the others or the build."""
    now = now or datetime.now(timezone.utc)
    known = {_key(r["station_id"], r["row"]) for r in load_raw(raw_dir)}
    since = (now - timedelta(days=FETCH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new, report = [], {}
    for s in stations(cfg):
        url = API.format(id=s["source"]["avalanche_canada_id"]) + f"?fromDate={since}"
        try:
            rows = get(url)
            fresh = []
            for row in rows:
                decoder.parse_time(row["measurementDateTime"])       # refuse rows we cannot time
                k = _key(s["id"], row)
                if k not in known:
                    known.add(k)
                    fresh.append(row)
            report[s["id"]] = {"ok": True, "rows": len(rows), "new": len(fresh)}
            new += [{"station_id": s["id"], "fetched_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "row": r}
                    for r in fresh]
        except Exception as e:                       # noqa: BLE001 - reported, never fatal
            report[s["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not new:
        return None, report
    path = raw_dir / RAW_DIR / f"{now:%Y-%m-%dT%H%M%SZ}_avcan-api.jsonl"
    if path.exists():
        raise FileExistsError(f"{path} exists; raw files are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    new.sort(key=lambda r: (r["station_id"], r["row"]["measurementDateTime"]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in new:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return path, report


# --- load and check -----------------------------------------------------------

def _site_at(cfg: Config, station: str, t: datetime) -> str | None:
    for s in cfg.sites:
        if s["station"] != station:
            continue
        a, b = as_utc(s["from"]), as_utc(s.get("until"))
        if a <= t and (b is None or t < b):
            return s["id"]
    return None


def readings(cfg: Config, records: list[dict], station_ids: set[str]) -> tuple[pd.DataFrame, dict]:
    """Raw records -> one row per value (latest fetch wins for a station and
    hour). Also returns {station: sorted unmapped API fields}."""
    latest: dict[tuple, dict] = {}
    unmapped: dict[str, set] = {}
    for r in records:                                   # oldest fetch first: later ones overwrite
        if r["station_id"] in station_ids:
            latest[(r["station_id"], r["row"]["measurementDateTime"])] = r
    out = []
    for (station, _), r in sorted(latest.items()):
        unmapped.setdefault(station, set()).update(decoder.unmapped(r["row"]))
        for v in decoder.decode(r["row"]):
            site = _site_at(cfg, station, v["time_utc"])
            if site is None:
                continue
            out.append({**v, "station_id": station, "site_id": site, "source": "avcan",
                        "time_method": "source", "message_utc": None, "qualifiers": ""})
    df = pd.DataFrame(out, columns=COLUMNS)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df["value"] = pd.to_numeric(df["value"])
    return df, {k: sorted(v) for k, v in unmapped.items() if v}


def _range(df: pd.DataFrame) -> None:
    df["quality"] = np.where(df["value"].isna(), "missing", "good")
    df["level"] = "qc"
    # a precipitation gauge reports small negative amounts as noise (seen at
    # #58: -0.1 to -0.5 mm): published, but labelled; values are never clipped
    noise = (df.variable == "precipitation_amount") & df["value"].between(PRECIP_NOISE_MM, 0, inclusive="left")
    worsen(df, noise, "suspect")
    add_qualifier(df, noise, "negative_amount")
    for var, (lo, hi) in RANGES.items():
        m = (df.variable == var) & df["value"].notna() & ~noise
        bad = m & ~df["value"].between(lo, hi)
        worsen(df, bad, "poor")
        add_qualifier(df, bad, "out_of_range")


def _temperature(df: pd.DataFrame) -> pd.Series:
    return (df.variable == "air_temperature") & (df.quality == "good")


def _spike_and_stuck(df: pd.DataFrame) -> None:
    """A spike jumps away from BOTH the reading before and the one after, in
    the same direction (a step change, such as a sensor failing, is not a
    spike: the neighbour check deals with it)."""
    spikes = pd.Series(False, index=df.index)
    stuck = pd.Series(False, index=df.index)
    for _, g in df[_temperature(df)].groupby("station_id"):
        g = g.sort_values("time_utc", kind="stable")
        s = pd.Series(g["value"].to_numpy(), index=pd.DatetimeIndex(g["time_utc"]))
        t = s.index.to_series()
        up, down = s - s.shift(1), s - s.shift(-1)
        close = ((t - t.shift(1)) <= SPIKE_GAP) & ((t.shift(-1) - t) <= SPIKE_GAP)
        spike = close & (up.abs() > SPIKE_C) & (down.abs() > SPIKE_C) & (np.sign(up) == np.sign(down))
        spikes.loc[g.index[spike.to_numpy()]] = True
        # runs of identical values over consecutive hours
        run = (s.diff().fillna(1) != 0).cumsum()
        for _, r in s.groupby(run.to_numpy()):
            if len(r) >= 2 and (r.index[-1] - r.index[0]) >= pd.Timedelta(hours=STUCK_HOURS):
                stuck.loc[g.index[np.isin(g["time_utc"].to_numpy(), r.index.to_numpy())]] = True
    worsen(df, spikes, "suspect")
    add_qualifier(df, spikes, "spike")
    worsen(df, stuck, "suspect")
    add_qualifier(df, stuck, "stuck")


def _neighbours(df: pd.DataFrame, cfg: Config, own_hourly: pd.DataFrame | None) -> None:
    """Temperature against the other stations at the same hour, each moved to
    this station's elevation with the standard lapse rate."""
    elev = {s["id"]: s["elevation_m"] for s in cfg.sites}
    t = df[_temperature(df)][["station_id", "site_id", "time_utc", "value"]]
    if own_hourly is not None and not own_hourly.empty:
        own = own_hourly[(own_hourly.variable == "air_temperature") &
                         own_hourly.quality.isin(["good", "estimate"])][["station_id", "site_id", "time_utc", "value"]]
        refs = pd.concat([t, own], ignore_index=True)
    else:
        refs = t
    refs = refs.assign(elev=refs.site_id.map(elev))
    by_hour = {h: g for h, g in refs.groupby("time_utc")}
    hits = pd.Series(False, index=df.index)
    for i, r in t.iterrows():
        g = by_hour.get(r.time_utc)
        if g is None:
            continue
        others = g[g.station_id != r.station_id]
        if len(others) < NEIGHBOUR_MIN:
            continue
        here = elev.get(r.site_id)
        moved = others["value"] + LAPSE_C_PER_M * (others["elev"] - here)
        below = others["elev"] < here                   # neighbour lower down
        lo = moved - NEIGHBOUR_C - np.where(below, 0.0, INVERSION_C)
        hi = moved + NEIGHBOUR_C + np.where(below, INVERSION_C, 0.0)
        if not ((r.value >= lo) & (r.value <= hi)).any():   # agrees with no neighbour at all
            hits.at[i] = True
    worsen(df, hits, "suspect")
    add_qualifier(df, hits, "neighbour")


def process(cfg: Config, records: list[dict] | None = None, own_hourly: pd.DataFrame | None = None,
            flag: str = "publish") -> tuple[pd.DataFrame, set[str], list[str], dict]:
    """(observations, matched correction ids, approval alarms, unmapped fields)
    for the nearby stations with `flag` set. `own_hourly`: POW-O-METER's
    on-the-hour series, used as one more neighbour."""
    records = load_raw() if records is None else records
    ids = {s["id"] for s in stations(cfg, flag)}
    df, unmapped = readings(cfg, records, ids)
    if df.empty:
        return df.assign(quality=None, level=None, approval=None), set(), [], unmapped
    df["mount_id"] = None
    _range(df)
    _spike_and_stuck(df)
    _neighbours(df, cfg, own_hourly)
    matched: set[str] = set()
    df = corrections.apply(df, cfg, "measured", matched)
    df = corrections.apply(df, cfg, "derived", matched)
    df, alarms = approval.apply(df, cfg)
    return df, matched, alarms, unmapped


def latest(best: pd.DataFrame, fmt: Callable[[pd.DataFrame], pd.DataFrame]) -> dict:
    """Newest value of each variable, per station."""
    out: dict = {}
    for (station, var), g in best.groupby(["station_id", "variable"]):
        r = fmt(g.sort_values("time_utc").tail(1)).iloc[0]
        out.setdefault(station, {"values": {}})["values"][var] = {
            "time_utc": r.time_utc, "value": float(r.value) if r.value != "" else None,
            "unit": r.unit, "statistic": r.statistic, "quality": r.quality,
            "approval": r.approval, "site_id": r.site_id}
    return out


def health(cfg: Config, records: list[dict], best: pd.DataFrame) -> dict:
    """Per published nearby station: newest observation and newest fetch that
    brought anything new (a stopped source shows as an old time)."""
    out = {}
    for s in stations(cfg, "publish"):
        mine = [r for r in records if r["station_id"] == s["id"]]
        obs = best[best.station_id == s["id"]]
        out[s["id"]] = {
            "last_observation_utc": obs.time_utc.max().strftime("%Y-%m-%dT%H:%M:%SZ") if not obs.empty else None,
            "last_new_data_fetched_utc": max((r["fetched_utc"] for r in mine), default=None),
        }
    return out
