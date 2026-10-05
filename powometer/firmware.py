"""How the station's firmware turns a reading into radio values.

Used to recognise the SAME reading in the SD record and in a satellite
message (assemble.py) and to anchor the frozen-clock period (frozen.py).

The firmware rounds HALF AWAY FROM ZERO (C `round()` in v1.1, `lround()` in
v1.3): 250.5 -> 251, -0.5 -> -1. Python's built-in round() rounds half to
EVEN (250.5 -> 250), which made ~10 % of readings look different in the two
records (independent code review F3, 2026-10-05).

Firmware path, per reading (scripts/POW_O_METER_v1.1.ino, v1_3.ino):
  distance  : whole mm (pulse width) -> round(mm / 10) -> cm: deterministic
  temperature: printf'd to ONE decimal from the exact float32, then *10
  humidity  : printf'd to NO decimals from the exact float32
The SD card keeps temperature and humidity with only TWO decimals, so when
the SD value sits exactly on a rounding boundary (-0.25 C, 86.50 %) the
float32 behind it may have been just above or just below: both encodings
are possible, and both must be accepted as "the same reading"
(2025-10-19 08:56 UTC: SD -0.25 C, radio -0.2 C).
"""
from __future__ import annotations

import math


def fw_round(x: float) -> int:
    """Round half away from zero, like the firmware. The value is first
    cleaned to 6 decimals so binary floating-point error (2.505 * 100 =
    250.49999...) does not decide the result."""
    x = round(x, 6)
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def _candidates(x: float) -> set[int]:
    """Integers the firmware may have produced from a value known to two
    decimals: both neighbours on an exact .5 tie, else the rounded value."""
    x = round(x, 6)
    if abs(abs(x) % 1 - 0.5) < 1e-6:
        return {math.floor(x), math.ceil(x)}
    return {fw_round(x)}


def reading_codes(distance_m, temperature_c, humidity_pct) -> set[tuple[int, int, int]]:
    """Every (distance cm, temperature x10, humidity) the radio may have sent
    for an SD reading (usually one). Empty if any value is missing."""
    vals = (distance_m, temperature_c, humidity_pct)
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return set()
    cm = fw_round(distance_m * 100)
    return {(cm, t10, 0 if rh >= 100 else rh)
            for t10 in _candidates(temperature_c * 10)
            for rh in _candidates(humidity_pct)}
