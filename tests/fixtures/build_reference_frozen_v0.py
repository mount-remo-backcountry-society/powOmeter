#!/usr/bin/env python3
"""Build tests/fixtures/reference_frozen_v0.csv from the ORIGINAL reconstruction.

Freezes the output of `scripts/reconstruct_frozen_period.py` (2026-10-04) for
the frozen-clock window 2025-01-31 -> 2025-02-17: reconstructed UTC time and
the measured values for each of the 1,584 SD rows. The original took its
satellite anchors from a local Google Sheet export; the new pipeline uses
raw/radio instead, so this fixture lets the two be compared.

Prerequisite: run scripts/reconstruct_frozen_period.py first (it writes
data/Cleaned/Reconstructed_2025-01-31_to_02-17/, which is git-ignored).
Usage: python tests/fixtures/build_reference_frozen_v0.py
"""
import csv, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "data", "Cleaned", "Reconstructed_2025-01-31_to_02-17", "UTC")
OUT = os.path.join(ROOT, "tests", "fixtures", "reference_frozen_v0.csv")
COLS = [("Distance_min", "dist_min_m"), ("Distance_max", "dist_max_m"), ("Distance_median", "dist_median_m"),
        ("Air_Temp", "air_temp_c"), ("Rel_Humidity", "rh_pct"), ("Atmos_Pressure", "pressure_hpa"),
        ("Battery_Voltage", "batt_v")]
rows = {}
for fname, col in COLS:
    with open(os.path.join(SRC, fname + ".csv"), encoding="utf-8") as f:
        lines = [l for l in f if not l.startswith("#")]
    for r in csv.DictReader(lines):
        rows.setdefault(r["ISO 8601 UTC"], {})[col] = r["Value"]
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["reconstructed_utc"] + [c for _, c in COLS])
    for t in sorted(rows):
        w.writerow([t] + [rows[t].get(c, "") for _, c in COLS])
print(f"wrote {OUT}: {len(rows)} rows")
