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
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import RAW

ERA_OFFSET = dt.timedelta(days=3932, hours=3, minutes=50, seconds=22)   # ERA3 - ERA2 epoch
PST = dt.timedelta(hours=8)                    # station clock: fixed UTC-8
PLAUSIBLE = (dt.datetime(2024, 1, 1), dt.datetime(2028, 1, 1))
ERA_SWITCH = dt.datetime(2026, 1, 14, 18, 8)   # Iridium ERA2 -> ERA3, UTC
DEPLOYMENT_START = dt.datetime(2025, 1, 26, 18, 58, 34)   # pressure step: on the mountain
CARRY_MAX_GAP = dt.timedelta(hours=1)          # logs closer than this share one clock
CARRY_SYNCS = 5                                # carried offset: median of the last 5 syncs
LOG_NAME = re.compile(r"^LOG.*[.]CSV$", re.IGNORECASE)

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
    file: int = -1                   # index into log_files(), for the report


def loose_dt(m):
    """Parse a timestamp that may carry impossible fields (hour 26, day 32),
    which the Jan-23 field patch really did write into the RTC."""
    y, mo, d, hh, mi, ss = (int(x) for x in m.groups())
    mo = max(1, min(12, mo))
    return dt.datetime(y, mo, 1) + dt.timedelta(days=d - 1, hours=hh, minutes=mi, seconds=ss)


@dataclass(frozen=True)
class ZipMember:
    """A LOG file inside a zip in raw/sd/<visit>/ (large logs are zipped to
    fit GitHub's 25 MB web-upload limit)."""
    zip_path: Path
    member: str

    @property
    def name(self) -> str:
        return f"{self.zip_path.name}/{self.member}"


def _lines(src: Path | ZipMember):
    if isinstance(src, ZipMember):
        with zipfile.ZipFile(src.zip_path) as z:
            yield from io.TextIOWrapper(z.open(src.member), encoding="utf-8", errors="replace")
    else:
        with open(src, encoding="utf-8", errors="replace") as f:
            yield from f


def parse_log(path: Path | ZipMember, stats: dict | None = None):
    """Return (measurements, syncs) from one LOG file, in file order.
    measurements: (index, device_utc, parts); syncs: (index, written, true).
    If `stats` is given, it receives the number of "Data:" lines and of those
    rejected as unreadable (wrong field count or timestamp), which used to be
    dropped silently (code review F7)."""
    meas, syncs, pending = [], [], {}
    n_data = n_rejected = 0
    for raw in _lines(path):
        m = LINE.match(raw.rstrip("\n").rstrip("\r"))
        if not m:
            continue
        msg = m.group(2)
        if msg.startswith("Data: "):
            n_data += 1
            parts = msg[6:].split(",")
            g = TS.match(parts[0]) if len(parts) == len(FIELDS) else None
            if g:
                meas.append((len(meas), loose_dt(g) + PST, tuple(parts)))
            else:
                n_rejected += 1
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
    if stats is not None:
        stats.update(data_lines=n_data, rejected=n_rejected)
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


def _log_order(name: str):
    """LOG.CSV first, then LOG 2.CSV etc. (the v0 order, which decides ties
    between duplicate rows)."""
    u = name.upper()
    return (u != "LOG.CSV", u)


def log_files(raw_dir: Path = RAW) -> list[tuple[str, Path | ZipMember]]:
    """(visit, source) for every LOG*.CSV in raw/sd/<visit>/, matched without
    regard to case, including LOG files inside .zip files (code review F7)."""
    out = []
    sd = raw_dir / "sd"
    for visit in sorted(p.name for p in sd.iterdir() if p.is_dir()):
        files = [p for p in (sd / visit).iterdir() if p.is_file()]
        logs = [p for p in files if LOG_NAME.match(p.name)]
        out += [(visit, p) for p in sorted(logs, key=lambda p: _log_order(p.name))]
        for z in sorted(p for p in files if p.suffix.lower() == ".zip"):
            with zipfile.ZipFile(z) as zf:
                members = [n for n in zf.namelist() if LOG_NAME.match(n.rsplit("/", 1)[-1])]
            out += [(visit, ZipMember(z, n)) for n in sorted(members, key=lambda n: _log_order(n.rsplit("/", 1)[-1]))]
    return out


def _clock_state(syncs) -> tuple:
    """(written, true) to carry into the next log: the last sync's written
    time with the MEDIAN offset of the last CARRY_SYNCS syncs, so one bad
    modem time cannot shift a whole summer (code review F8)."""
    offsets = sorted(true - written for _i, written, true in syncs[-CARRY_SYNCS:])
    written = syncs[-1][1]
    return (written, written + offsets[len(offsets) // 2])


def load(raw_dir: Path = RAW, carry_sync: bool = True, report: list | None = None) -> list[SdRow]:
    """All SD measurements after deployment, deduplicated, sorted by true UTC.
    Rows whose time cannot be recovered are returned with true_utc=None.

    If `report` is given, it receives one dict per LOG file: lines read,
    lines rejected, rows kept and untimed, and any carried clock offset
    (shown in status.json)."""
    files = log_files(raw_dir)
    stats = [{} for _ in files]
    parsed = [(visit, *parse_log(p, st)) for (visit, p), st in zip(files, stats)]

    carried_for: dict[int, tuple] = {}
    carried_from: dict[int, int] = {}
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
                    carried_from[k] = j
            syncs = parsed[k][2]
            state[k] = _clock_state(syncs) if syncs else carried_for.get(k)

    merged: dict[tuple, tuple] = {}
    for k, (visit, meas, syncs) in enumerate(parsed):
        for dev, true_utc, parts, method in correct(meas, syncs, carried_for.get(k)):
            key = (parts[0], parts[6], parts[9])      # device ts + temp + pressure
            rank = RANK.get(method, 0)
            prev = merged.get(key)
            if prev is None or rank > prev[0]:
                merged[key] = (rank, SdRow(true_utc, dev, method, visit, parts, k))

    rows = [r for _, r in merged.values()]
    timed = sorted((r for r in rows if r.true_utc is not None), key=lambda r: r.true_utc)
    timed = [r for r in timed if r.true_utc >= DEPLOYMENT_START]
    final: list[SdRow] = []
    for r in timed:                 # TPL5110 double cycles: rows < 60 s apart
        if final and abs((r.true_utc - final[-1].true_utc).total_seconds()) < 60:
            continue
        final.append(r)
    untimed = [r for r in rows if r.true_utc is None]
    if report is not None:
        for k, ((visit, src), st) in enumerate(zip(files, stats)):
            w, t = carried_for.get(k, (None, None))
            report.append({
                "download": visit,
                "file": src.name,
                "data_lines": st["data_lines"],
                "rejected_lines": st["rejected"],
                "rows_kept": sum(1 for r in final if r.file == k),
                "rows_untimed": sum(1 for r in untimed if r.file == k),
                "carried_clock_offset_min": None if w is None else round((t - w).total_seconds() / 60),
                "carried_from": None if w is None else f"{files[carried_from[k]][0]}/{files[carried_from[k]][1].name}",
            })
    return final + untimed
