"""SD processing: v0 behaviour reproduced exactly; extensions verified."""
import csv
import datetime as dt
from pathlib import Path

import pytest

from powometer import sd

REF = Path(__file__).parent / "fixtures" / "reference_sd_v0.csv"


@pytest.fixture(scope="module")
def v0():
    return list(csv.DictReader(open(REF, encoding="utf-8")))


@pytest.fixture(scope="module")
def rows_nocarry():
    return [r for r in sd.load(carry_sync=False) if r.true_utc is not None]


@pytest.fixture(scope="module")
def rows_carry():
    return sd.load(carry_sync=True)


def fmt(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_v0_reproduced_exactly(v0, rows_nocarry):
    """Without the extension, the port gives exactly the original output:
    same rows, times, methods, source visits and values."""
    assert len(rows_nocarry) == len(v0) == 41554
    for mine, ref in zip(rows_nocarry, v0):
        assert fmt(mine.true_utc) == ref["true_utc"]
        assert fmt(mine.device_utc) == ref["device_utc"]
        assert mine.time_method == ref["time_method"]
        assert mine.visit == ref["source_visit"]
        p = mine.parts
        assert (p[1], p[3], p[4], p[5], p[6], p[7], p[8], p[9]) == (
            ref["batt_v"], ref["dist_min_mm"], ref["dist_max_mm"], ref["dist_median_mm"],
            ref["air_temp_c"], ref["rh_pct"], ref["baro_temp_c"], ref["pressure_hpa"])


def test_carry_changes_only_previously_unrecoverable_rows(v0, rows_carry):
    """The extension may only ADD rows that v0 could not time; every v0 row
    must come out identical."""
    timed = {fmt(r.true_utc): r for r in rows_carry if r.true_utc is not None}
    for ref in v0:
        r = timed.get(ref["true_utc"])
        assert r is not None and r.time_method == ref["time_method"]
    added = [r for r in rows_carry if r.true_utc is not None and r.time_method == "carried-sync"]
    assert len(added) > 8000          # summer 2026 recovered
    assert len(timed) - len(v0) == len(added)


def test_summer_2026_clock_offset_is_plausible(rows_carry):
    """Carried rows: the station clock ran ~2 days behind (Messages tab:
    -2,862 to -2,882 min in Mar-May 2026). The recovered offset must be in
    that range and constant within RTC drift."""
    carried = [r for r in rows_carry if r.time_method == "carried-sync"]
    offs = {round((r.true_utc - r.device_utc).total_seconds() / 60) for r in carried}
    assert len(offs) == 1
    off = offs.pop()
    assert 2840 <= off <= 2900, off
    first, last = carried[0], carried[-1]
    assert first.true_utc.date() == dt.date(2026, 6, 19)
    assert last.true_utc.date() == dt.date(2026, 9, 26)


def test_no_overlap_or_disorder(rows_carry):
    timed = [r.true_utc for r in rows_carry if r.true_utc is not None]
    assert timed == sorted(timed)
    gaps = [(b - a).total_seconds() for a, b in zip(timed, timed[1:])]
    assert min(gaps) >= 60
