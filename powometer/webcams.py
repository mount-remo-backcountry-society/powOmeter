"""Webcam time lapse: keep the last few days of Shames Mountain's webcam
frames in the published site (config/webcams.yaml).

Shames publishes only the NEWEST frame of each camera, so a time lapse needs
the frames saved as they come. They are saved in the published site's
`webcams/` folder, never in git:

    webcams/index.json                       what the page reads
    webcams/<camera>/<YYYYMMDDTHHMM>Z.jpg    frames, shrunk to `width` px

Each publish run calls run(out/webcams). The folder survives between runs in
the GitHub Actions cache; if that is ever lost, run() takes the frames back
from the live site, and if that fails too, the time lapse starts again.

A frame is added only if it differs from the camera's previous one (a camera
that is switched off serves the same image for weeks), is not a night
frame (mean brightness below `min_brightness`, 0-255; measured 2026-10-05:
night about 35, daylight about 125), and is at least `min_frame_minutes`
after the previous frame (publish runs every 5 minutes; without this the
time lapse would grow too big for a phone). Its time is the
image's Last-Modified time when the server gives one, else the fetch time.
`latest_utc` in index.json is the time of the camera's newest image, frame
or not, so the page can say when a camera has stopped sending.

Nothing here may stop publishing (design principle 7): every failure is
printed and skipped.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

import yaml

from . import CONFIG

TIMEOUT = 20
INDEX = "index.json"


def load_config(path: Path = CONFIG / "webcams.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def download(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "powometer (MRBS station page)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(), {k.lower(): v for k, v in r.headers.items()}


def _utc(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _time(headers: dict, now: datetime) -> datetime:
    try:
        t = parsedate_to_datetime(headers["last-modified"]).astimezone(timezone.utc)
        return min(t, now)
    except (KeyError, TypeError, ValueError):
        return now


def shrink(data: bytes, width: int, quality: int) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue()


def brightness(data: bytes) -> float:
    from PIL import Image, ImageStat
    with Image.open(io.BytesIO(data)) as im:
        return ImageStat.Stat(im.convert("L")).mean[0]


def _load_index(folder: Path) -> dict | None:
    try:
        return json.loads((folder / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def restore(folder: Path, site_url: str, get: Callable[[str], tuple[bytes, dict]]) -> dict | None:
    """Take the frames back from the live site (when the cache was lost)."""
    try:
        data, _ = get(site_url.rstrip("/") + "/webcams/" + INDEX)
        index = json.loads(data.decode("utf-8"))
        for cam in index.get("cameras", []):
            kept = []
            for fr in cam.get("frames", []):
                try:
                    img, _ = get(site_url.rstrip("/") + "/webcams/" + fr["file"])
                    (folder / fr["file"]).parent.mkdir(parents=True, exist_ok=True)
                    (folder / fr["file"]).write_bytes(img)
                    kept.append(fr)
                except Exception:                    # noqa: BLE001 - a lost frame is just skipped
                    pass
            cam["frames"] = kept
        return index
    except Exception as e:                           # noqa: BLE001
        print(f"webcams: could not take frames back from the site ({type(e).__name__}: {e}); starting afresh")
        return None


def update(folder: Path, cfg: dict, now: datetime, get: Callable[[str], tuple[bytes, dict]]) -> dict:
    """Add new frames, drop old ones, write index.json. Returns the index."""
    folder.mkdir(parents=True, exist_ok=True)
    if not cfg.get("enabled"):
        shutil.rmtree(folder)
        folder.mkdir(parents=True)
        index = {"enabled": False, "built_utc": _utc(now), "cameras": []}
        (folder / INDEX).write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
        return index
    old = _load_index(folder)
    if old is None and cfg.get("site_url"):
        old = restore(folder, cfg["site_url"], get)
    old_cams = {c["id"]: c for c in (old or {}).get("cameras", [])}
    keep_from = now - timedelta(hours=cfg.get("keep_hours", 72))
    min_gap = cfg.get("min_frame_minutes", 0)
    cams = []
    for cam in cfg.get("cameras", []):
        frames = [f for f in old_cams.get(cam["id"], {}).get("frames", [])
                  if (folder / f["file"]).is_file()]
        latest = old_cams.get(cam["id"], {}).get("latest_utc")
        try:
            data, headers = get(cam["url"])
            if not headers.get("content-type", "").startswith("image/"):
                raise ValueError(f"not an image: {headers.get('content-type')!r}")
            sha = hashlib.sha256(data).hexdigest()
            t = _time(headers, now)
            latest = _utc(t)
            new = not frames or (frames[-1]["source_sha256"] != sha
                                 and frames[-1]["time_utc"] < _utc(t - timedelta(minutes=min_gap)))
            if new and brightness(data) < cfg.get("min_brightness", 0):
                new = False                          # night: nothing to see
            if new:
                name = f"{cam['id']}/{t:%Y%m%dT%H%M}Z.jpg"
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(shrink(data, cfg.get("width", 960), cfg.get("jpeg_quality", 70)))
                frames = [f for f in frames if f["file"] != name]
                frames.append({"time_utc": _utc(t), "file": name, "source_sha256": sha})
        except Exception as e:                       # noqa: BLE001 - never stops publishing
            print(f"webcams {cam['id']}: skipped ({type(e).__name__}: {e})")
        frames = [f for f in frames if f["time_utc"] >= _utc(keep_from)]
        cams.append({"id": cam["id"], "label": cam["label"], "live_url": cam["url"], "latest_utc": latest,
                     "frames": frames})
    index = {"enabled": True, "built_utc": _utc(now), "keep_hours": cfg.get("keep_hours", 72),
             "credit": cfg.get("credit", ""), "cameras": cams}
    # remove frames no longer listed
    listed = {f["file"] for c in cams for f in c["frames"]}
    for p in folder.rglob("*.jpg"):
        if p.relative_to(folder).as_posix() not in listed:
            p.unlink()
    (folder / INDEX).write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    return index


def run(folder: Path, now: datetime | None = None, get: Callable[[str], tuple[bytes, dict]] = download,
        cfg: dict | None = None) -> dict | None:
    """The `python -m powometer webcams <folder>` command. Never raises."""
    try:
        index = update(folder, load_config() if cfg is None else cfg, now or datetime.now(timezone.utc), get)
        n = {c["id"]: len(c["frames"]) for c in index["cameras"]}
        print("webcams: " + (f"frames kept {n}" if index["enabled"] else "switched off in config/webcams.yaml"))
        return index
    except Exception as e:                           # noqa: BLE001
        print(f"webcams: FAILED, time lapse not updated ({type(e).__name__}: {e})")
        return None
