"""Nearby stations (Avalanche Canada API): decoder, raw intake, quality
control, outputs. All values here are MADE UP in the API's format; no real
third-party data is needed in tests/."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from powometer import build, nearby
from powometer.config import load
from powometer.decoders import avalanche_canada as ac

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
STATIONS = {"avcan_56": 56, "avcan_57": 57, "avcan_58": 58}


def cfg_on(**kw):
    """The real config with the three nearby stations switched on."""
    c = load()
    for s in c.stations:
        if s["id"] in STATIONS:
            s.update(enabled=True, publish=True)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def row(n, hours, **values):
    return {"stationId": n, "measurementDateTime": (T0 + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "windDirCompass": "N", **values}


def records(rows_by_station, fetched=T0):
    return [{"station_id": sid, "fetched_utc": fetched.strftime("%Y-%m-%dT%H:%M:%SZ"), "row": r}
            for sid, rows in rows_by_station.items() for r in rows]


def temps(t56, t57, t58, hours=24):
    """Hourly temperatures; each argument a function of the hour."""
    return {"avcan_56": [row(56, h, airTempAvg=t56(h)) for h in range(hours)],
            "avcan_57": [row(57, h, airTempAvg=t57(h)) for h in range(hours)],
            "avcan_58": [row(58, h, airTempAvg=t58(h), hourlyPrecip=0.4) for h in range(hours)]}


def q(df, station, variable="air_temperature"):
    return df[(df.station_id == station) & (df.variable == variable)].sort_values("time_utc")


# --- decoder ----------------------------------------------------------------

def test_decoder_maps_fields_and_reports_unknown_ones():
    r = row(58, 0, airTempAvg=-3.2, hourlyPrecip=1.2, windSpeedAvg=12, windDirAvg=250, snowHeight=None,
            relativeHumidity=88)
    got = {(v["variable"], v["statistic"], v["unit"]): v["value"] for v in ac.decode(r)}
    assert got == {("air_temperature", "mean", "degC"): -3.2, ("precipitation_amount", "sum", "mm"): 1.2,
                   ("wind_speed", "mean", "km/h"): 12.0, ("wind_from_direction", "mean", "degree"): 250.0}
    assert ac.unmapped(r) == {"relativeHumidity"}             # snowHeight null: not reported
    assert ac.decode(r)[0]["time_utc"] == T0


def test_decoder_refuses_a_time_without_zone():
    with pytest.raises(ValueError):
        ac.decode({"measurementDateTime": "2026-12-01T00:00:00", "airTempAvg": 1})


# --- raw intake -----------------------------------------------------------------

def test_fetch_archives_only_new_rows_in_new_files(tmp_path):
    c = cfg_on()
    api = {56: [row(56, 0, airTempAvg=1.0)], 57: [], 58: [row(58, 0, airTempAvg=5.0)]}
    get = lambda url: api[int(url.split("/stations/")[1].split("/")[0])]
    path, report = nearby.fetch(c, tmp_path, T0, get)
    assert path.name == "2026-12-01T000000Z_avcan-api.jsonl"
    assert report["avcan_56"] == {"ok": True, "rows": 1, "new": 1}
    assert nearby.fetch(c, tmp_path, T0 + timedelta(hours=1), get)[0] is None       # nothing new
    api[58] = [row(58, 1, airTempAvg=4.0), row(58, 0, airTempAvg=5.5)]             # new hour + a revision
    path2, _ = nearby.fetch(c, tmp_path, T0 + timedelta(hours=2), get)
    assert path2 != path and len(path2.read_text().splitlines()) == 2
    df, _ = nearby.readings(c, nearby.load_raw(tmp_path), set(STATIONS))
    assert q(df, "avcan_58")["value"].tolist() == [5.5, 4.0]                        # newest fetch wins


def test_a_failing_station_does_not_stop_the_others(tmp_path):
    def get(url):
        if "/58/" in url:
            raise TimeoutError("no answer")
        return [row(int(url.split("/stations/")[1].split("/")[0]), 0, airTempAvg=1.0)]
    path, report = nearby.fetch(cfg_on(), tmp_path, T0, get)
    assert report["avcan_58"]["ok"] is False and "TimeoutError" in report["avcan_58"]["error"]
    assert len(path.read_text().splitlines()) == 2


def test_disabled_stations_are_not_fetched(tmp_path):
    c = load()
    for s in c.stations:
        if s["id"] in STATIONS:
            s.update(enabled=False, publish=False)
    called = []
    nearby.fetch(c, tmp_path, T0, lambda url: called.append(url) or [])
    assert called == []


def test_moti_licence_meets_the_permission():
    """docs/permissions/moti-2026-10.md: credit the Ministry, and say it is not liable."""
    for s in load().stations:
        if s["id"] in STATIONS and s["publish"]:
            assert s["permission"] == "docs/permissions/moti-2026-10.md"
            assert "Ministry of Transportation & Infrastructure" in s["licence"]
            assert "not liable" in s["licence"]


def test_raw_files_are_never_overwritten(tmp_path):
    c = cfg_on()
    nearby.fetch(c, tmp_path, T0, lambda url: [row(56, 0, airTempAvg=1.0)])
    with pytest.raises(FileExistsError):
        nearby.fetch(c, tmp_path, T0, lambda url: [row(56, 1, airTempAvg=1.0)])


# --- quality control ----------------------------------------------------------

def test_kasiks_high_fault_is_flagged_by_its_neighbours():
    """Like 2026-09-25: Kasiks High reads -25 to -49 while the others read
    about +5. Labels only: the values stay."""
    fault = lambda h: 4.0 if h < 12 else -25.0 - 2 * (h - 12)
    df, *_ = nearby.process(cfg_on(), records(temps(fault, lambda h: 6.0 + h / 10, lambda h: 7.0 - h / 10)))
    k = q(df, "avcan_56")
    assert (k.quality.iloc[:12] == "good").all()
    assert (k.quality.iloc[12:] == "suspect").all() and k.qualifiers.iloc[12:].str.contains("neighbour").all()
    assert k.value.iloc[-1] == -47.0
    assert (q(df, "avcan_57").quality == "good").all() and (q(df, "avcan_58").quality == "good").all()


def test_inversion_is_not_flagged():
    """Winter inversion: valleys at -20, Kasiks High (1435 m) at -5. Not a fault."""
    df, *_ = nearby.process(cfg_on(), records(temps(lambda h: -5.0 + 0.1 * h, lambda h: -20.0 - 0.1 * h,
                                                    lambda h: -19.0 + 0.05 * h)))
    assert (df[df.variable == "air_temperature"].quality == "good").all()


def test_powometer_counts_as_a_neighbour():
    """With only one other nearby station, POW-O-METER's hourly series
    supplies the second reference."""
    rec = records({"avcan_56": [row(56, h, airTempAvg=-40.0) for h in range(6)],
                   "avcan_57": [row(57, h, airTempAvg=5.0 + h / 10) for h in range(6)]})
    own = pd.DataFrame({"station_id": "powometer", "site_id": "shames_top_b",
                        "time_utc": [T0 + timedelta(hours=h) for h in range(6)],
                        "variable": "air_temperature", "value": 3.0, "quality": "good"})
    df, *_ = nearby.process(cfg_on(), rec, own)
    assert (q(df, "avcan_56").quality == "suspect").all()
    df, *_ = nearby.process(cfg_on(), rec)                       # one neighbour only: no verdict
    assert (q(df, "avcan_56").quality == "good").all()


def test_spike_stuck_and_range():
    t = lambda h: 30.0 if h == 10 else -2.0 + 0.2 * h
    rec = records({"avcan_57": [row(57, h, airTempAvg=t(h)) for h in range(24)],
                   "avcan_58": [row(58, h, airTempAvg=1.5, windSpeedAvg=300 if h == 3 else 10)
                                for h in range(24)]})
    df, *_ = nearby.process(cfg_on(), rec)
    k57 = q(df, "avcan_57")
    assert k57[k57.qualifiers.str.contains("spike")].value.tolist() == [30.0]
    assert q(df, "avcan_58").qualifiers.str.contains("stuck").all()           # 1.5 for 23 h
    w = q(df, "avcan_58", "wind_speed")
    assert w[w.quality == "poor"].value.tolist() == [300.0]


def test_corrections_apply_to_nearby_stations():
    c = cfg_on()
    c.corrections = [{"id": "C900", "op": "delete", "station": "avcan_57", "variable": "air_temperature",
                      "from": T0 + timedelta(hours=2), "until": T0 + timedelta(hours=4),
                      "reason": "test", "by": "JK", "date": "2026-12-02"}]
    df, matched, *_ = nearby.process(c, records(temps(lambda h: 0.0 + h / 10, lambda h: 1.0 + h / 10,
                                                      lambda h: 2.0 + h / 10)))
    assert matched == {"C900"}
    assert q(df, "avcan_57").quality.tolist().count("poor") == 2


def test_unpublished_stations_are_left_out():
    c = load()
    for s in c.stations:
        if s["id"] in STATIONS:
            s.update(enabled=False, publish=False)
    df, *_ = nearby.process(c, records(temps(lambda h: 0.0, lambda h: 0.0, lambda h: 0.0)))
    assert df.empty


# --- outputs --------------------------------------------------------------------

def test_latest_and_health():
    c = cfg_on()
    rec = records(temps(lambda h: -3.0, lambda h: 1.0, lambda h: 2.0 + h / 10, hours=5))
    df, *_ = nearby.process(c, rec)
    lat = nearby.latest(df, build._fmt)
    v = lat["avcan_58"]["values"]["air_temperature"]
    assert v == {"time_utc": "2026-12-01T04:00:00Z", "value": 2.4, "unit": "degC", "statistic": "mean",
                 "quality": "good", "approval": "working", "site_id": "shames_bottom"}
    assert lat["avcan_58"]["values"]["precipitation_amount"]["statistic"] == "sum"
    h = nearby.health(c, rec, df)
    assert h["avcan_56"] == {"last_observation_utc": "2026-12-01T04:00:00Z",
                             "last_new_data_fetched_utc": "2026-12-01T00:00:00Z"}


def test_recent_is_anchored_to_the_newest_reading():
    df = pd.DataFrame({"time_utc": [T0 - timedelta(days=d) for d in (40, 29, 0)], "value": [1, 2, 3]})
    assert build.recent(df)["value"].tolist() == [2, 3]
    assert build.recent(df.iloc[0:0]).empty


def test_small_negative_precipitation_is_noise_not_poor():
    rec = records({"avcan_58": [row(58, h, hourlyPrecip=v) for h, v in enumerate([0.4, -0.2, -3.0, 1.0])]})
    p = q(nearby.process(cfg_on(), rec)[0], "avcan_58", "precipitation_amount")
    assert p.quality.tolist() == ["good", "suspect", "poor", "good"]
    assert p.value.tolist() == [0.4, -0.2, -3.0, 1.0]                     # never clipped
    assert "negative_amount" in p.qualifiers.iloc[1]
