"""Rebuild everything from raw/ and config/ into out/v1/ (contract v1).

    python -m powometer build

Steps: validate config -> assemble readings (SD + radio) -> automatic QC and
snow depth -> manual corrections -> approval snapshots -> outputs.
Nothing outside out/ is written. Re-running gives identical files (apart
from the build time in status.json).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import OUT, ROOT, hourly, pipeline, radio, timing
from .config import as_utc, load, validate
from .decoders.powometer_payload import parse_payload

SCHEMA_VERSION = "1.0"
COLUMNS = ["time_utc", "station_id", "site_id", "variable", "statistic", "value", "unit",
           "source", "level", "approval", "quality", "qualifiers"]
BEST = [("snow_depth", "median"), ("distance_to_surface", "median"), ("air_temperature", "point"),
        ("relative_humidity", "point"), ("air_pressure", "point"), ("battery_voltage", "point"),
        ("enclosure_temperature", "point")]
DECIMALS = {"snow_depth": 0, "distance_to_surface": 3, "air_temperature": 2,
            "relative_humidity": 1, "air_pressure": 2, "battery_voltage": 2,
            "enclosure_temperature": 2}
DEPLOYED = datetime(2025, 1, 26, 18, 58, 34, tzinfo=timezone.utc)


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["time_utc"] = out["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for var, d in DECIMALS.items():
        m = out.variable == var
        out.loc[m, "value"] = out.loc[m, "value"].round(d)
    out["value"] = out["value"].map(lambda v: "" if pd.isna(v) else (f"{v:.0f}" if float(v).is_integer() else f"{v:g}"))
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


def _status(cfg, obs: pd.DataFrame, best: pd.DataFrame, messages, unmatched, alarms, sd_report) -> dict:
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
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stations": {
            "powometer": {
                "last_observation_utc": best["time_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_message_utc": last_msg.transmit_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if last_msg else None,
                "last_message_momsn": last_msg.momsn if last_msg else None,
                "clock_offset_min": clock,
                "battery_v": None if batt.empty else round(float(batt.value.iloc[0]), 2),
                "battery_bucket": latest_p.battery_bucket if latest_p else None,
                "decode_errors": len(decode_errors),
            }
        },
        "alarms": alarms,
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
            "time_convention": "UTC, ISO 8601 with Z; instants of point readings",
        } for x in cfg.sites],
    }


def _datapackage() -> dict:
    fields = [
        {"name": "time_utc", "type": "datetime", "description": "UTC instant of the reading (RFC 3339, Z). Hourly files: the whole hour."},
        {"name": "station_id", "type": "string"},
        {"name": "site_id", "type": "string", "description": "Physical site; never combine snow depth across sites"},
        {"name": "variable", "type": "string", "description": "See variables below"},
        {"name": "statistic", "type": "string", "description": "point | min | max | median (of the burst of ultrasonic readings)"},
        {"name": "value", "type": "number"},
        {"name": "unit", "type": "string"},
        {"name": "source", "type": "string", "description": "sd (SD card) | radio (satellite)"},
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
    unmatched = [c["id"] for c in cfg.corrections if c["id"] not in matched]
    publish = {s["id"] for s in cfg.stations if s.get("publish")}
    obs = obs[obs.station_id.isin(publish) & obs.site_id.notna()]

    best = pd.concat([obs[(obs.variable == v) & (obs.statistic == s)] for v, s in BEST])
    best = best[best.quality.notna() & ~best.quality.isin(["poor", "missing"])]
    hourly_df = hourly.to_hourly(best)
    hourly_df["source"] = "derived"

    v1 = out_dir / "v1"
    if v1.exists():
        shutil.rmtree(v1)
    v1.mkdir(parents=True)
    _write_csv(obs, v1 / "observations.csv", COLUMNS)
    _write_csv(best, v1 / "best.csv", COLUMNS)
    _write_csv(hourly_df, v1 / "best_hourly.csv", COLUMNS)

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
    dump("status.json", _status(cfg, obs, best, messages, unmatched, alarms, sd_report))
    dump("sites.json", _sites(cfg))
    dump("datapackage.json", _datapackage())
    for doc in ("SCHEMA.md", "CHANGELOG.md"):
        if (ROOT / doc).exists():
            shutil.copy(ROOT / doc, v1 / doc)

    sizes = ", ".join(f"{p.name} {p.stat().st_size / 1e6:.1f} MB" for p in sorted(v1.glob("*.csv")))
    print(f"Built {v1}: {len(obs):,} observations, {len(best):,} best, {len(hourly_df):,} hourly ({sizes})")
    if alarms:
        print("ALARMS:", *alarms, sep="\n  - ")
    if unmatched:
        print("Note: corrections matching no rows:", ", ".join(unmatched))
    return 0
