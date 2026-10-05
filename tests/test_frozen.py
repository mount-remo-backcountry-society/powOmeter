"""Frozen-clock reconstruction (2025-01-31 -> 2025-02-17) with radio anchors.

The original (v0) reference shares a blind spot with the first port: Python
rounding where the firmware rounds half away from zero (code review F3). So
these tests check properties that do not depend on that reference, plus
agreement with it where the anchors did not change.
"""
import csv
import datetime as dt
from pathlib import Path

import pytest

from powometer import frozen, radio, sd

REF = Path(__file__).parent / "fixtures" / "reference_frozen_v0.csv"
VISIT_ROWS = range(607, 935)     # 2025-02-07 visit: rapid power-cycling


@pytest.fixture(scope="module")
def result():
    return frozen.reconstruct(radio.load_all())


def irregular(times, skip=range(600, 741)):
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    return sum(1 for i, g in enumerate(gaps) if i not in skip and not 10 <= g <= 25)


def test_block_and_anchors(result):
    block, times, n_acc = result
    assert len(block) == 1584
    assert n_acc == 250          # v0: 175; firmware rounding and both tie encodings accepted
    assert all(b > a for a, b in zip(times, times[1:]))


def test_more_regular_than_v0(result):
    """Station cadence is ~16.6 min. Outside the visit window the new
    reconstruction has fewer irregular steps than the original (20)."""
    _, times, _ = result
    ref = [dt.datetime.strptime(r["reconstructed_utc"], "%Y-%m-%dT%H:%M:%SZ")
           for r in csv.DictReader(open(REF, encoding="utf-8"))]
    assert irregular(ref) == 20
    assert irregular(times) <= 12


def test_mostly_agrees_with_v0_outside_the_visit(result):
    """Where anchors did not change, times are identical: of the 1,256 rows
    outside the visit window, 1,130 within 1 s and 1,235 within 2 min
    (measured 2026-10-05)."""
    _, times, _ = result
    ref = [dt.datetime.strptime(r["reconstructed_utc"], "%Y-%m-%dT%H:%M:%SZ")
           for r in csv.DictReader(open(REF, encoding="utf-8"))]
    d = [abs((a.replace(microsecond=0) - b).total_seconds())
         for i, (a, b) in enumerate(zip(times, ref)) if i not in VISIT_ROWS]
    assert len(d) == 1256
    assert sum(x <= 1 for x in d) >= 1130
    assert sum(x <= 120 for x in d) >= 1235


def test_rows_on_the_visit_download_predate_the_visit(result):
    """Independent check: the SD download made AT the 2025-02-07 visit holds
    block rows up to index 723, so row 723 must be timed no later than the
    last visit message (19:37:10 UTC) plus a few minutes."""
    block, times, _ = result
    idx = {tuple(p): i for i, p in enumerate(block)}
    meas, _ = sd.parse_log(sd.RAW / "sd" / "2025-02-07" / "LOG.CSV")
    last = max(idx[p] for _, _, p in meas if p in idx)
    assert last == 723
    assert times[last] <= dt.datetime(2025, 2, 7, 19, 45)
