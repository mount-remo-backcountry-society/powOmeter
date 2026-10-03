#!/usr/bin/env python3
"""
POW-O-METER -- rebuild a clean, timestamp-corrected record from the SD card
downloads, ready for Aquarius.

WHY THIS IS DRIVEN BY LOG.CSV, NOT DATA.CSV
    LOG.CSV contains every "Data:" row *and* every clock-sync event, in true
    append order. That lets each measurement be tied to the clock state that
    was actually in force when it was taken. DATA.CSV alone cannot do this:
    after a backward clock jump the same device timestamp occurs twice, and
    sorting by it silently interleaves rows from different days.

HOW THE CORRECTION WORKS
    Each sync logs the value written to the RTC and (via "TIME FROM MODEM")
    the raw Iridium decode. Iridium switched ERA2 -> ERA3 on 2026-01-14, and
    IridiumSBD decodes with the ERA2 epoch, so the true time is the decode
    plus exactly 3932 d 03:50:22. Between syncs the RTC free-runs at about
    2 s/day, which is negligible, so:

        true_utc(row) = true_at_last_sync + (device_utc(row) - written_at_last_sync)

    The error is applied PER SYNC, not per episode. Within each episode it
    alternates by ~17 min because the Jan-23 patch computed `t.tm_min - 9`,
    which wraps a uint8_t to 250, and bin2bcd(250) overflows to 0x90, read
    back as minute 10. Averaging over an episode would smear every timestamp.

Usage:  python clean_sd_data.py
Output: data/Cleaned/  (see README written alongside the CSVs)
"""

import os, re, csv, sys, math, datetime as dt
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISITS = os.path.join(ROOT, "data", "Site Visits")
OUTDIR = os.path.join(ROOT, "data", "Cleaned")

# ERA3 epoch minus ERA2 epoch -- the constant IridiumSBD was wrong by.
ERA_OFFSET = dt.timedelta(days=3932, hours=3, minutes=50, seconds=22)
PST = dt.timedelta(hours=8)          # station uses fixed UTC-8 year round

PLAUSIBLE = (dt.datetime(2024, 1, 1), dt.datetime(2028, 1, 1))
ERA_SWITCH = dt.datetime(2026, 1, 14, 18, 8)   # network ERA2 -> ERA3 swap, UTC

# The station reached the mountain here. Barometric pressure steps 1023.8 -> 894.1
# hPa at this instant (sea level -> ~1100 m), and the Field sheet's first site
# reference is 2025-01-26 13:00 PST the same day. Rows before this are bench and
# transit readings, not station data, so they are written to a separate folder
# rather than into the series you would upload.
DEPLOYMENT_START = dt.datetime(2025, 1, 26, 18, 58, 34)

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}), (.*)$")
TS   = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")
TFM  = re.compile(r"TIME FROM MODEM: (-?\d+)-(-?\d+)-(-?\d+) (-?\d+):(-?\d+)")

FIELDS = ["datetime", "batt_v", "memory", "snow_depth_min_mm", "snow_depth_max_mm",
          "snow_depth_median_mm", "air_2m_temp_deg_c", "air_2m_temp_rh_prct",
          "baro_temp_deg_c", "baro_pressure_hPa"]


def loose_dt(m):
    """Parse a timestamp that may carry impossible fields (hour 26, day 32),
    which the Jan-23 patch really did write into the RTC."""
    y, mo, d, hh, mi, ss = (int(x) for x in m.groups())
    mo = max(1, min(12, mo))
    return (dt.datetime(y, mo, 1)
            + dt.timedelta(days=d - 1, hours=hh, minutes=mi, seconds=ss))


def parse_log(path):
    """Return (measurements, syncs) from one LOG.CSV, in file order."""
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
                        meas.append((len(meas), loose_dt(g) + PST, parts))  # -> device UTC

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
                            # a decode already in the plausible range predates the
                            # ERA switch and needs no offset
                            true = L if L.year >= 2020 else L + ERA_OFFSET
                    else:
                        # v1.1 wrote the decode straight to the RTC
                        true = written + ERA_OFFSET if written.year < 2020 else written
                    if true and PLAUSIBLE[0] <= true < PLAUSIBLE[1]:
                        syncs.append((len(meas), written, true))
                pending = {}
    return meas, syncs


def correct(meas, syncs):
    """Attach a true UTC timestamp to each measurement.

    Priority: (1) the clock-sync record in force at the time, (2) the ERA
    offset for rows written while IridiumSBD was decoding with the wrong
    epoch, (3) the identity, for rows taken before the ERA switch when the
    clock was demonstrably correct. Rows a frozen or failed RTC touched are
    left unrecoverable rather than guessed at.
    """
    import bisect
    out = []
    starts = [s[0] for s in syncs]

    # a clock that never advances is stopped, not slow -- do not trust those rows
    frozen = set()
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
        true_utc, quality = None, None

        if syncs:
            j = bisect.bisect_right(starts, idx) - 1
            q = "extrapolated" if j < 0 else "synced"
            j = max(j, 0)
            _, written, true = syncs[j]
            cand = true + (dev - written)
            if PLAUSIBLE[0] <= cand < PLAUSIBLE[1]:
                true_utc, quality = cand, q

        if true_utc is None and dev.year < 2020:
            # RTC holds a raw ERA2 decode of an ERA3 tick count
            cand = dev + ERA_OFFSET
            if PLAUSIBLE[0] <= cand < PLAUSIBLE[1]:
                true_utc, quality = cand, "era-offset"

        if true_utc is None and PLAUSIBLE[0] <= dev < PLAUSIBLE[1] and dev < ERA_SWITCH:
            # before the ERA switch the decode was correct, so the RTC was right
            true_utc, quality = dev, "clock-was-correct"

        if pos in frozen:
            true_utc, quality = None, "frozen-clock"

        out.append((dev, true_utc, parts, quality or "unrecoverable"))
    return out


# --------------------------------------------------------------------------- QC
GARBAGE = 1e6            # anything this large is float corruption, not a reading
DIST_MIN_MM, DIST_MAX_MM = 300.0, 5200.0
NO_TARGET_LO, NO_TARGET_HI = 4900.0, 5200.0   # sensor's own no-return band


def qc_row(parts, flags):
    """Return a dict of cleaned values; None means 'no data' for that parameter."""
    v = {}
    def num(i):
        try:
            x = float(parts[i])
        except (ValueError, TypeError):
            return None
        return None if (math.isnan(x) or math.isinf(x) or abs(x) > GARBAGE) else x

    v["batt_v"] = num(1)
    if v["batt_v"] is not None and not (2.0 <= v["batt_v"] <= 5.0):
        v["batt_v"] = None; flags.add("batt_out_of_range")

    for key, i in (("dist_min", 3), ("dist_max", 4), ("dist_median", 5)):
        x = num(i)
        if x is None:
            flags.add("distance_garbage")
        elif NO_TARGET_LO <= x <= NO_TARGET_HI:
            x = None; flags.add("no_target")
        elif not (DIST_MIN_MM <= x <= DIST_MAX_MM):
            x = None; flags.add("distance_out_of_range")
        v[key] = x

    t = num(6)
    if t is not None and (t <= -99.0 or not (-60.0 <= t <= 50.0)):
        t = None; flags.add("air_temp_invalid")
    v["air_temp"] = t

    rh = num(7)
    if rh is not None and not (0.0 <= rh <= 100.0):
        rh = None; flags.add("humidity_out_of_range")
    v["humidity"] = rh

    bt = num(8)
    if bt is not None and not (-60.0 <= bt <= 80.0):
        bt = None; flags.add("baro_temp_out_of_range")
    v["baro_temp"] = bt

    bp = num(9)
    if bp is not None and not (500.0 <= bp <= 1100.0):
        bp = None; flags.add("pressure_out_of_range")
    v["pressure"] = bp
    return v


# ------------------------------------------------------------------- sensor height
# From the Google Sheet "Field" tab. Height above ground, metres, effective from
# the given date. Re-measure on the next visit: the archive sits on a -0.54 m
# floor at bare ground, which suggests these are about half a metre too small.
HEIGHTS = [(dt.datetime(2025, 1, 26, 21, 0), 4.10),   # 13:00 PST -> UTC
           (dt.datetime(2025, 9, 30, 17, 0), 4.45),   # 09:00 PST
           (dt.datetime(2026, 3, 7, 19, 30), 5.09)]   # 11:30 PST


def height_at(t):
    h = None
    for when, val in HEIGHTS:
        if t >= when:
            h = val
    return h


def main():
    logs = []
    for visit in sorted(os.listdir(VISITS)):
        for fn in ("LOG.CSV", "LOG 2.CSV"):
            p = os.path.join(VISITS, visit, fn)
            if os.path.exists(p):
                logs.append((visit, fn, p))

    merged = {}
    stats = Counter()
    for visit, fn, path in logs:
        meas, syncs = parse_log(path)
        stats["log_files"] += 1
        stats["sync_events"] += len(syncs)
        for dev, true_utc, parts, quality in correct(meas, syncs):
            key = (parts[0], parts[6], parts[9])      # device ts + temp + pressure
            rank = {"synced": 5, "era-offset": 4, "clock-was-correct": 3, "extrapolated": 2,
                    "frozen-clock": 1, "unrecoverable": 0}.get(quality, 0)
            prev = merged.get(key)
            if prev is None or rank > prev[3]:
                merged[key] = (dev, true_utc, parts, rank, quality, visit)
            stats["rows_seen"] += 1

    print("LOG files parsed        : %d" % stats["log_files"])
    print("clock-sync events       : %d" % stats["sync_events"])
    print("measurement rows seen   : %d" % stats["rows_seen"])
    print("unique measurements     : %d" % len(merged))

    recs, qcount, unrec = [], Counter(), []
    for dev, true_utc, parts, rank, quality, visit in merged.values():
        flags = set()
        if true_utc is None:
            unrec.append((parts[0], visit))
            qcount["unrecoverable timestamp"] += 1
            continue
        qcount[quality] += 1
        vals = qc_row(parts, flags)
        for f in flags:
            qcount["flag:" + f] += 1
        recs.append((true_utc, dev, vals, quality, flags))

    recs.sort(key=lambda r: r[0])
    pre = [r for r in recs if r[0] < DEPLOYMENT_START]
    recs = [r for r in recs if r[0] >= DEPLOYMENT_START]
    qcount["pre-deployment (excluded from series)"] = len(pre)

    # collapse duplicate corrected timestamps (the TPL5110 "Power down failed"
    # double-cycles produce two rows seconds apart)
    deduped, dropped_dupes = [], 0
    for r in recs:
        if deduped and abs((r[0] - deduped[-1][0]).total_seconds()) < 60:
            dropped_dupes += 1
            continue
        deduped.append(r)

    print("\ncorrected + QC'd        : %d" % len(recs))
    print("near-duplicates dropped : %d  (<60 s apart after correction)" % dropped_dupes)
    print("final rows              : %d" % len(deduped))
    print("span (true UTC)         : %s -> %s" % (deduped[0][0], deduped[-1][0]))
    print()
    for k, v in sorted(qcount.items(), key=lambda x: -x[1]):
        print("   %-34s %6d" % (k, v))

    write_outputs(deduped, unrec, qcount, dropped_dupes, pre)


SERIES = [
    ("Air_Temp",            "air_temp",    "degC", "Air Temp"),
    ("Air_Temp_enclosure",  "baro_temp",   "degC", "Air Temp (enclosure)"),
    ("Atmos_Pressure",      "pressure",    "hPa",  "Atmos Pressure"),
    ("Rel_Humidity",        "humidity",    "%",    "Rel. Humidity"),
    ("Battery_Voltage",     "batt_v",      "V",    "Battery Voltage"),
    ("Distance_min",        "dist_min",    "m",    "Distance to target (min of burst)"),
    ("Distance_max",        "dist_max",    "m",    "Distance to target (max of burst)"),
    ("Distance_median",     "dist_median", "m",    "Distance to target (median of burst)"),
    ("Snow_Depth_max",      "_depth_max",  "m",    "Snow depth (deepest: height - min distance)"),
    ("Snow_Depth_min",      "_depth_min",  "m",    "Snow depth (shallowest: height - max distance)"),
    ("Snow_Depth_median",   "_depth_med",  "m",    "Snow depth (height - median distance)"),
]


def value_for(key, vals, t):
    if key == "air_temp":   return vals["air_temp"]
    if key == "baro_temp":  return vals["baro_temp"]
    if key == "pressure":   return vals["pressure"]
    if key == "humidity":   return vals["humidity"]
    if key == "batt_v":     return vals["batt_v"]
    if key.startswith("dist_"):
        x = vals[key]
        return None if x is None else round(x / 1000.0, 4)     # mm -> m
    h = height_at(t)
    if h is None: return None
    src = {"_depth_max": "dist_min", "_depth_min": "dist_max", "_depth_med": "dist_median"}[key]
    x = vals[src]
    return None if x is None else round(h - x / 1000.0, 4)


def write_outputs(recs, unrec, qcount, dropped_dupes, pre=()):
    variants = [("UTC", recs), ("DevicePST_matches_existing_archive", recs)]
    if pre:
        variants.append(("Excluded_pre_deployment", pre))
    for variant, source in variants:
        d = os.path.join(OUTDIR, variant)
        os.makedirs(d, exist_ok=True)
        shift = -PST if variant == "DevicePST_matches_existing_archive" else dt.timedelta(0)
        for name, key, unit, label in SERIES:
            rows = []
            for t, dev, vals, quality, flags in source:
                v = value_for(key, vals, t)
                if v is None:
                    continue
                ts = t + shift
                rows.append((ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             (ts - PST).strftime("%Y-%m-%d %H:%M:%S"),
                             ("%g" % v)))
            p = os.path.join(d, "%s.csv" % name)
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                f.write("# POW-O-METER rebuilt from SD card downloads by scripts/clean_sd_data.py\n")
                f.write("# Time-series: %s\n" % label)
                f.write("# Value units: %s\n" % unit)
                f.write("# Timestamp basis: %s\n" % (
                    "true UTC (device PST + 8 h)" if variant == "UTC"
                    else "device PST written into the UTC column -- matches the EXISTING archive, which is 8 h off true UTC"))
                f.write("# Rows: %d\n" % len(rows))
                f.write("#\n")
                w.writerow(["ISO 8601 UTC", "Timestamp (UTC-08:00)", "Value",
                            "Approval Level", "Grade", "Qualifiers"])
                for a, b, c in rows:
                    w.writerow([a, b, c, "Working", "-1", ""])
    # QC report
    with open(os.path.join(OUTDIR, "QC_REPORT.txt"), "w", encoding="utf-8") as f:
        f.write("POW-O-METER -- SD rebuild QC report\n")
        f.write("generated %s\n\n" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        f.write("final rows: %d\nspan: %s -> %s\n\n" % (len(recs), recs[0][0], recs[-1][0]))
        f.write("near-duplicate rows dropped (<60 s apart after correction): %d\n\n" % dropped_dupes)
        for k, v in sorted(qcount.items(), key=lambda x: -x[1]):
            f.write("  %-36s %6d\n" % (k, v))
        f.write("\nRows whose timestamp could not be recovered (%d):\n" % len(unrec))
        c = Counter(u[0][:7] for u in unrec)
        for k, v in sorted(c.items()):
            f.write("  device month %s : %d rows\n" % (k, v))
    print("\nwritten to %s" % OUTDIR)


if __name__ == "__main__":
    main()
