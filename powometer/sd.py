"""Read SD-card downloads (raw/sd/) and recover true UTC times.

Ported from scripts/clean_sd_data.py (the "v0" logic), whose behaviour is
frozen in tests/fixtures/reference_sd_v0.csv. Two documented extensions,
each switchable so the v0 behaviour stays testable:

1. carry_sync: a log with no clock-sync of its own (summer 2026: the modem
   was disconnected, so no syncs) continues from the last sync of the log
   that precedes it on the same clock. v0 left all such rows unrecoverable.
2. The frozen-clock window 2025-01-31 -> 2025-02-17 is timed from satellite
   transmit times in raw/radio (see frozen.py), not from a sheet export.

Why LOG.CSV and not DATA.CSV: LOG.CSV holds every "Data:" row AND every
clock sync in true append order, so each row can be tied to the clock state
in force when it was taken (see the v0 docstring for the full reasoning).
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from . import RAW

ERA_OFFSET = dt.timedelta(days=3932, hours=3, minutes=50, seconds=22)   # ERA3 - ERA2 epoch
PST = dt.timedelta(hours=8)                    # station clock: fixed UTC-8
PLAUSIBLE = (dt.datetime(2024, 1, 1), dt.datetime(2028, 1, 1))
ERA_SWITCH = dt.datetime(2026, 1, 14, 18, 8)   # Iridium ERA2 -> ERA3, UTC
DEPLOYMENT_START = dt.datetime(2025, 1, 26, 18, 58, 34)   # pressure step: on the mountain
CARRY_MAX_GAP = dt.timedelta(hours=1)          # logs closer than this share one clock

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}), (.*)$")
TS = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")
TFM = re.compile(r"TIME FROM MODEM: (-?\d+)-(-?\d+)-(-?\d+) (-?\d+):(-?\d+)")

FIELDS = ["datetime", "batt_v", "memory", "snow_depth_min_mm", "snow_depth_max_mm",
          "snow_depth_median_mm", "air_2m_temp_deg_c", "air_2m_temp_rh_prct",
          "baro_temp_deg_c", "baro_pressure_hPa"]

RANK = {"synced": 5, "era-offset": 4, "clock-was-correct": 3, "carried-sync": 2.5,
        "extrapolated": 2, "frozen-clock": 1, "unrecoverable": 0}


@dataclass(frozen=True)
class SdRow:
    true_utc: dt.datetime | None     # naive, UTC
    device_utc: dt.datetime          # naive, UTC (device UTC-8 + 8 h)
    time_method: str
    visit: str
    parts: tuple[str, ...]           # the raw "Data:" fields, unchanged


def loose_dt(m):
    """Parse a timestamp that may carry impossible fields (hour 26, day 32),
    which the Jan-23 field patch really did write into the RTC."""
    y, mo, d, hh, mi, ss = (int(x) for x in m.groups())
    mo = max(1, min(12, mo))
    return dt.datetime(y, mo, 1) + dt.timedelta(days=d - 1, hours=hh, minutes=mi, seconds=ss)


def parse_log(path: Path):
    """Return (measurements, syncs) from one LOG.CSV, in file order.
    measurements: (index, device_utc, parts); syncs: (index, written, true)."""
    meas, syncs, pending = [], [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = LINE.match(raw.rstrip("\n").rstrip("\r"))
            if not m:
                continue
            msg = m.group(2)
            if msg.startswith("Data: "):
                parts = msg[6:].split(",")
                if len(parts) == len(FIELDS):
                    g = TS.match(parts[0])
                    if g:
                        meas.append((len(meas), loose_dt(g) + PST, tuple(parts)))
            elif msg.startswith(" - Arduino Old Time:"):
                pending = {}
            elif "TIME FROM MODEM" in msg:
                g = TFM.search(msg)
                if g:
                    pending["tfm"] = tuple(int(x) for x in g.groups())
            elif msg.startswith(" - Arduino New Time:"):
                g = TS.search(msg)
                if g:
                    written = loose_dt(g)
                    true = None
                    if "tfm" in pending:
                        y, mo, md, hh, mi = pending["tfm"]
                        try:
                            L = dt.datetime(1900 + y, max(1, min(12, mo + 1)), 1) \
                                + dt.timedelta(days=md - 1, hours=hh, minutes=mi)
                        except Exception:
                            L = None
                        if L is not None:
                            true = L if L.year >= 2020 else L + ERA_OFFSET
                    else:
                        true = written + ERA_OFFSET if written.year < 2020 else written
                    if true and PLAUSIBLE[0] <= true < PLAUSIBLE[1]:
                        syncs.append((len(meas), written, true))
                pending = {}
    return meas, syncs


def correct(meas, syncs, carried=None):
    """Attach a true UTC time to each measurement (v0 rules), with an optional
    carried-in sync (written, true) from the preceding log on the same clock.
    Returns a list of (device_utc, true_utc | None, parts, method)."""
    out = []
    starts = [s[0] for s in syncs]

    frozen = set()                      # a clock that never advances is stopped
    run = [0]
    for i in range(1, len(meas)):
        if meas[i][1] == meas[i - 1][1]:
            run.append(i)
        else:
            if len(run) >= 3:
                frozen.update(run)
            run = [i]
    if len(run) >= 3:
        frozen.update(run)

    for pos, (idx, dev, parts) in enumerate(meas):
        true_utc, method = None, None
        if syncs:
            j = bisect.bisect_right(starts, idx) - 1
            q = "extrapolated" if j < 0 else "synced"
            _, written, true = syncs[max(j, 0)]
            cand = true + (dev - written)
            if PLAUSIBLE[0] <= cand < PLAUSIBLE[1]:
                true_utc, method = cand, q

        if true_utc is None and dev.year < 2020:
            cand = dev + ERA_OFFSET
            if PLAUSIBLE[0] <= cand < PLAUSIBLE[1]:
                true_utc, method = cand, "era-offset"

        if true_utc is None and PLAUSIBLE[0] <= dev < PLAUSIBLE[1] and dev < ERA_SWITCH:
            true_utc, method = dev, "clock-was-correct"

        # Extension (last resort, so v0 results never change): a log with no
        # usable sync continues its predecessor's clock.
        if true_utc is None and carried is not None:
            written, true = carried
            cand = true + (dev - written)
            if PLAUSIBLE[0] <= cand < PLAUSIBLE[1]:
                true_utc, method = cand, "carried-sync"

        if pos in frozen:
            true_utc, method = None, "frozen-clock"

        out.append((dev, true_utc, parts, method or "unrecoverable"))
    return out


def log_files(raw_dir: Path = RAW):
    """(visit, path) for every LOG file, in folder order (as v0)."""
    out = []
    sd = raw_dir / "sd"
    for visit in sorted(p.name for p in sd.iterdir() if p.is_dir()):
        for fn in ("LOG.CSV", "LOG 2.CSV"):
            p = sd / visit / fn
            if p.exists():
                out.append((visit, p))
    return out


def load(raw_dir: Path = RAW, carry_sync: bool = True) -> list[SdRow]:
    """All SD measurements after deployment, deduplicated, sorted by true UTC.
    Rows whose time cannot be recovered are returned with true_utc=None."""
    parsed = [(visit, *parse_log(p)) for visit, p in log_files(raw_dir)]

    carried_for: dict[int, tuple] = {}
    if carry_sync:
        # A log continues the clock of its PREDECESSOR: the log that ENDS
        # closest before this one starts, within CARRY_MAX_GAP. (Ordering by
        # start time is wrong: a log can begin with bench rows from years
        # earlier, e.g. 2026-06-19 starts with 2015 device dates.)
        spans = sorted((meas[0][1], meas[-1][1], k)
                       for k, (_v, meas, _s) in enumerate(parsed) if meas)
        end_of = {k: end for _s, end, k in spans}
        state: dict[int, tuple | None] = {}       # clock state at the end of each log
        for start, _end, k in spans:
            preds = [j for j in state
                     if j != k and dt.timedelta(0) <= start - end_of[j] <= CARRY_MAX_GAP]
            if preds:
                j = max(preds, key=lambda j: end_of[j])
                if state[j] is not None:
                    carried_for[k] = state[j]
            syncs = parsed[k][2]
            state[k] = (syncs[-1][1], syncs[-1][2]) if syncs else carried_for.get(k)

    merged: dict[tuple, tuple] = {}
    for k, (visit, meas, syncs) in enumerate(parsed):
        for dev, true_utc, parts, method in correct(meas, syncs, carried_for.get(k)):
            key = (parts[0], parts[6], parts[9])      # device ts + temp + pressure
            rank = RANK.get(method, 0)
            prev = merged.get(key)
            if prev is None or rank > prev[0]:
                merged[key] = (rank, SdRow(true_utc, dev, method, visit, parts))

    rows = [r for _, r in merged.values()]
    timed = sorted((r for r in rows if r.true_utc is not None), key=lambda r: r.true_utc)
    timed = [r for r in timed if r.true_utc >= DEPLOYMENT_START]
    final: list[SdRow] = []
    for r in timed:                 # TPL5110 double cycles: rows < 60 s apart
        if final and abs((r.true_utc - final[-1].true_utc).total_seconds()) < 60:
            continue
        final.append(r)
    untimed = [r for r in rows if r.true_utc is None]
    return final + untimed
