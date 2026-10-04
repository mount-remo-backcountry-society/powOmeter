"""Frozen-clock reconstruction (2025-01-31 -> 2025-02-17) with radio anchors."""
import csv
import datetime as dt
from pathlib import Path

import pytest

from powometer import frozen, radio, sd

REF = Path(__file__).parent / "fixtures" / "reference_frozen_v0.csv"


@pytest.fixture(scope="module")
def result():
    return frozen.reconstruct(radio.load_all())


def test_block_and_anchors(result):
    block, times, n_acc = result
    assert len(block) == 1584
    assert n_acc == 225                     # v0: 175 (sheet lacked 10 anchors)
    assert all(b > a for a, b in zip(times, times[1:]))


def test_agrees_with_v0_outside_the_visit_window(result):
    """Identical to the original (within 1 s) except: rows 607-934, which the
    original mistimed (see frozen.py docstring); and rows 1352-1358, refined
    by at most 60 s by one extra anchor the sheet lacked (2025-02-15 04:34:16)."""
    block, times, _ = result
    ref = [dt.datetime.strptime(r["reconstructed_utc"], "%Y-%m-%dT%H:%M:%SZ")
           for r in csv.DictReader(open(REF, encoding="utf-8"))]
    for i, (a, b) in enumerate(zip(times, ref)):
        if 607 <= i <= 934:
            continue
        tol = 60 if 1352 <= i <= 1358 else 1
        assert abs((a.replace(microsecond=0) - b).total_seconds()) <= tol, i


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
