"""Rebuild everything from raw/ and config/ into out/v1/ (contract v1).

    python -m powometer build

Steps: validate config -> assemble readings (SD + radio) -> automatic QC and
snow depth -> manual corrections -> approval snapshots -> outputs.
Nothing outside out/ is written. Re-running gives identical files (apart
from the build time in status.json).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import OUT, ROOT, hourly, monitor, nearby, pipeline, radio, timing
from .config import as_utc, load, validate
from .decoders.powometer_payload import parse_payload

SCHEMA_VERSION = "1.1"
RECENT_DAYS = 30                  # recent.csv: the days before the newest reading
COLUMNS = ["time_utc", "station_id", "site_id", "variable", "statistic", "value", "unit",
           "source", "level", "approval", "quality", "qualifiers"]
BEST = [("snow_depth", "median"), ("distance_to_surface", "median"), ("air_temperature", "point"),
        ("relative_humidity", "point"), ("air_pressure", "point"), ("battery_voltage", "point"),
        ("enclosure_temperature", "point")]
DECIMALS = {"snow_depth": 0, "distance_to_surface": 3, "air_temperature": 2,
            "relative_humidity": 1, "air_pressure": 2, "battery_voltage": 2,
            "enclosure_temperature": 2, "wind_speed": 1, "wind_from_direction": 0,
            "precipitation_amount": 1}
DEPLOYED = datetime(2025, 1, 26, 18, 58, 34, tzinfo=timezone.utc)


def _num(v) -> str:
    """Shortest text for a number; never "-0" (review F12)."""
    if pd.isna(v):
        return ""
    v = float(v) + 0.0                     # + 0.0 turns -0.0 into 0.0
    return f"{v:.0f}" if v.is_integer() else f"{v:g}"


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["time_utc"] = out["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for var, d in DECIMALS.items():
        m = out.variable == var
        out.loc[m, "value"] = out.loc[m, "value"].round(d)
    out["value"] = out["value"].map(_num)
    out["qualifiers"] = out["qualifiers"].fillna("")
    return out


def _write_csv(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    df = df.sort_values(["station_id", "site_id", "variable", "statistic", "time_utc", "source"],
                        kind="mergesort")
    _fmt(df)[columns].to_csv(path, index=False, lineterminator="\n")


def sd_alarms(report: list[dict]) -> list[str]:
    """An SD file that yields nothing readable, or rejects more than 1 % of
    its "Data:" lines, needs a look: the data would otherwise be dropped
    silently (code review F7)."""
    out = []
    for r in report:
        where = f"SD {r['download']}/{r['file']}"
        if r["data_lines"] - r["rejected_lines"] == 0:
            out.append(f"{where}: no readable measurements ({r['data_lines']} Data lines)")
        elif r["rejected_lines"] > 0.01 * r["data_lines"]:
            out.append(f"{where}: {r['rejected_lines']} of {r['data_lines']} Data lines unreadable")
    return out


def recent(best: pd.DataFrame, days: int = RECENT_DAYS) -> pd.DataFrame:
    """The rows of `best` from the `days` before its newest reading: small
    enough for the station page on a phone. Anchored to the data, not the
    clock, so two builds from the same raw data are identical."""
    if best.empty:
        return best
    return best[best.time_utc > best.time_utc.max() - pd.Timedelta(days=days)]


def _status(cfg, obs: pd.DataFrame, best: pd.DataFrame, messages, unmatched, alarms, sd_report,
            now: datetime) -> dict:
    last_msg = messages[-1] if messages else None
    payloads = [m for m in messages if m.transmit_utc >= DEPLOYED]
    decode_errors = [m for m in payloads if parse_payload(m.payload_text) is None]
    # the newest decodable message, timed by ITS OWN transmit time (code
    # review F6: not the newest message's, which may be undecodable)
    latest_m = next((m for m in reversed(payloads) if parse_payload(m.payload_text)), None)
    latest_p = parse_payload(latest_m.payload_text) if latest_m else None
    clock = timing.clock_offset_minutes(latest_p, latest_m.transmit_utc) if latest_p else None
    batt = best[(best.variable == "battery_voltage") & best.value.notna()].sort_values("time_utc").tail(1)
    st = {
        "schema_version": SCHEMA_VERSION,
        "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stations": {
            "powometer": {
                "last_observation_utc": best["time_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_message_utc": last_msg.transmit_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if last_msg else None,
                "last_message_momsn": last_msg.momsn if last_msg else None,
                "clock_offset_min": clock,
                "battery_v": None if batt.empty else round(float(batt.value.iloc[0]), 2),
                # battery_v comes from the SD card, so it is only as recent as the
                # last site visit; its time says so (v1.1)
                "battery_v_time_utc": None if batt.empty else batt.time_utc.iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "battery_bucket": latest_p.battery_bucket if latest_p else None,
                "battery_bucket_upper_v": monitor.battery_upper_v(latest_p.battery_bucket) if latest_p else None,
                "decode_errors": len(decode_errors),
            }
        },
        # each {"id", "message"}; INJECT_ALARM (set by hand in the publish
        # workflow) adds a test alarm to check that Issues open and close
        "alarms": monitor.check(cfg, payloads, now, alarms, os.environ.get("INJECT_ALARM", "")),
        "info": {
            "corrections_matching_nothing": unmatched,
            "sd_downloads": list(sd_report),
            "rows": {"observations": int(len(obs)), "best": int(len(best))},
        },
    }
    return st


def _sites(cfg) -> dict:
    stations = {s["id"]: s for s in cfg.stations}
    return {
        "schema_version": SCHEMA_VERSION,
        "stations": [{k: s.get(k) for k in ("id", "name", "operator", "licence", "publish")}
                     for s in cfg.stations],
        "sites": [{
            "id": x["id"], "station": x["station"], "name": x["name"],
            "latitude": x["latitude"], "longitude": x["longitude"], "elevation_m": x["elevation_m"],
            "from_utc": as_utc(x["from"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until_utc": as_utc(x["until"]).strftime("%Y-%m-%dT%H:%M:%SZ") if x.get("until") else None,
            "licence": stations[x["station"]].get("licence"),
            "time_convention": "UTC, ISO 8601 with Z; instants of point readings"
                               if stations[x["station"]].get("type") == "powometer"
                               else "UTC, ISO 8601 with Z; hourly values (see SCHEMA.md)",
        } for x in cfg.sites],
    }


def _datapackage() -> dict:
    fields = [
        {"name": "time_utc", "type": "datetime", "description": "UTC instant of the reading (RFC 3339, Z). Hourly files: the whole hour."},
        {"name": "station_id", "type": "string"},
        {"name": "site_id", "type": "string", "description": "Physical site; never combine snow depth across sites"},
        {"name": "variable", "type": "string", "description": "See variables below"},
        {"name": "statistic", "type": "string", "description": "point | min | max | median (of the burst of ultrasonic readings) | mean | sum (nearby stations: over the hour)"},
        {"name": "value", "type": "number"},
        {"name": "unit", "type": "string"},
        {"name": "source", "type": "string", "description": "sd (SD card) | radio (satellite) | derived (best_hourly.csv: interpolated) | avcan (nearby stations)"},
        {"name": "level", "type": "string", "description": "qc (automatic checks) | corrected (manual corrections applied)"},
        {"name": "approval", "type": "string", "description": "working (shown as Provisional) | in_review | approved"},
        {"name": "quality", "type": "string", "description": "WaterML2: good | suspect | estimate | poor | unchecked | missing"},
        {"name": "qualifiers", "type": "string", "description": "Semicolon-separated reasons; correction ids such as C001"},
    ]
    variables = {
        "snow_depth": {"cf_standard_name": "surface_snow_thickness", "unit": "cm"},
        "distance_to_surface": {"cf_standard_name": None, "unit": "m"},
        "air_temperature": {"cf_standard_name": "air_temperature", "unit": "degC"},
        "relative_humidity": {"cf_standard_name": "relative_humidity", "unit": "%"},
        "air_pressure": {"cf_standard_name": "surface_air_pressure", "unit": "hPa"},
        "battery_voltage": {"cf_standard_name": None, "unit": "V"},
        "enclosure_temperature": {"cf_standard_name": None, "unit": "degC"},
        "wind_speed": {"cf_standard_name": "wind_speed", "unit": "km/h"},
        "wind_from_direction": {"cf_standard_name": "wind_from_direction", "unit": "degree"},
        "precipitation_amount": {"cf_standard_name": "precipitation_amount", "unit": "mm"},
    }
    res = lambda name, desc: {"name": name.split(".")[0], "path": name, "format": "csv",
                              "description": desc, "schema": {"fields": fields}}
    return {
        "name": "powometer", "title": "POW-O-METER snow station, Shames Mountain BC",
        "version": SCHEMA_VERSION,
        "licenses": [{"name": "CC-BY-4.0", "title": "Mount Remo Backcountry Society, CC BY 4.0"}],
        "resources": [
            res("observations.csv", "All point readings, every variable and statistic, with labels"),
            res("best.csv", "Best available point readings (median distance and snow depth); poor and missing values excluded"),
            res("best_hourly.csv", "best.csv interpolated to whole hours (not means)"),
            res("recent.csv", f"The rows of best.csv from the {RECENT_DAYS} days before its newest reading"),
            res("nearby_best.csv", "Nearby stations of other owners (their licences, see sites.json); "
                                   "poor and missing values excluded"),
            res("nearby_recent.csv", f"The rows of nearby_best.csv from the {RECENT_DAYS} days before its newest reading"),
        ],
        "variables": variables,
    }


def build(out_dir: Path = OUT) -> int:
    problems = validate()
    if problems:
        print("Build stopped: fix these config problems first:")
        for p in problems:
            print("  - " + p)
        return 1
    cfg = load()
    messages = radio.load_all()
    sd_report: list[dict] = []
    obs, matched, alarms = pipeline.process(cfg, messages, sd_report=sd_report)
    alarms += sd_alarms(sd_report)
    publish = {s["id"] for s in cfg.stations if s.get("publish")}
    obs = obs[obs.station_id.isin(publish) & obs.site_id.notna()]

    best = pd.concat([obs[(obs.variable == v) & (obs.statistic == s)] for v, s in BEST])
    best = best[best.quality.notna() & ~best.quality.isin(["poor", "missing"])]
    hourly_df = hourly.to_hourly(best)
    hourly_df["source"] = "derived"

    # nearby stations (published separately: other owners, other licences)
    records = nearby.load_raw()
    near, near_matched, near_alarms, unmapped = nearby.process(cfg, records, hourly_df)
    alarms += near_alarms
    near_best = near[near.quality.notna() & ~near.quality.isin(["poor", "missing"])]
    unmatched = [c["id"] for c in cfg.corrections if c["id"] not in matched | near_matched]

    v1 = out_dir / "v1"
    if v1.exists():
        shutil.rmtree(v1)
    v1.mkdir(parents=True)
    _write_csv(obs, v1 / "observations.csv", COLUMNS)
    _write_csv(best, v1 / "best.csv", COLUMNS)
    _write_csv(hourly_df, v1 / "best_hourly.csv", COLUMNS)
    _write_csv(recent(best), v1 / "recent.csv", COLUMNS)
    _write_csv(near_best, v1 / "nearby_best.csv", COLUMNS)
    _write_csv(recent(near_best), v1 / "nearby_recent.csv", COLUMNS)

    latest = {}
    for v, s in BEST:
        row = best[(best.variable == v) & (best.statistic == s)].sort_values("time_utc").tail(1)
        if not row.empty:
            r = _fmt(row).iloc[0]
            latest[v] = {"time_utc": r.time_utc, "value": float(r.value) if r.value != "" else None,
                         "unit": r.unit,
                         "quality": r.quality, "approval": r.approval, "site_id": r.site_id}
    dump = lambda name, obj: (v1 / name).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dump("latest.json", {"schema_version": SCHEMA_VERSION, "station_id": "powometer", "values": latest})
    dump("nearby_latest.json", {"schema_version": SCHEMA_VERSION,
                                "stations": nearby.latest(near_best, _fmt)})
    status = _status(cfg, obs, best, messages, unmatched, alarms, sd_report, datetime.now(timezone.utc))
    status["nearby"] = nearby.health(cfg, records, near_best)
    status["info"]["nearby_unmapped_fields"] = unmapped
    dump("status.json", status)
    alarms = [a["message"] for a in status["alarms"]]
    dump("sites.json", _sites(cfg))
    dump("datapackage.json", _datapackage())
    for doc in ("SCHEMA.md", "CHANGELOG.md"):
        if (ROOT / doc).exists():
            shutil.copy(ROOT / doc, v1 / doc)

    sizes = ", ".join(f"{p.name} {p.stat().st_size / 1e6:.1f} MB" for p in sorted(v1.glob("*.csv")))
    print(f"Built {v1}: {len(obs):,} observations, {len(best):,} best, {len(hourly_df):,} hourly, "
          f"{len(near_best):,} nearby ({sizes})")
    if alarms:
        print("ALARMS:", *alarms, sep="\n  - ")
    if unmatched:
        print("Note: corrections matching no rows:", ", ".join(unmatched))
    return 0
