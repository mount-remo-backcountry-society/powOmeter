"""Demo page: the station page with the real POW-O-METER data, made-up nearby
stations and a drawn placeholder webcam picture, published at demo/.

Nothing from the Ministry or Shames Mountain goes in: the nearby stations are
invented (names, places, numbers) and the webcam is a picture drawn here. The
made-up temperatures follow POW-O-METER's own, shifted for elevation, so the
charts look like weather rather than noise. Every made-up value is a fixed
function of its hour, so the history does not change from one run to the next.

    python -m powometer demo <site folder> <out folder>   (normally: site out)

reads <out>/v1 and writes <out>/demo. It is built in a temporary folder and
moved into place only when complete; the publish workflow runs it with
continue-on-error, so the demo can never stop the real page from publishing.
"""
from __future__ import annotations

import csv
import io
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATION = "powometer"
OWN_FILES = ("latest.json", "recent.csv")          # copied as they are
DAYS = 30                                          # = recent.csv
LICENCE = "Made-up numbers for this demo page, not a real station."
COLUMNS = ["time_utc", "station_id", "site_id", "variable", "statistic", "value", "unit",
           "source", "level", "approval", "quality", "qualifiers"]
UNITS = {"air_temperature": ("mean", "degC"), "wind_speed": ("mean", "km/h"),
         "wind_from_direction": ("mean", "degree"), "precipitation_amount": ("sum", "mm"),
         "snow_depth": ("point", "cm")}
# (id, name, elevation m, variables, phase): phase shifts the made-up weather a little per station
FAKE = [
    ("demo_ridge", "Example Ridge", 1420, ["air_temperature", "wind_speed", "wind_from_direction"], 0.0),
    ("demo_lake", "Example Lake", 760, ["air_temperature"], 1.3),
    ("demo_valley", "Example Valley", 720,
     ["air_temperature", "precipitation_amount", "snow_depth", "wind_speed", "wind_from_direction"], 2.1),
]
OWN_ELEVATION_M = 1150
LAPSE_C_PER_M = 0.0065
CAM_DAYS = 3
CAM_STEP_H = 2
CAM_BC_HOURS = range(8, 17)        # daylight frames only, like the real time lapse
BC = timezone(timedelta(hours=-7))
BANNER = ('<p class="banner"><strong>Demo page.</strong> The snow depth, temperature and humidity are real '
          'POW-O-METER readings; the nearby stations and the webcam picture are made up. '
          '<a href="../">The real station page</a></p>')

H = 3600.0


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _wave(ts: float, period_h: float, phase: float) -> float:
    return math.sin(2 * math.pi * ts / (period_h * H) + phase)


def _own_temperatures(recent_csv: str) -> list[tuple[float, float]]:
    """(unix time, °C) of POW-O-METER's air temperature, oldest first."""
    out = []
    for r in csv.DictReader(io.StringIO(recent_csv)):
        if r.get("station_id") == STATION and r.get("variable") == "air_temperature" and r.get("value"):
            try:
                t = datetime.strptime(r["time_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                out.append((t.timestamp(), float(r["value"])))
            except ValueError:
                continue
    return sorted(out)


def _interp(points: list[tuple[float, float]], ts: float) -> float:
    if not points:
        return 0.0
    if ts <= points[0][0]:
        return points[0][1]
    if ts >= points[-1][0]:
        return points[-1][1]
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= ts:
            lo = mid
        else:
            hi = mid
    (t0, v0), (t1, v1) = points[lo], points[hi]
    return v0 + (v1 - v0) * (ts - t0) / (t1 - t0)


def fake_value(variable: str, elevation_m: int, phase: float, ts: float, own_c: float) -> float:
    """One made-up hourly value. Fixed for a given hour and station."""
    if variable == "air_temperature":
        return round(own_c + (OWN_ELEVATION_M - elevation_m) * LAPSE_C_PER_M + 0.8 * _wave(ts, 26, phase), 1)
    if variable == "wind_speed":
        scale = 1.0 if elevation_m > 1000 else 0.5
        return round(max(0.0, scale * (14 + 9 * _wave(ts, 74, phase) + 4 * _wave(ts, 9, 2 * phase))), 1)
    if variable == "wind_from_direction":
        return float(round((240 + 70 * _wave(ts, 103, phase)) % 360))
    if variable == "precipitation_amount":
        storm = _wave(ts, 137, 1 + phase) + 0.4 * _wave(ts, 31, phase)
        return round(max(0.0, storm - 0.6) * 2.0, 1)
    if variable == "snow_depth":
        return float(round(max(0.0, 25 + 12 * _wave(ts, 312, 0.5 + phase) + 4 * _wave(ts, 70, phase))))
    raise ValueError(variable)


def nearby(recent_csv: str, now: datetime) -> tuple[list[dict], dict]:
    """Rows for nearby_recent.csv and the content of nearby_latest.json."""
    own = _own_temperatures(recent_csv)
    last = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    hours = [last - timedelta(hours=h) for h in range(DAYS * 24 - 1, -1, -1)]
    rows, latest = [], {}
    for sid, _name, elev, variables, phase in FAKE:
        values = {}
        for var in variables:
            stat, unit = UNITS[var]
            for t in hours:
                ts = t.timestamp()
                v = fake_value(var, elev, phase, ts, _interp(own, ts))
                rows.append({"time_utc": _iso(t), "station_id": sid, "site_id": sid, "variable": var,
                             "statistic": stat, "value": v, "unit": unit, "source": "demo", "level": "qc",
                             "approval": "working", "quality": "good", "qualifiers": ""})
            values[var] = {"approval": "working", "quality": "good", "site_id": sid, "statistic": stat,
                           "time_utc": _iso(hours[-1]), "unit": unit, "value": rows[-1]["value"]}
        latest[sid] = {"values": values}
    return rows, {"schema_version": "1.1", "stations": latest}


def sites(real: dict) -> dict:
    """sites.json: POW-O-METER's own sites and station, plus the made-up ones."""
    own_sites = [s for s in real.get("sites", []) if s.get("station") == STATION]
    own_stations = [s for s in real.get("stations", []) if s.get("id") == STATION]
    fake_sites = [{"elevation_m": elev, "from_utc": "2026-01-01T00:00:00Z", "id": sid, "latitude": None,
                   "licence": LICENCE, "longitude": None, "name": name, "station": sid,
                   "time_convention": "UTC, ISO 8601 with Z; hourly values (see SCHEMA.md)", "until_utc": None}
                  for sid, name, elev, _, _ in FAKE]
    fake_stations = [{"id": sid, "licence": LICENCE, "name": name, "operator": "Nobody: made-up demo data",
                      "publish": True} for sid, name, _, _, _ in FAKE]
    return {"schema_version": real.get("schema_version", "1.1"), "sites": own_sites + fake_sites,
            "stations": own_stations + fake_stations}


def status(real: dict, now: datetime) -> dict:
    out = json.loads(json.dumps(real))
    last = _iso(now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1))
    out["nearby"] = {sid: {"last_new_data_fetched_utc": _iso(now), "last_observation_utc": last}
                     for sid, *_ in FAKE}
    out.get("info", {})["nearby_unmapped_fields"] = {}
    return out


def picture(t: datetime | None, width: int = 960, height: int = 540) -> bytes:
    """A drawn mountain scene with DEMO written on it; the sky follows the hour."""
    from PIL import Image, ImageDraw, ImageFont
    hour = t.astimezone(BC).hour if t else 12
    light = max(0.25, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0.25
    sky = tuple(int(c * light) for c in (120, 170, 230))
    im = Image.new("RGB", (width, height), sky)
    d = ImageDraw.Draw(im)
    snow = tuple(int(c * (0.5 + 0.5 * light)) for c in (235, 240, 248))
    rock = tuple(int(c * (0.5 + 0.5 * light)) for c in (90, 95, 110))
    d.polygon([(0, height), (0, 330), (220, 150), (380, 260), (560, 110), (760, 280), (width, 200), (width, height)], fill=rock)
    d.polygon([(150, 210), (220, 150), (300, 205)], fill=snow)
    d.polygon([(470, 190), (560, 110), (650, 190)], fill=snow)
    d.polygon([(0, height), (0, 430), (width, 400), (width, height)], fill=snow)
    for x in range(40, width, 90):                       # trees
        d.polygon([(x, 440), (x + 18, 380), (x + 36, 440)], fill=(30, 70, 45))
    d.rectangle([0, 0, width, 56], fill=(0, 0, 0))
    label = "DEMO PICTURE - not a real webcam"
    if t:
        label += "   " + t.astimezone(BC).strftime("%a %d %b %H:%M") + " BC"
    d.text((16, 12), label, fill=(255, 255, 255), font=ImageFont.load_default(size=30))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=70, optimize=True)
    return out.getvalue()


def webcams(folder: Path, now: datetime) -> dict:
    """webcams/index.json and its pictures, in the format of the real time lapse."""
    cam = folder / "example"
    cam.mkdir(parents=True)
    frames = []
    start = (now - timedelta(days=CAM_DAYS)).replace(minute=0, second=0, microsecond=0)
    t = start
    while t <= now:
        if t.astimezone(BC).hour in CAM_BC_HOURS and t.hour % CAM_STEP_H == 0:
            name = f"example/{t:%Y%m%dT%H%MZ}.jpg"
            (folder / name).write_bytes(picture(t))
            frames.append({"time_utc": _iso(t), "file": name})
        t += timedelta(hours=1)
    (cam / "latest.jpg").write_bytes(picture(None))
    return {"enabled": True, "built_utc": _iso(now), "keep_hours": CAM_DAYS * 24,
            "credit": "Placeholder pictures drawn for this demo, not a real webcam.",
            "cameras": [{"id": "example", "label": "Example camera", "live_url": "webcams/example/latest.jpg",
                         "latest_utc": _iso(now), "frames": frames}]}


def page(index_html: str) -> str:
    """The station page, marked as a demo; download links go to the real files."""
    html = index_html.replace("<title>POW-O-METER snow station</title>",
                              '<title>POW-O-METER demo</title>\n<meta name="robots" content="noindex">', 1)
    html = html.replace('<a href="./" aria-current="page">Station data</a>',
                        '<a href="../">Station data</a>', 1)
    html = html.replace("<h1>POW-O-METER</h1>", "<h1>POW-O-METER (demo)</h1>\n  " + BANNER, 1)
    return html.replace('href="v1/', 'href="../v1/')


def build(site: Path, out: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    v1 = out / "v1"
    final, tmp = out / "demo", out / ".demo-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(site, tmp)
        (tmp / "selftest.html").unlink(missing_ok=True)
        (tmp / "index.html").write_text(page((site / "index.html").read_text(encoding="utf-8")),
                                        encoding="utf-8", newline="\n")
        (tmp / "v1").mkdir()
        for f in OWN_FILES:
            shutil.copy2(v1 / f, tmp / "v1" / f)
        recent = (v1 / "recent.csv").read_text(encoding="utf-8")
        rows, latest = nearby(recent, now)
        with open(tmp / "v1" / "nearby_recent.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        dump = lambda name, obj: (tmp / "v1" / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")
        dump("nearby_latest.json", latest)
        dump("sites.json", sites(json.loads((v1 / "sites.json").read_text(encoding="utf-8"))))
        dump("status.json", status(json.loads((v1 / "status.json").read_text(encoding="utf-8")), now))
        index = webcams(tmp / "webcams", now)
        (tmp / "webcams" / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return final
