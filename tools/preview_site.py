"""Preview the station page locally, in any state, with MADE-UP data.

    python tools/preview_site.py                      list the scenarios
    python tools/preview_site.py fresh                serve one at http://localhost:8000
    python tools/preview_site.py live                 the real files from the last build (out/)

Scenarios (times are relative to now, so ages look right):
    fresh             normal: newest reading 40 min old
    late              newest reading 8 h old (amber)
    stale             newest reading 3 days old (red, values struck through)
    pipeline_stopped  files built 5 h ago: processing has stopped
    storm             snow depth missing for 6 h (falling snow) while temperature continues
    site_a_values     newest snow depth is from the old site: must NOT show under "Now"
    negative          snow depth slightly below zero: shows as 0 on the main view
    alarms            two open alarms in Station health
    nearby_fault      Kasiks High temperature fault (flagged suspect, left out of the chart)
    webcam_off        webcams switched off

Nothing here is real data. The files go to a temporary folder.
"""
from __future__ import annotations

import csv
import http.server
import io
import json
import math
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from powometer import OUT, build  # noqa: E402
from powometer.config import load  # noqa: E402

SCENARIOS = ["fresh", "late", "stale", "pipeline_stopped", "storm", "site_a_values", "negative", "alarms",
             "nearby_fault", "webcam_off"]
STEP = timedelta(minutes=67)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")


def row(t, variable, value, unit, site="shames_top_b", station="powometer", statistic="point",
        source="radio", quality="good", qualifiers=""):
    return {"time_utc": iso(t), "station_id": station, "site_id": site, "variable": variable,
            "statistic": statistic, "value": "" if value is None else f"{value:g}", "unit": unit,
            "source": source, "level": "qc", "approval": "working", "quality": quality, "qualifiers": qualifiers}


def own_rows(scenario, now, last):
    """About 8 days of station readings ending at `last`."""
    out, i = [], 0
    n = int(timedelta(days=8) / STEP)
    for t in (last - k * STEP for k in range(n, -1, -1)):         # ends exactly at `last`
        h = (t - (last - timedelta(days=8))).total_seconds() / 3600
        temp = -4 + 4 * math.sin((t.hour - 15) / 24 * 2 * math.pi) - h / 60
        depth = 0 if h < 60 else min(80, (h - 60) * 0.9)          # a storm builds snow from day 2.5
        if scenario == "negative":
            depth = -4 + (i % 3)
        storm_gap = scenario == "storm" and last - t < timedelta(hours=6)
        if not storm_gap:
            out.append(row(t, "snow_depth", round(depth), "cm", statistic="median"))
            out.append(row(t, "distance_to_surface", round(3.82 - depth / 100, 3), "m", statistic="median"))
        out.append(row(t, "air_temperature", round(temp, 1), "degC"))
        out.append(row(t, "relative_humidity", round(min(100, 80 + 15 * math.sin(h / 7))), "%"))
        i += 1
    return out


def latest_of(rows, extra):
    lat = {}
    for r in rows:
        if r["value"] == "" or (r["variable"] == "snow_depth" and r["statistic"] != "median"):
            continue
        cur = lat.get(r["variable"])
        if cur is None or r["time_utc"] >= cur["time_utc"]:
            lat[r["variable"]] = {"time_utc": r["time_utc"], "value": float(r["value"]), "unit": r["unit"],
                                  "quality": r["quality"], "approval": r["approval"], "site_id": r["site_id"]}
    lat.update(extra)
    return {"schema_version": "1.1", "station_id": "powometer", "values": lat}


def nearby(scenario, now):
    stations = {"avcan_56": ("kasiks_high", 1435), "avcan_57": ("kasiks_low", 732), "avcan_58": ("shames_bottom", 740)}
    rows, latest = [], {}
    t0 = now.replace(minute=0, second=0, microsecond=0)
    for sid, (site, elev) in stations.items():
        for k in range(8 * 24):
            t = t0 - timedelta(hours=k)
            temp = -2 + 4 * math.sin((t.hour - 15) / 24 * 2 * math.pi) - (elev - 1150) * 0.0065
            q, ql = "good", ""
            if scenario == "nearby_fault" and sid == "avcan_56" and k < 30:
                temp, q, ql = -35 - (k % 7) * 2, "suspect", "neighbour"
            rows.append(row(t, "air_temperature", round(temp, 1), "degC", site=site, station=sid,
                            statistic="mean", source="avcan", quality=q, qualifiers=ql))
            if sid in ("avcan_56", "avcan_58"):                     # these two have wind sensors
                wind = max(0.0, (25 if sid == "avcan_56" else 8) + 12 * math.sin(k / 9))
                rows.append(row(t, "wind_speed", round(wind, 1), "km/h", site=site, station=sid,
                                statistic="mean", source="avcan"))
                rows.append(row(t, "wind_from_direction", (200 + 7 * k) % 360, "degree", site=site, station=sid,
                                statistic="mean", source="avcan"))
            if sid == "avcan_56":                                     # and Kasiks High a snow sensor
                rows.append(row(t, "snow_depth", round(160 - k * 0.3), "cm", site=site, station=sid,
                                source="avcan"))
        r0 = rows[-8 * 24]
        latest[sid] = {"values": {"air_temperature": {
            "time_utc": r0["time_utc"], "value": float(r0["value"]), "unit": "degC", "statistic": "mean",
            "quality": r0["quality"], "approval": "working", "site_id": site}}}
    return rows, {"schema_version": "1.1", "stations": latest}


def frames(folder: Path, now, enabled: bool):
    from PIL import Image, ImageDraw
    folder.mkdir(parents=True, exist_ok=True)
    if not enabled:
        (folder / "index.json").write_text(json.dumps({"enabled": False, "cameras": []}), encoding="utf-8")
        return
    cams = []
    for cid, label, base in (("chairlift", "Chairlift", (150, 170, 200)), ("parking", "Parking lot", (190, 190, 180))):
        fr = []
        for k in range(36, -1, -1):
            t = now - timedelta(hours=2 * k)
            if not 7 <= (t - timedelta(hours=7)).hour <= 19:
                continue
            name = f"{cid}/{t:%Y%m%dT%H%M}Z.jpg"
            im = Image.new("RGB", (960, 540), tuple(min(255, c + (k * 3) % 50) for c in base))
            ImageDraw.Draw(im).text((20, 500), f"TEST FRAME {label} {iso(t)}", fill=(200, 0, 0))
            (folder / cid).mkdir(exist_ok=True)
            im.save(folder / name, "JPEG", quality=70)
            fr.append({"time_utc": iso(t), "file": name, "source_sha256": "test"})
        live = folder / f"{cid}-live.jpg"
        shutil.copy(folder / fr[-1]["file"], live)
        cams.append({"id": cid, "label": label, "live_url": f"webcams/{cid}-live.jpg", "frames": fr})
    (folder / "index.json").write_text(json.dumps({"enabled": True, "built_utc": iso(now), "keep_hours": 72,
        "credit": "TEST IMAGES (made up for the preview)", "cameras": cams}), encoding="utf-8")


def write_csv(path: Path, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=build.COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def make(scenario: str, dest: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    shutil.copytree(ROOT / "site", dest, dirs_exist_ok=True)
    v1 = dest / "v1"
    v1.mkdir(exist_ok=True)
    if scenario == "live":
        shutil.copytree(OUT / "v1", v1, dirs_exist_ok=True)
        if (OUT / "webcams").exists():
            shutil.copytree(OUT / "webcams", dest / "webcams", dirs_exist_ok=True)
        return dest
    age = {"late": timedelta(hours=8), "stale": timedelta(days=3), "pipeline_stopped": timedelta(hours=5, minutes=30)}
    last = now - age.get(scenario, timedelta(minutes=40))
    rows = own_rows(scenario, now, last)
    sd_time = now - timedelta(days=9)
    extra = {k: {"time_utc": iso(sd_time), "value": v, "unit": u, "quality": "good", "approval": "working",
                 "site_id": "shames_top_a"}
             for k, v, u in (("air_pressure", 886.6, "hPa"), ("battery_voltage", 4.11, "V"),
                             ("enclosure_temperature", 9.4, "degC"))}
    if scenario == "site_a_values":
        rows = [r for r in rows if r["variable"] not in ("snow_depth", "distance_to_surface")]
        extra["snow_depth"] = {"time_utc": iso(sd_time), "value": 142.0, "unit": "cm", "quality": "good",
                               "approval": "working", "site_id": "shames_top_a"}
    write_csv(v1 / "recent.csv", rows)
    (v1 / "latest.json").write_text(json.dumps(latest_of(rows, extra), indent=1), encoding="utf-8")
    built = now - (timedelta(hours=5) if scenario == "pipeline_stopped" else timedelta(minutes=20))
    alarms = [] if scenario != "alarms" else [
        {"id": "battery", "message": "Battery level 1 (below 3.5 V) in the message of today (limit 3.5 V)"},
        {"id": "no_message", "message": "No satellite message for 13 h (limit 12 h in season)"}]
    if scenario == "stale":
        alarms = [{"id": "no_message", "message": f"No satellite message since {last:%Y-%m-%d %H:%M} UTC"}]
    status = {"schema_version": "1.1", "built_utc": iso(built), "alarms": alarms,
              "stations": {"powometer": {"last_observation_utc": iso(last), "last_message_utc": iso(last + timedelta(minutes=1)),
                                         "last_message_momsn": 1, "clock_offset_min": -1, "battery_v": 4.11,
                                         "battery_v_time_utc": iso(sd_time), "battery_bucket": 3,
                                         "battery_bucket_upper_v": 3.7, "decode_errors": 0}},
              "nearby": {}, "info": {"sd_downloads": [{"download": f"{sd_time:%Y-%m-%d}"}]}}
    (v1 / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    cfg = load()
    for s in cfg.stations:
        if s["id"].startswith("avcan"):
            s.update(publish=True, licence="TEST: made-up values for the preview")
    (v1 / "sites.json").write_text(json.dumps(build._sites(cfg), indent=1), encoding="utf-8")
    nrows, nlatest = nearby(scenario, now)
    write_csv(v1 / "nearby_recent.csv", nrows)
    (v1 / "nearby_latest.json").write_text(json.dumps(nlatest, indent=1), encoding="utf-8")
    frames(dest / "webcams", now, scenario != "webcam_off")
    return dest


def main(argv):
    if len(argv) < 2 or argv[1] not in SCENARIOS + ["live"]:
        print(__doc__)
        return 2
    port = int(argv[2]) if len(argv) > 2 else 8000
    dest = make(argv[1], Path(tempfile.mkdtemp(prefix=f"powometer-{argv[1]}-")))
    print(f"Scenario '{argv[1]}' at http://localhost:{port}/  (files in {dest}; Ctrl+C to stop)", flush=True)
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(dest))
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
