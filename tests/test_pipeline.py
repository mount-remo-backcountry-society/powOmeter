"""Snow depth, quality control, corrections, approval, hourly values and the
full build. Most tests use small synthetic tables so each rule is checked in
isolation; the last ones run the real build."""
import csv
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from powometer import approval, build, corrections, hourly, qc
from powometer.config import Config, load

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
FIX = Path(__file__).parent / "fixtures"


def cfg(**kw) -> Config:
    base = dict(stations=[{"id": "powometer"}], sites=[], eras=[], no_echo_cm=[498, 499, 500],
                mounts=[{"id": "MX", "site": "s", "from": T0 - timedelta(days=1), "until": None,
                         "reference_m": 4.0, "quality": "good"}],
                visits=[], corrections=[], approvals=[], monitoring={})
    base.update(kw)
    return Config(**base)


def table(values, variable="distance_to_surface", statistic="median", step_min=17):
    return pd.DataFrame({
        "station_id": "powometer", "site_id": "s",
        "time_utc": [T0 + timedelta(minutes=step_min * i) for i in range(len(values))],
        "variable": variable, "statistic": statistic, "value": values,
        "unit": "m", "source": "sd", "time_method": "synced", "message_utc": None, "qualifiers": "",
    })


# --- snow depth and QC ----------------------------------------------------

def test_snow_depth_is_reference_minus_distance_in_whole_cm():
    out = qc.run(table([1.234, 2.0, 3.995]), cfg())
    sd = out[out.variable == "snow_depth"]
    assert list(sd.value) == [277, 200, 0]           # 4.0 - d, cm, rounded
    assert set(sd.unit) == {"cm"} and set(sd.statistic) == {"median"}


def test_min_distance_gives_max_depth():
    out = qc.run(table([1.0], statistic="min"), cfg())
    assert out[out.variable == "snow_depth"].statistic.iloc[0] == "max"


def test_estimate_mount_labels_depth():
    c = cfg(mounts=[{"id": "MX", "site": "s", "from": T0, "until": None, "reference_m": 4.0,
                     "quality": "estimate"}])
    sd = qc.run(table([1.0]), c)
    sd = sd[sd.variable == "snow_depth"].iloc[0]
    assert sd.quality == "estimate" and "reference_estimate" in sd.qualifiers


def test_no_echo_and_out_of_range():
    out = qc.run(table([4.95, 0.1, 2.0]), cfg())
    d = out[out.variable == "distance_to_surface"].reset_index()
    assert d.quality.tolist() == ["missing", "poor", "good"]
    assert "no_echo" in d.qualifiers[0] and pd.isna(d.value[0])
    assert "out_of_range" in d.qualifiers[1]


def test_spike_is_suspect_not_deleted():
    out = qc.run(table([2.0, 2.01, 2.02, 0.9, 2.03, 2.02, 2.01]), cfg())
    d = out[out.variable == "distance_to_surface"].reset_index()
    assert d.quality[3] == "suspect" and "spike" in d.qualifiers[3]
    assert d.value[3] == 0.9 and len(d) == 7


def test_negative_depth_flagged_not_clipped():
    out = qc.run(table([4.30, 4.05]), cfg())        # depths -30, -5 cm
    sd = out[out.variable == "snow_depth"].reset_index()
    assert sd.value.tolist() == [-30, -5]
    assert sd.quality.tolist() == ["suspect", "good"]


def test_dead_temperature_sensor():
    out = qc.run(table([-99.9, 3.2], variable="air_temperature", statistic="point"), cfg())
    assert out.quality.tolist() == ["missing", "good"] and "sensor_fault" in out.qualifiers.iloc[0]


def test_site_visit_window():
    c = cfg(visits=[{"date": T0.date(), "disturbed": {"from": T0 + timedelta(minutes=10),
                                                       "until": T0 + timedelta(minutes=40)}}])
    out = qc.run(table([2.0, 2.0, 2.0, 2.0]), c)
    d = out[out.variable == "distance_to_surface"].reset_index()
    assert d.quality.tolist() == ["good", "suspect", "suspect", "good"]


# --- corrections ----------------------------------------------------------

def corr(**kw):
    c = {"id": "C9", "station": "powometer", "variable": "distance_to_surface",
         "reason": "test", "by": "JK", "date": T0.date()}
    c.update(kw)
    return c


def run_corr(values, *cs):
    df = qc.run(table(values), cfg())
    df = df[df.variable == "distance_to_surface"]
    unmatched = []
    out = corrections.apply(df, cfg(corrections=list(cs)), unmatched)
    return out.reset_index(drop=True), unmatched


def test_delete_keeps_value_marks_poor():
    out, _ = run_corr([2.0, 2.01, 2.02], corr(op="delete", **{"from": T0 + timedelta(minutes=10),
                                                               "until": T0 + timedelta(minutes=30)}))
    assert out.value.tolist() == [2.0, 2.01, 2.02]
    assert out.quality.tolist() == ["good", "poor", "good"]
    assert out.level.tolist() == ["qc", "corrected", "qc"] and "C9" in out.qualifiers[1]


def test_offset_and_drift():
    out, _ = run_corr([1.0, 1.0, 1.0], corr(op="offset", params={"value": 0.5}, **{"from": T0}))
    assert out.value.tolist() == [1.5, 1.5, 1.5]
    out, _ = run_corr([1.0, 1.0, 1.0], corr(op="drift", params={"start": 0, "end": 0.34},
                                            **{"from": T0, "until": T0 + timedelta(minutes=34)}))
    assert out.value.round(3).tolist() == [1.0, 1.17, 1.0]   # until is exclusive


def test_threshold_and_spike_filter():
    out, _ = run_corr([1.0, 9.0, 2.0], corr(op="threshold", params={"min": 0, "max": 5}, **{"from": T0}))
    assert out.quality.tolist() == ["good", "poor", "good"]
    out, _ = run_corr([2.0, 2.0, 3.5, 2.0, 2.0],
                      corr(op="spike_filter", params={"max_step": 0.5, "window": 3}, **{"from": T0}))
    assert out.quality.tolist() == ["good", "good", "poor", "good", "good"]


def test_gap_fill():
    out, _ = run_corr([1.0, 4.95, 2.0], corr(op="gap_fill", params={"max_gap": "1h"}, **{"from": T0}))
    assert out.value.round(3).tolist() == [1.0, 1.5, 2.0]
    assert out.quality[1] == "estimate" and "gap_filled" in out.qualifiers[1]


def test_unmatched_correction_reported():
    _, unmatched = run_corr([1.0], corr(op="delete", message=T0 + timedelta(days=99)))
    assert unmatched == ["C9"]


# --- approval -------------------------------------------------------------

def test_approved_snapshot_published_and_mismatch_alarmed(tmp_path):
    df = qc.run(table([1.0, 2.0, 3.0]), cfg())
    df = df[df.variable == "distance_to_surface"].reset_index(drop=True)
    snap = tmp_path / "approved" / "x.csv"
    sha = approval.write_snapshot(df, "powometer", "distance_to_surface", T0, T0 + timedelta(days=1), snap)
    ap = {"station": "powometer", "variable": "distance_to_surface", "from": T0,
          "until": T0 + timedelta(days=1), "snapshot": "approved/x.csv", "sha256": sha}
    out, alarms = approval.apply(df, cfg(approvals=[ap]), root=tmp_path)
    assert alarms == [] and set(out.approval) == {"approved"}
    changed = df.copy(); changed.loc[1, "value"] = 2.5        # e.g. a later code change
    out, alarms = approval.apply(changed, cfg(approvals=[ap]), root=tmp_path)
    assert len(alarms) == 1 and "differs from its snapshot" in alarms[0]
    assert out.value.tolist() == [1.0, 2.0, 3.0]            # snapshot wins


# --- hourly ---------------------------------------------------------------

def test_hourly_interpolates_points_and_respects_gaps():
    t = [T0 + timedelta(minutes=m) for m in (-30, 30, 50, 70)] + [T0 + timedelta(hours=5)]
    df = pd.DataFrame({"station_id": "powometer", "site_id": "s", "variable": "air_temperature",
                       "statistic": "point", "unit": "degC", "time_utc": t,
                       "value": [0.0, 2.0, 4.0, 6.0, 9.0], "quality": "good",
                       "approval": "working", "level": "qc"})
    h = hourly.to_hourly(df)
    assert h.time_utc.tolist() == [T0, T0 + timedelta(hours=1)]   # 02:00-04:00 have no close neighbours
    assert h.value.round(3).tolist() == [1.0, 5.0]
    assert set(h.qualifiers) == {"interpolated"} and set(h.statistic) == {"point"}


# --- real build -----------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    assert build.build(out) == 0
    return out / "v1"


def test_build_outputs_exist(built):
    for f in ("observations.csv", "best.csv", "best_hourly.csv", "latest.json", "status.json",
              "sites.json", "datapackage.json"):
        assert (built / f).stat().st_size > 0


def test_no_moti_data_published(built):
    stations = {r["station_id"] for r in csv.DictReader(open(built / "best.csv", encoding="utf-8"))}
    assert stations == {"powometer"}


def test_matches_sheet_since_the_move(built):
    """Radio readings since the move: same values as the sheet (which used
    the same decoder), and snow depth within 1 cm (sheet: metres, 3.82 m)."""
    sheet = list(csv.DictReader(open(FIX / "sheet_data_since_2026-09-26.csv", encoding="utf-8")))
    best = [r for r in csv.DictReader(open(built / "best.csv", encoding="utf-8"))
            if r["time_utc"] >= "2026-09-26T20:45" and r["source"] == "radio"]
    by_var = {}
    for r in best:
        by_var.setdefault(r["variable"], []).append(float(r["value"]))
    assert len(by_var["snow_depth"]) == len(sheet)
    sheet_depth = sorted(round(float(r["snow_depth_m"]) * 100) for r in sheet)
    assert max(abs(a - b) for a, b in zip(sorted(by_var["snow_depth"]), sheet_depth)) <= 1
    assert sorted(by_var["air_temperature"]) == sorted(float(r["air_temp_c"]) for r in sheet)


def test_rebuild_is_identical(built, tmp_path):
    assert build.build(tmp_path) == 0
    for f in built.iterdir():
        a, b = f.read_bytes(), (tmp_path / "v1" / f.name).read_bytes()
        if f.name == "status.json":
            a, b = (x.decode().split('"built_utc"')[0] for x in (a, b))
            assert a == b
        else:
            assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest(), f.name
