#!/usr/bin/env python3
"""
POW-O-METER -- reconstruct timestamps for the frozen-clock window
(2025-01-31 -> 2025-02-17), where the RTC was stopped or failing and the SD
card recorded no usable time.

METHOD
    The station kept transmitting throughout. Every Iridium message carries a
    gateway-assigned Transmit Time, which is true time and independent of the
    broken RTC. The LAST reading in a message was taken at essentially that
    moment (established from periods where the clock was healthy), so each
    message yields one trustworthy anchor.

    Readings are matched back to SD rows on the exact triple the payload
    encodes -- (distance cm, temperature x10, humidity) -- and the SD rows are
    contiguous in LOG.CSV order, so a monotonic alignment pins the sequence.
    Times are then interpolated piecewise-linearly between accepted anchors.

    Only the last reading of each message is used. The earlier readings in a
    multi-reading batch would have to be back-dated using an assumed 4035 s
    spacing, and the measured cadence in this window is 15.4 min rather than
    the nominal 16.8, so that assumption does not hold here.

CAVEAT
    These timestamps are RECONSTRUCTED, not recorded. They are written to
    their own folder so they can be loaded into Aquarius separately and shifted
    as a block.

Usage: python reconstruct_frozen_period.py
"""

import os, re, sys, csv, datetime as dt, bisect
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clean_sd_data as C

import openpyxl

SHEET = os.path.join(C.ROOT, "data", "Google Sheet", "Shames POW-O-METER.xlsx")
OUT = os.path.join(C.ROOT, "data", "Cleaned", "Reconstructed_2025-01-31_to_02-17")

MSG = re.compile(r"^(\d{6})(\d)(.+)$")
READING = re.compile(r"^(\d{3})([+-]\d{3})(\d{2})$")
PST = dt.timedelta(hours=8)


def payload_code(parts):
    """Encode an SD row exactly as prepMsg() would have put it on the radio."""
    try:
        cm = int(round(float(parts[3]) / 10.0))        # minimum distance, mm -> cm
        t = int(round(float(parts[6]) * 10))
        rh = int(round(float(parts[7])))
        return (cm, t, 0 if rh >= 100 else rh)
    except Exception:
        return None


def find_block():
    meas, syncs = C.parse_log(os.path.join(C.VISITS, "2025-07-06", "LOG.CSV"))
    out = C.correct(meas, syncs)
    runs, cur = [], None
    for i, (d, t, parts, q) in enumerate(out):
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


def load_anchors(lo_utc, hi_utc):
    wb = openpyxl.load_workbook(SHEET, read_only=True, data_only=True)
    ws = wb["Data"]
    seen, msgs, hdr = set(), [], None
    for r in ws.iter_rows(values_only=True):
        if hdr is None:
            hdr = r; continue
        if r[0] is None or r[7] is None or not isinstance(r[0], dt.datetime):
            continue
        key = (r[0], str(r[7]))
        if key not in seen:
            seen.add(key); msgs.append(key)
    anchors = []
    for t_pst, m in sorted(msgs):
        t_utc = t_pst + PST                       # sheet column is PST; Jan/Feb has no DST
        if not (lo_utc <= t_utc <= hi_utc):
            continue
        g = MSG.match(m)
        if not g:
            continue
        last = g.group(3).split(":")[-1]          # only the final reading is trustworthy
        mm = READING.match(last)
        if mm:
            anchors.append((t_utc, (int(mm.group(1)), int(mm.group(2)), int(mm.group(3)))))
    return anchors


def align(codes, anchors, t0, t1):
    """Assign each anchor an SD row index, monotonically."""
    n = len(codes)
    where = {}
    for i, c in enumerate(codes):
        where.setdefault(c, []).append(i)

    span = (t1 - t0).total_seconds()
    def predict(t):                                # first guess: uniform pace
        return (t - t0).total_seconds() / span * (n - 1)

    accepted = []
    for rnd in range(3):
        cand = []
        for t, code in anchors:
            idxs = where.get(code)
            if not idxs:
                continue
            if accepted:                           # refine using the fit so far
                xs = [a[0] for a in accepted]; ys = [a[1] for a in accepted]
                j = bisect.bisect_left(ys, t)
                if j == 0:
                    p = predict(t)
                elif j >= len(ys):
                    p = predict(t)
                else:
                    f = (t - ys[j - 1]).total_seconds() / max(1.0, (ys[j] - ys[j - 1]).total_seconds())
                    p = xs[j - 1] + f * (xs[j] - xs[j - 1])
            else:
                p = predict(t)
            best = min(idxs, key=lambda i: abs(i - p))
            if abs(best - p) <= max(12, 0.02 * n):
                cand.append((best, t))
        # keep a strictly increasing subsequence
        cand.sort(key=lambda x: x[1])
        keep, last = [], -1
        for i, t in cand:
            if i > last:
                keep.append((i, t)); last = i
        accepted = keep
    return accepted


def main():
    block, before, after = find_block()
    codes = [payload_code(p) for p in block]
    n = len(block)
    print("frozen block: %d rows, bracketed %s .. %s" % (n, before, after))

    anchors = load_anchors(before - dt.timedelta(hours=3), after + dt.timedelta(hours=3))
    print("satellite anchors available (last reading of each message): %d" % len(anchors))

    acc = align(codes, anchors, before, after)
    print("anchors accepted after monotonic alignment: %d" % len(acc))

    # piecewise-linear index -> time through (-1, before), anchors, (n, after)
    pts = [(-1.0, before)] + [(float(i), t) for i, t in acc] + [(float(n), after)]
    xs = [p[0] for p in pts]
    times = []
    for i in range(n):
        j = bisect.bisect_right(xs, float(i)) - 1
        j = min(max(j, 0), len(pts) - 2)
        x0, t_0 = pts[j]; x1, t_1 = pts[j + 1]
        f = (i - x0) / (x1 - x0)
        times.append(t_0 + dt.timedelta(seconds=f * (t_1 - t_0).total_seconds()))

    # quality: monotonic? cadence sane? anchor residuals?
    bad = sum(1 for a, b in zip(times, times[1:]) if b <= a)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    gaps_sorted = sorted(gaps)
    print("monotonic: %s   cadence min/median/max = %.1f / %.1f / %.1f min"
          % ("yes" if bad == 0 else "NO (%d)" % bad,
             gaps_sorted[0], gaps_sorted[len(gaps_sorted) // 2], gaps_sorted[-1]))
    resid = [abs((times[i] - t).total_seconds()) for i, t in acc]
    if resid:
        resid.sort()
        print("anchor residuals: median %.0f s, p90 %.0f s, max %.0f s"
              % (resid[len(resid) // 2], resid[int(0.9 * len(resid))], resid[-1]))

    write(block, times, len(acc))


def write(block, times, n_anchor):
    for variant, shift in (("UTC", dt.timedelta(0)),
                           ("DevicePST_matches_existing_archive", -PST)):
        d = os.path.join(OUT, variant)
        os.makedirs(d, exist_ok=True)
        for name, key, unit, label in C.SERIES:
            rows = []
            for parts, t in zip(block, times):
                flags = set()
                vals = C.qc_row(parts, flags)
                v = C.value_for(key, vals, t)
                if v is None:
                    continue
                ts = t + shift
                rows.append([ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             (ts - PST).strftime("%Y-%m-%d %H:%M:%S"),
                             "%g" % v, "Working", "-1", ""])
            with open(os.path.join(d, "%s.csv" % name), "w", newline="", encoding="utf-8") as f:
                f.write("# POW-O-METER -- RECONSTRUCTED timestamps, 2025-01-31 to 2025-02-17\n")
                f.write("# The RTC was stopped or failing; these times are interpolated between\n")
                f.write("# %d Iridium transmit-time anchors. Values are as recorded; TIMES ARE NOT.\n" % n_anchor)
                f.write("# Time-series: %s\n# Value units: %s\n# Rows: %d\n#\n" % (label, unit, len(rows)))
                w = csv.writer(f)
                w.writerow(["ISO 8601 UTC", "Timestamp (UTC-08:00)", "Value",
                            "Approval Level", "Grade", "Qualifiers"])
                w.writerows(rows)
    print("\nwritten to %s" % OUT)


if __name__ == "__main__":
    main()
