"""Firmware rounding and the SD/radio merge (code review F3, F10)."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from powometer import assemble
from powometer.firmware import fw_round, reading_codes

T0 = datetime(2025, 10, 19, 8, 56, 29, tzinfo=timezone.utc)


def test_fw_round_half_away_from_zero():
    assert fw_round(250.5) == 251 and fw_round(-0.5) == -1 and fw_round(2.4) == 2
    assert fw_round(2.505 * 100) == 251            # binary error must not decide
    assert fw_round(0.05 * 10) == 1


def test_tie_values_accept_both_encodings():
    # SD keeps two decimals: -0.25 C may have been -0.2512 or -0.2488
    assert reading_codes(4.449, -0.25, 98.94) == {(445, -3, 99), (445, -2, 99)}
    assert reading_codes(2.5, 3.21, 86.5) == {(250, 32, 86), (250, 32, 87)}
    assert reading_codes(2.505, 1.0, 99.6) == {(251, 10, 0)}       # 100 % sent as 0
    assert reading_codes(None, 1.0, 50.0) == set()


def sd_rows(t, d_min, temp, rh):
    base = dict(station_id="powometer", site_id="s", time_utc=t, source="sd",
                time_method="synced", message_utc=None, qualifiers="")
    return [{**base, "variable": "distance_to_surface", "statistic": "min", "value": d_min, "unit": "m"},
            {**base, "variable": "air_temperature", "statistic": "point", "value": temp, "unit": "degC"},
            {**base, "variable": "relative_humidity", "statistic": "point", "value": rh, "unit": "%"}]


def radio_rows(t, d_cm, t10, rh, msg):
    base = dict(station_id="powometer", site_id="s", time_utc=t, source="radio",
                time_method="transmit time (station clock off by -63 min)",
                message_utc=msg, qualifiers="timed_by_transmit")
    return [{**base, "variable": "distance_to_surface", "statistic": "min", "value": d_cm / 100, "unit": "m"},
            {**base, "variable": "air_temperature", "statistic": "point", "value": t10 / 10, "unit": "degC"},
            {**base, "variable": "relative_humidity", "statistic": "point", "value": rh, "unit": "%"}]


def radio_kept(sd, rad):
    m = assemble.merge(sd, rad)
    return m[m.source == "radio"].time_utc.nunique()


def test_same_reading_on_a_tie_is_a_duplicate():
    # the real 2025-10-19 case: SD -0.25 C, radio -0.2 C, 62 min apart
    sd = sd_rows(T0, 4.449, -0.25, 98.94)
    rad = radio_rows(T0 + timedelta(minutes=62), 445, -2, 99, T0 + timedelta(hours=1))
    assert radio_kept(sd, rad) == 0


def test_half_cm_distance_is_a_duplicate():
    sd = sd_rows(T0, 2.505, -13.33, 76.9)                       # 250.5 cm -> 251 on the station
    rad = radio_rows(T0 - timedelta(minutes=78), 251, -133, 77, T0)
    assert radio_kept(sd, rad) == 0


def test_different_reading_is_kept():
    sd = sd_rows(T0, 2.505, -13.33, 76.9)
    rad = radio_rows(T0 + timedelta(hours=2), 260, -120, 70, T0 + timedelta(hours=2))
    assert radio_kept(sd, rad) == 1


def test_same_values_too_far_apart_are_both_kept():
    sd = sd_rows(T0, 2.505, -13.33, 76.9)
    rad = radio_rows(T0 + timedelta(hours=4), 251, -133, 77, T0 + timedelta(hours=4))
    assert radio_kept(sd, rad) == 1


def test_missing_value_falls_back_to_time():
    sd = sd_rows(T0, 2.505, -13.33, 76.9)
    near = radio_rows(T0 + timedelta(minutes=5), 251, -133, 77, T0)
    for r in near:
        if r["variable"] == "air_temperature":
            r["value"] = None                                   # dead sensor: no code
    assert radio_kept(sd, near) == 0
    far = [dict(r, time_utc=T0 + timedelta(minutes=30)) for r in near]
    assert radio_kept(sd, far) == 1


def test_no_sd_at_all_keeps_radio():
    rad = radio_rows(T0, 251, -133, 77, T0)
    m = assemble.merge([], rad)
    assert (m.source == "radio").sum() == 3
