#!/usr/bin/env python3
"""Build tests/fixtures/reference_sd_v0.csv from the ORIGINAL processing code.

This freezes what the pre-pipeline script (`scripts/clean_sd_data.py`, as it
stood on 2026-10-04) produces from the SD downloads, so the new `powometer`
package can be tested against it. It calls the original functions unchanged.

One row per final record (after the original de-duplication and deployment
cut-off): true UTC time, device time, how the time was recovered, and the
measured values. Snow depth is deliberately NOT included: the original used a
sensor-height table that phase 1 replaces with field-visit calibration.

Usage (from the repo root):  python tests/fixtures/build_reference_v0.py
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import clean_sd_data as C  # noqa: E402

OUT = os.path.join(ROOT, "tests", "fixtures", "reference_sd_v0.csv")


def main():
    logs = []
    for visit in sorted(os.listdir(C.VISITS)):
        for fn in ("LOG.CSV", "LOG 2.CSV"):
            p = os.path.join(C.VISITS, visit, fn)
            if os.path.exists(p):
                logs.append((visit, p))
    merged = {}
    rank_of = {"synced": 5, "era-offset": 4, "clock-was-correct": 3, "extrapolated": 2,
               "frozen-clock": 1, "unrecoverable": 0}
    for visit, path in logs:
        meas, syncs = C.parse_log(path)
        for dev, true_utc, parts, quality in C.correct(meas, syncs):
            key = (parts[0], parts[6], parts[9])
            rank = rank_of.get(quality, 0)
            prev = merged.get(key)
            if prev is None or rank > prev[3]:
                merged[key] = (dev, true_utc, parts, rank, quality, visit)

    recs = sorted((r for r in merged.values() if r[1] is not None), key=lambda r: r[1])
    recs = [r for r in recs if r[1] >= C.DEPLOYMENT_START]
    final = []
    for r in recs:
        if final and abs((r[1] - final[-1][1]).total_seconds()) < 60:
            continue
        final.append(r)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["true_utc", "device_utc", "time_method", "source_visit",
                    "batt_v", "dist_min_mm", "dist_max_mm", "dist_median_mm",
                    "air_temp_c", "rh_pct", "baro_temp_c", "pressure_hpa"])
        for dev, true_utc, parts, _rank, quality, visit in final:
            w.writerow([true_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), dev.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        quality, visit, parts[1], parts[3], parts[4], parts[5],
                        parts[6], parts[7], parts[8], parts[9]])
    print(f"wrote {OUT}: {len(final)} rows, {final[0][1]} .. {final[-1][1]}")


if __name__ == "__main__":
    main()
