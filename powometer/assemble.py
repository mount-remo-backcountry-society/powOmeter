"""Turn SD rows and satellite messages into one long table of point readings.

One row per value: station_id, site_id, time_utc, variable, statistic, value,
unit, source, time_method, message_utc, qualifiers. Values here are as
recorded (level "raw"); quality control, snow depth and corrections come
later.

Merge rule (design §6.4): the SD card is authoritative. A radio reading is
dropped when the SD card has the same variable and statistic within
SD_MATCH of its time; otherwise it fills the gap (e.g. after the last SD
download). The radio's distance statistic follows the firmware era (minimum
before v1.3, median after), so it is only ever matched against the same SD
statistic: never mixed (review F5).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from . import frozen, radio, sd, timing
from .config import Config, as_utc
from .decoders.powometer_payload import parse_payload
from .firmware import reading_codes

SD_MATCH = timedelta(minutes=10)
STATION = "powometer"

COLUMNS = ["station_id", "site_id", "time_utc", "variable", "statistic", "value", "unit",
           "source", "time_method", "message_utc", "qualifiers"]


def _site_at(cfg: Config, t: datetime) -> str | None:
    for s in cfg.sites:
        if s["station"] != STATION:
            continue
        a, b = as_utc(s["from"]), as_utc(s.get("until"))
        if a <= t and (b is None or t < b):
            return s["id"]
    return None


def _era_statistic(cfg: Config, t: datetime) -> str:
    for e in cfg.eras:
        a, b = as_utc(e["from"]), as_utc(e.get("until"))
        if a <= t and (b is None or t < b):
            return e["radio_distance_statistic"]
    return "min" if t < as_utc(cfg.eras[-1]["from"]) else cfg.eras[-1]["radio_distance_statistic"]


def _f(x: str) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None          # NaN -> None


def sd_points(cfg: Config, messages: list[radio.Message], report: list | None = None) -> list[dict]:
    out = []
    rows = [r for r in sd.load(report=report) if r.true_utc is not None]
    timed = [(r.true_utc, r.parts, r.time_method, r.device_utc) for r in rows]
    block, times, _ = frozen.reconstruct(messages)
    timed += [(t, tuple(p), "reconstructed", None) for p, t in zip(block, times)]
    for t_naive, parts, method, dev in timed:
        t = t_naive.replace(tzinfo=timezone.utc)
        site = _site_at(cfg, t)
        if site is None:
            continue
        q = []
        if method == "reconstructed":
            q.append("timestamp_estimated")
        elif dev is not None and abs((t_naive - dev).total_seconds()) > 60:
            q.append("timestamp_repaired")
        base = dict(station_id=STATION, site_id=site, time_utc=t, source="sd",
                    time_method=method, message_utc=None, qualifiers=";".join(q))
        for var, stat, idx, unit, scale in (
                ("distance_to_surface", "min", 3, "m", 0.001),
                ("distance_to_surface", "max", 4, "m", 0.001),
                ("distance_to_surface", "median", 5, "m", 0.001),
                ("air_temperature", "point", 6, "degC", 1),
                ("relative_humidity", "point", 7, "%", 1),
                ("air_pressure", "point", 9, "hPa", 1),
                ("enclosure_temperature", "point", 8, "degC", 1),   # BMP390, inside the logger box
                ("battery_voltage", "point", 1, "V", 1)):
            v = _f(parts[idx])
            out.append({**base, "variable": var, "statistic": stat,
                        "value": None if v is None else v * scale, "unit": unit})
    return out


def radio_points(cfg: Config, messages: list[radio.Message]) -> list[dict]:
    out = []
    for m in messages:
        p = parse_payload(m.payload_text)
        if p is None:
            continue
        times, source = timing.measurement_times(p, m.transmit_utc)
        q_time = "" if source == "station time" else "timed_by_transmit"
        for r, t in zip(p.readings, times):
            if r is None:
                continue
            site = _site_at(cfg, t)
            if site is None:
                continue
            base = dict(station_id=STATION, site_id=site, time_utc=t, source="radio",
                        time_method=source, message_utc=m.transmit_utc, qualifiers=q_time)
            stat = _era_statistic(cfg, t)
            no_echo = r.distance_cm is None
            out.append({**base, "variable": "distance_to_surface", "statistic": stat,
                        "value": None if no_echo else r.distance_cm / 100, "unit": "m",
                        "qualifiers": ";".join(x for x in (q_time, "no_echo" if no_echo else "") if x)})
            dead = r.air_temperature_c is None
            out.append({**base, "variable": "air_temperature", "statistic": "point",
                        "value": r.air_temperature_c, "unit": "degC",
                        "qualifiers": ";".join(x for x in (q_time, "sensor_fault" if dead else "") if x)})
            out.append({**base, "variable": "relative_humidity", "statistic": "point",
                        "value": r.relative_humidity_pct, "unit": "%",
                        "qualifiers": ";".join(x for x in (q_time, "sensor_fault" if dead else "") if x)})
    return out


CODE_WINDOW = timedelta(hours=3)


def _codes(dist_m, temp_c, rh) -> set:
    """Every way the radio may have encoded an SD reading (firmware.py)."""
    if any(v is None or pd.isna(v) for v in (dist_m, temp_c, rh)):
        return set()
    return reading_codes(float(dist_m), float(temp_c), float(rh))


def _radio_code(dist_m, temp_c, rh) -> tuple | None:
    """A radio reading's own values back as integers (exact: they came off
    the wire as integers)."""
    if any(v is None or pd.isna(v) for v in (dist_m, temp_c, rh)):
        return None
    r = int(round(rh))
    return (int(round(dist_m * 100)), int(round(temp_c * 10)), 0 if r >= 100 else r)


def merge(sd_rows: list[dict], radio_rows: list[dict]) -> pd.DataFrame:
    """SD is authoritative; a radio reading is kept only if the SD card does
    not have the same reading. "Same" = the identical encoded values
    (distance cm, temperature x10, RH) within CODE_WINDOW, because radio
    readings timed from the transmit time can be ~10 min off (seen in Oct
    2025); or, when a value is missing, an SD reading within SD_MATCH."""
    s = pd.DataFrame(sd_rows, columns=COLUMNS)
    r = pd.DataFrame(radio_rows, columns=COLUMNS)
    if r.empty:
        return s.sort_values(["time_utc", "variable", "statistic"]).reset_index(drop=True)

    wide = s.pivot_table(index="time_utc", columns=["variable", "statistic"], values="value",
                         aggfunc="first")
    sd_times = wide.index
    codes: dict[str, dict[tuple, list]] = {"min": {}, "median": {}}
    for stat in codes:
        if ("distance_to_surface", stat) not in wide.columns:
            continue
        d = wide[("distance_to_surface", stat)]
        t = wide.get(("air_temperature", "point"))
        h = wide.get(("relative_humidity", "point"))
        for ts, dv, tv, hv in zip(sd_times, d, t, h):
            for c in _codes(dv, tv, hv):
                codes[stat].setdefault(c, []).append(ts)

    def is_duplicate(reading: pd.DataFrame) -> bool:
        t = reading["time_utc"].iloc[0]
        d = reading.loc[reading.variable == "distance_to_surface"]
        stat = d["statistic"].iloc[0] if not d.empty else "min"
        val = lambda var: reading.loc[reading.variable == var, "value"].iloc[0] \
            if (reading.variable == var).any() else None
        c = _radio_code(val("distance_to_surface"), val("air_temperature"), val("relative_humidity"))
        if c is not None and any(abs(ts - t) <= CODE_WINDOW for ts in codes.get(stat, {}).get(c, [])):
            return True
        i = sd_times.searchsorted(t)
        near = [sd_times[j] for j in (i - 1, i) if 0 <= j < len(sd_times)]
        return any(abs(x - t) <= SD_MATCH for x in near)

    keep_idx = []
    for _, reading in r.groupby(["message_utc", "time_utc"], sort=False):
        if not is_duplicate(reading):
            keep_idx += list(reading.index)
    both = pd.concat([s, r.loc[keep_idx]], ignore_index=True)
    return both.sort_values(["time_utc", "variable", "statistic", "source"]).reset_index(drop=True)


def assemble(cfg: Config) -> pd.DataFrame:
    msgs = radio.load_all()
    return merge(sd_points(cfg, msgs), radio_points(cfg, msgs))
