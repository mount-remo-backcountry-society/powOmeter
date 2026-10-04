"""Assign times to the readings in a satellite message.

Ported from `headerTimeMs`, `clockOffsetMinutes` and `measurementTimes` in
scripts/apps_script/Code.gs. All times here are timezone-aware UTC.

A message carries the station-clock time of its FIRST reading (DDHHMM, fixed
UTC-8) and N readings taken one queue interval apart; the last reading is
taken shortly before transmission. Two ways to time the readings:

  * station clock: first reading at the header time, then every interval;
  * transmit time: last reading at the transmit time, earlier ones before it.

The station clock is used when it agrees with the transmit-time estimate to
within CLOCK_TRUST_MIN minutes; otherwise transmit time is used. This keeps
the better precision of the station clock without ever trusting a broken one.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from .decoders.powometer_payload import Payload

DEVICE_UTC_OFFSET = timedelta(hours=-8)    # station clock is fixed UTC-8
# Queue interval: 4 wake-ups of 998 s (median of 8,577 gaps, SD download of
# 2026-09-26). The older value 1.1209 h placed final readings after their
# own transmission.
QUEUE_INTERVAL = timedelta(seconds=4 * 998)
CLOCK_TRUST_MIN = 30


def _add_months(d: datetime, months: int) -> tuple[int, int]:
    m = d.month - 1 + months
    return d.year + m // 12, m % 12 + 1


def header_time(p: Payload, near: datetime) -> datetime | None:
    """The DDHHMM header as a UTC instant, choosing the month (previous,
    same or next) that puts it closest to `near`."""
    if not (1 <= p.day <= 31 and p.hour <= 23 and p.minute <= 59):
        return None
    near_wall = near + DEVICE_UTC_OFFSET
    best = None
    for dm in (-1, 0, 1):
        y, mo = _add_months(near_wall, dm)
        # Date.UTC in the original rolls an invalid day (e.g. 31 Feb) into the
        # next month; replicate that.
        wall = datetime(y, mo, 1, p.hour, p.minute, tzinfo=timezone.utc) + timedelta(days=p.day - 1)
        t = wall - DEVICE_UTC_OFFSET
        if best is None or abs(t - near) < abs(best - near):
            best = t
    return best


def clock_offset_minutes(p: Payload, transmit: datetime) -> int | None:
    """Header time of the first reading minus its transmit-time estimate."""
    expected = transmit - (len(p.readings) - 1) * QUEUE_INTERVAL
    h = header_time(p, expected)
    if h is None:
        return None
    # JS Math.round rounds .5 towards +infinity
    x = (h - expected).total_seconds() / 60
    return math.floor(x + 0.5)


def measurement_times(p: Payload, transmit: datetime) -> tuple[list[datetime], str]:
    """Times for each reading slot, and which clock they came from."""
    n = len(p.readings)

    def from_transmit():
        return [transmit - (n - 1 - i) * QUEUE_INTERVAL for i in range(n)]

    off = clock_offset_minutes(p, transmit)
    if off is None:
        return from_transmit(), "transmit time (station clock unreadable)"
    if abs(off) > CLOCK_TRUST_MIN:
        return from_transmit(), f"transmit time (station clock off by {off} min)"
    first = header_time(p, transmit - (n - 1) * QUEUE_INTERVAL)
    return [first + i * QUEUE_INTERVAL for i in range(n)], "station time"
