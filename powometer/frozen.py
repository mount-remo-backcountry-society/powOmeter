"""Reconstruct times for the frozen-clock window (2025-01-31 -> 2025-02-17).

Ported from scripts/reconstruct_frozen_period.py (frozen in
tests/fixtures/reference_frozen_v0.csv). The station's clock was stopped or
failing, so the SD card holds no usable time; but the station kept
transmitting, and every satellite message has a true transmit time.

Method (unchanged from v0): the LAST reading of each message was taken at
essentially its transmit time. Readings are matched back to SD rows on the
exact (distance cm, temperature x10, humidity) triple that the payload
encodes (the radio sent the burst MINIMUM before firmware v1.3), with a
monotonic alignment, and times are interpolated linearly between anchors.

Difference from v0: anchors come from raw/radio (true UTC, complete RockBLOCK
history) instead of a local Google Sheet export with PST times. The radio
history has 10 anchors the sheet lacked, 9 of them sent during the 2025-02-07
site visit, when the station was power-cycled repeatedly and logged ~120
readings in about an hour. v0, without those anchors, spread those readings
over the following day (rows 607-934 off by up to 20 h). Verified
independently: the SD download taken AT that visit already contains block
rows up to index 723, so they cannot have been taken a day later.
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
from pathlib import Path

from . import RAW
from . import sd
from .radio import Message

FROZEN_LOG = ("2025-07-06", "LOG.CSV")     # the download that holds the whole block
MSG = re.compile(r"^(\d{6})(\d)(.+)$")
READING = re.compile(r"^(\d{3})([+-]\d{3})(\d{2})$")


def payload_code(parts):
    """An SD row encoded exactly as prepMsg() put it on the radio (minimum)."""
    try:
        cm = int(round(float(parts[3]) / 10.0))
        t = int(round(float(parts[6]) * 10))
        rh = int(round(float(parts[7])))
        return (cm, t, 0 if rh >= 100 else rh)
    except Exception:
        return None


def find_block(raw_dir: Path = RAW):
    meas, syncs = sd.parse_log(raw_dir / "sd" / FROZEN_LOG[0] / FROZEN_LOG[1])
    out = sd.correct(meas, syncs)
    runs, cur = [], None
    for i, (_d, t, _p, _q) in enumerate(out):
        if t is None:
            cur = [i, i] if cur is None else [cur[0], i]
        elif cur:
            runs.append(tuple(cur)); cur = None
    if cur:
        runs.append(tuple(cur))
    a, b = max(runs, key=lambda r: r[1] - r[0])
    before = next(out[j][1] for j in range(a - 1, -1, -1) if out[j][1])
    after = next(out[j][1] for j in range(b + 1, len(out)) if out[j][1])
    return [out[i][2] for i in range(a, b + 1)], before, after


def anchors_from(messages: list[Message], lo: dt.datetime, hi: dt.datetime):
    out = []
    for m in messages:
        t = m.transmit_utc.replace(tzinfo=None)
        if not (lo <= t <= hi):
            continue
        g = MSG.match(m.payload_text)
        if not g:
            continue
        mm = READING.match(g.group(3).split(":")[-1])
        if mm:
            out.append((t, (int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))))
    return sorted(set(out))


def align(codes, anchors, t0, t1):
    """Assign each anchor an SD row index, monotonically (v0 algorithm)."""
    n = len(codes)
    where: dict = {}
    for i, c in enumerate(codes):
        where.setdefault(c, []).append(i)
    span = (t1 - t0).total_seconds()

    def predict(t):
        return (t - t0).total_seconds() / span * (n - 1)

    accepted: list = []
    for _ in range(3):
        cand = []
        for t, code in anchors:
            idxs = where.get(code)
            if not idxs:
                continue
            if accepted:
                xs = [a[0] for a in accepted]; ys = [a[1] for a in accepted]
                j = bisect.bisect_left(ys, t)
                if j == 0 or j >= len(ys):
                    p = predict(t)
                else:
                    f = (t - ys[j - 1]).total_seconds() / max(1.0, (ys[j] - ys[j - 1]).total_seconds())
                    p = xs[j - 1] + f * (xs[j] - xs[j - 1])
            else:
                p = predict(t)
            best = min(idxs, key=lambda i: abs(i - p))
            if abs(best - p) <= max(12, 0.02 * n):
                cand.append((best, t))
        cand.sort(key=lambda x: x[1])
        keep, last = [], -1
        for i, t in cand:
            if i > last:
                keep.append((i, t)); last = i
        accepted = keep
    return accepted


def reconstruct(messages: list[Message], raw_dir: Path = RAW):
    """Return (block_parts, times, n_anchors): reconstructed naive-UTC times
    for each row of the frozen block."""
    block, before, after = find_block(raw_dir)
    codes = [payload_code(p) for p in block]
    n = len(block)
    anchors = anchors_from(messages, before - dt.timedelta(hours=3), after + dt.timedelta(hours=3))
    acc = align(codes, anchors, before, after)
    pts = [(-1.0, before)] + [(float(i), t) for i, t in acc] + [(float(n), after)]
    xs = [p[0] for p in pts]
    times = []
    for i in range(n):
        j = bisect.bisect_right(xs, float(i)) - 1
        j = min(max(j, 0), len(pts) - 2)
        x0, t_0 = pts[j]; x1, t_1 = pts[j + 1]
        f = (i - x0) / (x1 - x0)
        times.append(t_0 + dt.timedelta(seconds=f * (t_1 - t_0).total_seconds()))
    return block, times, len(acc)
