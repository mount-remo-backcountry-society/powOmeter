"""Decoder for Avalanche Canada's weather-station API (source type
"avalanche_canada"): the BC Ministry stations near Shames.

The API (weather.prod.avalanche.ca/stations/<id>/measurements) returns one
JSON object per hour, newest first, for about the last 7 days, e.g.

    {"stationId": 58, "measurementDateTime": "2026-10-06T02:00:00Z",
     "hourlyPrecip": 1.2, "airTempAvg": 7.9, "windSpeedAvg": 2.2,
     "windDirAvg": 10, "windDirCompass": "N"}

Which fields a station sends varies (seen 2026-10-05: #56 temperature and
wind, #57 temperature only, #58 temperature, wind and precipitation; snow
height is expected in winter). Fields not in FIELDS are kept in raw/ but not
published; the build lists them in status.json so a person can add them.

Statistics: the "...Avg" fields are averages over the hour (`mean`), and
hourlyPrecip is the hour's total (`sum`). Whether `measurementDateTime` is
the start or the end of that hour is NOT verified; SCHEMA.md says so.
"""
from __future__ import annotations

from datetime import datetime, timezone

# API field: (variable, statistic, unit)
FIELDS = {
    "airTempAvg": ("air_temperature", "mean", "degC"),
    "snowHeight": ("snow_depth", "point", "cm"),
    "windSpeedAvg": ("wind_speed", "mean", "km/h"),
    "windDirAvg": ("wind_from_direction", "mean", "degree"),
    "hourlyPrecip": ("precipitation_amount", "sum", "mm"),
}
IGNORED = {"stationId", "measurementDateTime", "windDirCompass"}   # identity, or derived from windDirAvg


def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"time without a zone: {text!r}")
    return t.astimezone(timezone.utc)


def decode(row: dict) -> list[dict]:
    """One API row as readings: [{time_utc, variable, statistic, value, unit}].
    A field that is null gives no reading; a value that is not a number gives
    a reading with value None (quality missing later)."""
    t = parse_time(row["measurementDateTime"])
    out = []
    for field, (variable, statistic, unit) in FIELDS.items():
        if row.get(field) is None:
            continue
        try:
            value = float(row[field])
        except (TypeError, ValueError):
            value = None
        out.append({"time_utc": t, "variable": variable, "statistic": statistic,
                    "value": value, "unit": unit})
    return out


def unmapped(row: dict) -> set[str]:
    """Fields with a value that this decoder does not publish (yet)."""
    return {k for k, v in row.items() if v is not None and k not in FIELDS and k not in IGNORED}
