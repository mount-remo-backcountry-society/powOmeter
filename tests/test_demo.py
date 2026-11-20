"""Demo page: the real POW-O-METER files, made-up nearby stations and webcam,
nothing from the Ministry or Shames Mountain, and a failed build leaves no
half-built demo behind. Everything here is generated; nothing is downloaded."""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from powometer import demo

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 12, 1, 18, 20, tzinfo=timezone.utc)
MINISTRY = "Data courtesy of the BC Ministry of Transportation & Infrastructure"
RECENT = ("time_utc,station_id,site_id,variable,statistic,value,unit,source,level,approval,quality,qualifiers\n"
          "2026-12-01T10:00:00Z,powometer,shames_top_b,air_temperature,point,-4.0,degC,radio,qc,working,good,\n"
          "2026-12-01T14:00:00Z,powometer,shames_top_b,air_temperature,point,-2.0,degC,radio,qc,working,good,\n"
          "2026-12-01T14:00:00Z,powometer,shames_top_b,snow_depth,point,80,cm,radio,qc,working,good,\n")


@pytest.fixture
def out(tmp_path):
    """An out/ folder as the build leaves it, with real-looking nearby data in it."""
    v1 = tmp_path / "out" / "v1"
    v1.mkdir(parents=True)
    (v1 / "recent.csv").write_text(RECENT, encoding="utf-8")
    (v1 / "latest.json").write_text(json.dumps({"station_id": "powometer", "values": {}}), encoding="utf-8")
    (v1 / "sites.json").write_text(json.dumps({"schema_version": "1.1", "sites": [
        {"id": "shames_top_b", "station": "powometer", "name": "Shames top", "elevation_m": 1150, "until_utc": None},
        {"id": "kasiks_high", "station": "avcan_56", "name": "Kasiks High", "licence": MINISTRY}],
        "stations": [{"id": "powometer", "name": "POW-O-METER"},
                     {"id": "avcan_56", "name": "Kasiks High", "licence": MINISTRY}]}), encoding="utf-8")
    (v1 / "status.json").write_text(json.dumps({"built_utc": "2026-12-01T18:15:00Z", "alarms": [],
        "nearby": {"avcan_56": {"last_observation_utc": "2026-12-01T17:00:00Z"}},
        "info": {"nearby_unmapped_fields": {"avcan_58": ["newSnow"]}},
        "stations": {"powometer": {"last_observation_utc": "2026-12-01T14:00:00Z"}}}), encoding="utf-8")
    (v1 / "nearby_recent.csv").write_text("time_utc,station_id\n2026-12-01T17:00:00Z,avcan_56\n", encoding="utf-8")
    (v1 / "nearby_latest.json").write_text(json.dumps({"stations": {"avcan_56": {}}}), encoding="utf-8")
    (tmp_path / "out" / "webcams").mkdir()
    (tmp_path / "out" / "webcams" / "index.json").write_text('{"credit": "Shames Mountain"}', encoding="utf-8")
    return tmp_path / "out"


def test_builds_the_page_and_files(out):
    d = demo.build(ROOT / "site", out, NOW)
    assert d == out / "demo"
    for f in ["index.html", "app.js", "style.css", "v1/latest.json", "v1/recent.csv", "v1/sites.json",
              "v1/status.json", "v1/nearby_latest.json", "v1/nearby_recent.csv", "webcams/index.json"]:
        assert (d / f).is_file(), f
    assert not (d / "selftest.html").exists()
    assert (d / "v1" / "recent.csv").read_text(encoding="utf-8") == RECENT      # the real readings, as they are


def test_nothing_from_the_ministry_or_shames_mountain(out):
    d = demo.build(ROOT / "site", out, NOW)
    for f in d.rglob("*"):
        if f.suffix in (".json", ".csv"):
            text = f.read_text(encoding="utf-8")
            for word in ("avcan", "Kasiks", "kasiks", "Ministry", "Shames Mountain", "newSnow"):
                assert word not in text, (f, word)
    sites = json.loads((d / "v1" / "sites.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in sites["stations"]] == ["powometer"] + [s[0] for s in demo.FAKE]
    assert all(s["licence"] == demo.LICENCE for s in sites["sites"] if s["station"] != "powometer")


def test_page_says_demo_and_links_to_the_real_files(out):
    html = (demo.build(ROOT / "site", out, NOW) / "index.html").read_text(encoding="utf-8")
    assert "POW-O-METER (demo)" in html and "made up" in html and 'name="robots" content="noindex"' in html
    assert 'href="../v1/best.csv"' in html and 'href="v1/' not in html
    assert 'href="../"' not in html                     # no link to the real station page


def test_nearby_files_match_what_the_page_reads(out):
    d = demo.build(ROOT / "site", out, NOW)
    rows = list(csv.DictReader((d / "v1" / "nearby_recent.csv").open(encoding="utf-8")))
    assert list(rows[0]) == demo.COLUMNS
    latest = json.loads((d / "v1" / "nearby_latest.json").read_text(encoding="utf-8"))
    assert sorted(latest["stations"]) == sorted(s[0] for s in demo.FAKE)
    ridge = latest["stations"]["demo_ridge"]["values"]["air_temperature"]
    assert ridge["time_utc"] == "2026-12-01T17:00:00Z"             # the last full hour
    assert len([r for r in rows if r["station_id"] == "demo_lake"]) == demo.DAYS * 24
    status = json.loads((d / "v1" / "status.json").read_text(encoding="utf-8"))
    assert sorted(status["nearby"]) == sorted(s[0] for s in demo.FAKE)
    assert status["stations"]["powometer"]["last_observation_utc"] == "2026-12-01T14:00:00Z"


def test_made_up_history_stays_the_same_from_run_to_run():
    a, _ = demo.nearby(RECENT, NOW)
    b, _ = demo.nearby(RECENT, NOW + timedelta(hours=3))
    key = lambda r: (r["time_utc"], r["station_id"], r["variable"])
    later = {key(r): r["value"] for r in b}
    assert all(later[key(r)] == r["value"] for r in a if key(r) in later)


def test_made_up_values_are_plausible():
    rows, _ = demo.nearby(RECENT, NOW)
    by = lambda var: [r["value"] for r in rows if r["variable"] == var]
    assert all(0 <= v <= 360 for v in by("wind_from_direction"))
    assert all(v >= 0 for v in by("wind_speed") + by("precipitation_amount") + by("snow_depth"))
    # follows POW-O-METER's temperature, colder up high and warmer down low
    t = {(r["station_id"], r["time_utc"]): r["value"] for r in rows if r["variable"] == "air_temperature"}
    at = "2026-12-01T14:00:00Z"
    assert t[("demo_ridge", at)] < -2.0 + 0.9 < t[("demo_valley", at)] + 0.9


def test_webcam_index_and_pictures(out):
    d = demo.build(ROOT / "site", out, NOW)
    idx = json.loads((d / "webcams" / "index.json").read_text(encoding="utf-8"))
    cam = idx["cameras"][0]
    assert idx["enabled"] and cam["frames"]
    for f in cam["frames"]:
        assert (d / "webcams" / f["file"]).stat().st_size > 1000
        bc_hour = (datetime.strptime(f["time_utc"], "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=7)).hour
        assert bc_hour in demo.CAM_BC_HOURS
    assert (d / cam["live_url"]).is_file()


def test_a_failure_leaves_no_half_built_demo(out, monkeypatch):
    demo.build(ROOT / "site", out, NOW)
    def boom(*a, **k):
        raise RuntimeError("picture failed")
    monkeypatch.setattr(demo, "webcams", boom)
    with pytest.raises(RuntimeError):
        demo.build(ROOT / "site", out, NOW)
    assert not (out / ".demo-tmp").exists()
    assert (out / "demo" / "index.html").is_file()      # the earlier demo is still there
