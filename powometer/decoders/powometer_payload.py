"""Decode a POW-O-METER satellite payload.

Ported from `parsePayload` in scripts/apps_script/Code.gs (tested there
against every historic message). Wire format, unchanged since 2025:

    DDHHMMB  then one or more readings  DDD(+|-)TTTHH  separated by ':'

    DD HH MM  day / hour / minute of the FIRST reading, station clock (UTC-8)
    B         battery bucket 0-9
    DDD       distance to the surface in cm (498-500 = no echo)
    ±TTT      air temperature x10 (-999 = dead temperature sensor)
    HH        relative humidity (00 = 100 %)

A malformed reading is returned as None so the other readings keep their
position (and therefore their time slot).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEAD_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(\d)(.+)$")
READING_RE = re.compile(r"^(\d{3})([+-])(\d{3})(\d{2})$")

NO_ECHO_CM = (498, 499, 500)     # older firmware also sent 498 and 500
DEAD_SHT_X10 = -999


@dataclass(frozen=True)
class Reading:
    distance_cm: int | None      # None = no echo
    air_temperature_c: float | None   # None = dead sensor
    relative_humidity_pct: int | None
    raw: str


@dataclass(frozen=True)
class Payload:
    day: int
    hour: int
    minute: int
    battery_bucket: int
    readings: tuple[Reading | None, ...]
    text: str


def parse_reading(s: str) -> Reading | None:
    m = READING_RE.match(s.strip())
    if not m:
        return None
    cm = int(m.group(1))
    t10 = (-1 if m.group(2) == "-" else 1) * int(m.group(3))
    rh = int(m.group(4))
    dead = t10 == DEAD_SHT_X10
    return Reading(
        distance_cm=None if cm in NO_ECHO_CM else cm,
        air_temperature_c=None if dead else t10 / 10,
        relative_humidity_pct=None if dead else (100 if rh == 0 else rh),
        raw=s.strip(),
    )


def parse_payload(text: str) -> Payload | None:
    """Return the decoded payload, or None if it is not a station payload
    (test messages, "Hello World", early comma-separated formats)."""
    m = HEAD_RE.match(text.strip())
    if not m:
        return None
    readings = tuple(parse_reading(s) for s in m.group(5).split(":"))
    if all(r is None for r in readings):
        return None
    return Payload(
        day=int(m.group(1)), hour=int(m.group(2)), minute=int(m.group(3)),
        battery_bucket=int(m.group(4)), readings=readings, text=text.strip(),
    )


def hex_to_text(payload_hex: str) -> str:
    """RockBLOCK 'Payload' column: hex of the bytes sent."""
    try:
        return bytes.fromhex(payload_hex.strip()).decode("ascii", errors="replace").strip()
    except ValueError:
        return ""
