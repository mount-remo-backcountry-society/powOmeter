"""Webcam time lapse: frames added only when new, shrunk, dropped after
keep_hours, restored from the site when the cache is lost, and no failure
ever stops publishing. Images are generated here; nothing is downloaded."""
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from powometer import webcams
from powometer.config import validate

NOW = datetime(2026, 12, 1, 18, 0, tzinfo=timezone.utc)
CFG = {"enabled": True, "keep_hours": 72, "width": 960, "jpeg_quality": 70, "credit": "© Test",
       "site_url": "https://example.org/site/",
       "cameras": [{"id": "lift", "label": "Lift", "url": "https://cam/lift.png"},
                   {"id": "lot", "label": "Lot", "url": "https://cam/lot.png"}]}


def png(shade: int, w: int = 1920, h: int = 1080) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (shade, shade, shade)).save(out, "PNG")
    return out.getvalue()


class Cams:
    """A fake web: camera images (with Last-Modified) and the live site."""
    def __init__(self):
        self.images = {"https://cam/lift.png": (png(10), NOW), "https://cam/lot.png": (png(200), NOW)}
        self.site = {}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url in self.site:
            return self.site[url], {}
        if url not in self.images:
            raise OSError(f"404 {url}")
        data, t = self.images[url]
        return data, {"content-type": "image/png", "last-modified": t.strftime("%a, %d %b %Y %H:%M:%S GMT")}


def frames(index, cam="lift"):
    return [f["time_utc"] for c in index["cameras"] if c["id"] == cam for f in c["frames"]]


def test_new_frames_are_added_shrunk_and_unchanged_ones_skipped(tmp_path):
    web = Cams()
    idx = webcams.run(tmp_path, NOW, web, CFG)
    assert frames(idx) == ["2026-12-01T18:00:00Z"]
    jpg = tmp_path / idx["cameras"][0]["frames"][0]["file"]
    with Image.open(jpg) as im:
        assert im.format == "JPEG" and im.width == 960
    idx = webcams.run(tmp_path, NOW + timedelta(hours=1), web, CFG)       # camera unchanged
    assert frames(idx) == ["2026-12-01T18:00:00Z"]
    web.images["https://cam/lift.png"] = (png(30), NOW + timedelta(minutes=50))
    idx = webcams.run(tmp_path, NOW + timedelta(hours=2), web, CFG)
    assert frames(idx) == ["2026-12-01T18:00:00Z", "2026-12-01T18:50:00Z"]
    assert json.loads((tmp_path / "index.json").read_text())["credit"] == "© Test"


def test_old_frames_are_dropped_with_their_files(tmp_path):
    web = Cams()
    webcams.run(tmp_path, NOW, web, CFG)
    web.images["https://cam/lift.png"] = (png(40), NOW + timedelta(hours=73))
    idx = webcams.run(tmp_path, NOW + timedelta(hours=73), web, CFG)
    assert frames(idx) == ["2026-12-04T19:00:00Z"]
    assert frames(idx, "lot") == []                           # a camera that stopped empties out
    assert sorted(p.name for p in tmp_path.rglob("*.jpg")) == ["20261204T1900Z.jpg"]


def test_a_failing_camera_never_stops_the_run(tmp_path):
    web = Cams()
    del web.images["https://cam/lot.png"]
    idx = webcams.run(tmp_path, NOW, web, CFG)
    assert frames(idx) == ["2026-12-01T18:00:00Z"] and frames(idx, "lot") == []
    web.images["https://cam/lot.png"] = (b"<html>maintenance</html>", NOW)
    assert webcams.run(tmp_path, NOW, web, CFG) is not None
    assert webcams.run(tmp_path, NOW, web, {**CFG, "cameras": None}) is None        # broken config: reported


def test_lost_cache_is_restored_from_the_site(tmp_path):
    web = Cams()
    first = tmp_path / "a"
    idx = webcams.run(first, NOW, web, CFG)
    web.site["https://example.org/site/webcams/index.json"] = json.dumps(idx).encode()
    for c in idx["cameras"]:
        for f in c["frames"]:
            web.site["https://example.org/site/webcams/" + f["file"]] = (first / f["file"]).read_bytes()
    web.images["https://cam/lift.png"] = (png(60), NOW + timedelta(hours=1))
    idx2 = webcams.run(tmp_path / "b", NOW + timedelta(hours=1), web, CFG)      # empty folder: cache lost
    assert frames(idx2) == ["2026-12-01T18:00:00Z", "2026-12-01T19:00:00Z"]


def test_switched_off_publishes_nothing(tmp_path):
    web = Cams()
    webcams.run(tmp_path, NOW, web, CFG)
    idx = webcams.run(tmp_path, NOW, web, {**CFG, "enabled": False})
    assert idx["enabled"] is False and not list(tmp_path.rglob("*.jpg"))


def test_real_config_meets_the_permission():
    """docs/permissions/shames-webcams-2026-10.md: credit Shames Mountain, and say it is not liable."""
    cfg = webcams.load_config()
    if cfg["enabled"]:
        assert "Shames Mountain" in cfg["credit"] and "not liable" in cfg["credit"]
    assert validate() == []


def test_night_frames_are_left_out(tmp_path):
    web = Cams()
    web.images["https://cam/lift.png"] = (png(30), NOW)            # dark
    idx = webcams.run(tmp_path, NOW, web, {**CFG, "min_brightness": 50})
    assert frames(idx) == [] and len(frames(idx, "lot")) == 1


def test_frames_are_spaced_by_min_frame_minutes(tmp_path):
    # publish runs every 5 minutes; the camera changes every 10
    web = Cams()
    cfg = {**CFG, "min_frame_minutes": 55}
    for m in range(0, 135, 5):
        web.images["https://cam/lift.png"] = (png(20 + m // 10), NOW + timedelta(minutes=m // 10 * 10))
        idx = webcams.run(tmp_path, NOW + timedelta(minutes=m), web, cfg)
    assert frames(idx) == ["2026-12-01T18:00:00Z", "2026-12-01T19:00:00Z", "2026-12-01T20:00:00Z"]


def test_latest_image_time_is_kept_even_without_a_new_frame(tmp_path):
    web = Cams()
    latest = lambda idx, cam: next(c["latest_utc"] for c in idx["cameras"] if c["id"] == cam)
    idx = webcams.run(tmp_path, NOW, web, {**CFG, "min_brightness": 50})
    assert latest(idx, "lift") == "2026-12-01T18:00:00Z"                  # a night image: no frame, but a time
    assert frames(idx) == []
    del web.images["https://cam/lift.png"]                                 # camera unreachable: last time kept
    idx = webcams.run(tmp_path, NOW + timedelta(hours=2), web, CFG)
    assert latest(idx, "lift") == "2026-12-01T18:00:00Z"
