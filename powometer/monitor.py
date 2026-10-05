"""Station health checks (design §6.7), thresholds in config/monitoring.yaml.

Each alarm has a stable `id`, so the publish workflow can open one GitHub
Issue when an alarm starts and close it when the alarm clears
(tools/alarm_issues.py): one email per event, not one per hourly run.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from .config import Config
from .decoders.powometer_payload import parse_payload

DECODE_WINDOW = timedelta(hours=24)       # a decode error alarms for a day, then clears


def battery_upper_v(bucket: int) -> float | None:
    """Upper voltage of a radio battery level: 0 = below 3.4 V, then 0.1 V
    steps (1 = 3.4-3.5 V ... 8 = 4.1-4.2 V), 9 = 4.2 V or more."""
    return None if bucket >= 9 else round(3.4 + 0.1 * bucket, 1)


def in_season(cfg: Config, now: datetime) -> bool:
    s = cfg.monitoring.get("season") or {}
    start, end = s.get("start", "01-01"), s.get("end", "12-31")
    md = f"{now:%m-%d}"
    return start <= md <= end if start <= end else (md >= start or md <= end)


def alarm(id_: str, message: str) -> dict:
    return {"id": id_, "message": message}


def check(cfg: Config, messages, now: datetime, build_alarms: list[str] = (),
          injected: str = "") -> list[dict]:
    """All current alarms. `messages`: decodable and undecodable radio
    messages since the current firmware (newest last). `build_alarms`: plain
    texts from the build (approval mismatches, SD files, visit steps)."""
    lim = cfg.monitoring.get("alarms") or {}
    out: list[dict] = []
    last = messages[-1] if messages else None

    hours = lim.get("no_message_hours")
    if hours and in_season(cfg, now):
        age = (now - last.transmit_utc) if last else None
        if age is None or age > timedelta(hours=hours):
            since = f"since {last.transmit_utc:%Y-%m-%d %H:%M} UTC" if last else "at all"
            out.append(alarm("no_message", f"No satellite message {since} (limit {hours} h in season)"))

    decoded = [(m, parse_payload(m.payload_text)) for m in messages]
    latest = next(((m, p) for m, p in reversed(decoded) if p), None)
    if latest:
        from .timing import clock_offset_minutes
        m, p = latest
        off = clock_offset_minutes(p, m.transmit_utc)
        lim_off = lim.get("clock_offset_minutes")
        if lim_off is not None and off is not None and abs(off) > lim_off:
            out.append(alarm("clock_offset", f"Station clock is {off:+d} min off (limit ±{lim_off} min); "
                                             "readings are timed from the transmit time meanwhile"))
        v = lim.get("battery_volts_below")
        up = battery_upper_v(p.battery_bucket)
        if v is not None and up is not None and up <= v:
            out.append(alarm("battery", f"Battery level {p.battery_bucket} (below {up} V) in the message of "
                                        f"{m.transmit_utc:%Y-%m-%d %H:%M} UTC (limit {v} V)"))

    if lim.get("decode_errors"):
        bad = [m for m, p in decoded if p is None and now - m.transmit_utc <= DECODE_WINDOW]
        if len(bad) >= lim["decode_errors"]:
            out.append(alarm("decode_errors", f"{len(bad)} message(s) in the last 24 h could not be decoded, "
                                              f"latest {bad[-1].transmit_utc:%Y-%m-%d %H:%M} UTC"))

    for text in build_alarms:
        out.append(alarm("build-" + hashlib.sha1(text.encode()).hexdigest()[:10], text))
    if injected:
        out.append(alarm("test-injected", f"TEST (injected by hand to check the alarm Issues): {injected}"))
    return out
