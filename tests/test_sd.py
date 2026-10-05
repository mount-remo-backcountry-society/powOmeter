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


# --- reading downloads (code review F7, F8) -------------------------------

def data_line(t: dt.datetime, depth_mm=2000, temp="-3.21"):
    ts = t.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{ts}, Data: {ts},4.05,1,{depth_mm},{depth_mm + 10},{depth_mm + 5},{temp},88.50,1.00,870.12"


def write_raw(tmp_path):
    """One download: a lower-case log.csv, and a zip holding LOG 2.CSV with
    one unreadable Data line."""
    import zipfile
    d = tmp_path / "sd" / "2025-03-01"
    d.mkdir(parents=True)
    t0 = dt.datetime(2025, 3, 1, 12, 0)
    first = [data_line(t0 + dt.timedelta(minutes=17 * i)) for i in range(3)]
    (d / "log.csv").write_text("\n".join(first) + "\n", encoding="utf-8")
    second = [data_line(t0 + dt.timedelta(hours=2, minutes=17 * i), temp=f"-{i}.50") for i in range(3)]
    second.append("2025-03-01T15:00:00, Data: 2025-03-01T15:00:00,4.05,truncated")
    with zipfile.ZipFile(d / "card.zip", "w") as z:
        z.writestr("LOG 2.CSV", "\n".join(second) + "\n")
        z.writestr("DATA.CSV", "ignored\n")
    return tmp_path


def test_lower_case_and_zipped_logs_are_read(tmp_path):
    raw = write_raw(tmp_path)
    assert [(v, s.name) for v, s in sd.log_files(raw)] == \
        [("2025-03-01", "log.csv"), ("2025-03-01", "card.zip/LOG 2.CSV")]
    report = []
    rows = sd.load(raw, report=report)
    assert len([r for r in rows if r.true_utc]) == 6
    assert [(r["file"], r["data_lines"], r["rejected_lines"]) for r in report] == \
        [("log.csv", 3, 0), ("card.zip/LOG 2.CSV", 4, 1)]


def test_unreadable_lines_raise_an_alarm(tmp_path):
    from powometer.build import sd_alarms
    report = []
    sd.load(write_raw(tmp_path), report=report)
    alarms = sd_alarms(report)
    assert len(alarms) == 1 and "1 of 4 Data lines unreadable" in alarms[0]
    assert sd_alarms([{"download": "x", "file": "LOG.CSV", "data_lines": 5, "rejected_lines": 5}])[0] \
        .endswith("no readable measurements (5 Data lines)")


def test_carried_offset_is_the_median_of_recent_syncs():
    """F8: one bad modem time at the end of a log must not shift the next
    log's whole clock."""
    w = dt.datetime(2026, 5, 1)
    good = [(i, w + dt.timedelta(hours=i), w + dt.timedelta(hours=i, minutes=2879)) for i in range(4)]
    bad = (9, w + dt.timedelta(hours=9), w + dt.timedelta(hours=9, minutes=2879 + 600))
    written, true = sd._clock_state(good + [bad])
    assert written == bad[1] and (true - written) == dt.timedelta(minutes=2879)
