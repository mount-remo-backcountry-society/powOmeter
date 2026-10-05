"""Load and validate the configuration files in config/.

`validate()` returns a list of plain-English problems (empty = all good). It
is what `python -m powometer validate` runs, and what a pull-request check
will run, so that a mistake made while editing a file in the GitHub web
interface shows up as a readable message.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import CONFIG, ROOT

DECODER_TYPES = {"powometer", "avalanche_canada"}
MOUNT_METHODS = {"bare_ground", "tape", "chained", "field_offset"}
QUALITIES = {"good", "estimate"}
OPS = {"delete", "spike_filter", "threshold", "gap_fill", "offset", "drift"}
OP_PARAMS = {"delete": set(), "spike_filter": {"max_step", "window"},
             "threshold": {"min", "max"}, "gap_fill": {"max_gap"},
             "offset": {"value"}, "drift": {"start", "end"}}
VARIABLES = {"all", "snow_depth", "distance_to_surface", "air_temperature",
             "relative_humidity", "air_pressure", "battery_voltage", "enclosure_temperature"}
STATISTICS = {"min", "median"}
REMOUNT = {True, False, "probable", "unknown"}
INITIALS = re.compile(r"^[A-Z]{2,4}$")


@dataclass
class Config:
    stations: list[dict]
    sites: list[dict]
    eras: list[dict]
    no_echo_cm: list[int]
    mounts: list[dict]
    visits: list[dict]
    corrections: list[dict]
    approvals: list[dict]
    monitoring: dict


def _load(name: str, config_dir: Path) -> Any:
    with open(config_dir / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load(config_dir: Path = CONFIG) -> Config:
    fw = _load("firmware_versions.yaml", config_dir)
    return Config(
        stations=_load("stations.yaml", config_dir).get("stations", []),
        sites=_load("sites.yaml", config_dir).get("sites", []),
        eras=fw.get("eras", []),
        no_echo_cm=fw.get("no_echo_cm", []),
        mounts=_load("mounts.yaml", config_dir).get("mounts", []),
        visits=_load("field_visits.yaml", config_dir).get("visits", []),
        corrections=_load("corrections.yaml", config_dir).get("corrections", []) or [],
        approvals=_load("approvals.yaml", config_dir).get("approvals", []) or [],
        monitoring=_load("monitoring.yaml", config_dir),
    )


def as_utc(v) -> datetime | None:
    """YAML gives timezone-aware datetimes for '...Z' values; normalise."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
    raise TypeError(f"not a time: {v!r}")


def _period_problems(where: str, items: list[dict], key: str = "id") -> list[str]:
    """from < until, and no overlaps between consecutive periods."""
    out, spans = [], []
    for it in items:
        name = f"{where} {it.get(key, '?')}"
        try:
            a, b = as_utc(it.get("from")), as_utc(it.get("until"))
        except Exception:
            out.append(f"{name}: 'from'/'until' must be UTC times like 2026-01-01T00:00:00Z")
            continue
        if a is None:
            out.append(f"{name}: 'from' is missing")
            continue
        if b is not None and b <= a:
            out.append(f"{name}: 'until' ({b:%Y-%m-%d %H:%M}) must be after 'from' ({a:%Y-%m-%d %H:%M})")
        spans.append((a, b, name))
    spans.sort(key=lambda s: s[0])
    for (a1, b1, n1), (a2, _b2, n2) in zip(spans, spans[1:]):
        if b1 is None or b1 > a2:
            out.append(f"{n1} overlaps {n2}")
    return out


def validate(config_dir: Path = CONFIG, root: Path = ROOT) -> list[str]:
    try:
        c = load(config_dir)
    except yaml.YAMLError as e:
        return [f"A config file is not valid YAML (often an indentation mistake): {e}"]
    except FileNotFoundError as e:
        return [f"Missing config file: {e.filename}"]
    p: list[str] = []

    # stations
    ids = [s.get("id") for s in c.stations]
    if len(ids) != len(set(ids)):
        p.append("stations.yaml: station ids must be unique")
    for s in c.stations:
        if s.get("type") not in DECODER_TYPES:
            p.append(f"stations.yaml {s.get('id')}: unknown type {s.get('type')!r} (known: {sorted(DECODER_TYPES)})")
        for k in ("enabled", "publish"):
            if not isinstance(s.get(k), bool):
                p.append(f"stations.yaml {s.get('id')}: '{k}' must be true or false")
    station_ids = set(ids)

    # sites
    for st in station_ids:
        p += _period_problems("sites.yaml site", [x for x in c.sites if x.get("station") == st])
    site_ids = {x.get("id") for x in c.sites}
    for x in c.sites:
        if x.get("station") not in station_ids:
            p.append(f"sites.yaml {x.get('id')}: unknown station {x.get('station')!r}")

    # firmware eras
    p += _period_problems("firmware_versions.yaml era", c.eras, key="name")
    for e in c.eras:
        if e.get("radio_distance_statistic") not in STATISTICS:
            p.append(f"firmware_versions.yaml {e.get('name')}: radio_distance_statistic must be one of {sorted(STATISTICS)}")

    # mounts
    p += _period_problems("mounts.yaml mount", c.mounts)
    for m in c.mounts:
        name = f"mounts.yaml {m.get('id')}"
        if m.get("site") not in site_ids:
            p.append(f"{name}: unknown site {m.get('site')!r}")
        if not isinstance(m.get("reference_m"), (int, float)) or not 0.5 < m["reference_m"] < 10:
            p.append(f"{name}: reference_m must be a distance in metres (0.5-10)")
        if m.get("method") not in MOUNT_METHODS:
            p.append(f"{name}: method must be one of {sorted(MOUNT_METHODS)}")
        if m.get("quality") not in QUALITIES:
            p.append(f"{name}: quality must be one of {sorted(QUALITIES)}")
        site = next((x for x in c.sites if x.get("id") == m.get("site")), None)
        if site:
            try:
                if as_utc(m["from"]) < as_utc(site["from"]) or (
                        site.get("until") and (m.get("until") is None or as_utc(m["until"]) > as_utc(site["until"]))):
                    p.append(f"{name}: must lie within its site's from/until")
            except Exception:
                pass

    # field visits
    for v in c.visits:
        name = f"field_visits.yaml {v.get('date')}"
        if not isinstance(v.get("date"), date):
            p.append(f"{name}: 'date' must be a date like 2026-09-26")
        if not INITIALS.match(str(v.get("who", ""))):
            p.append(f"{name}: 'who' must be initials (2-4 capital letters); this repository is public")
        if v.get("remount") not in REMOUNT:
            p.append(f"{name}: 'remount' must be true, false, probable or unknown")
        d = v.get("disturbed")
        if d is not None:
            p += _period_problems("field_visits.yaml visit", [{"id": v.get("date"), **d}])

    # corrections
    cids = [x.get("id") for x in c.corrections]
    if len(cids) != len(set(cids)):
        p.append("corrections.yaml: ids must be unique")
    for x in c.corrections:
        name = f"corrections.yaml {x.get('id')}"
        op = x.get("op")
        if op not in OPS:
            p.append(f"{name}: unknown op {op!r} (known: {sorted(OPS)})")
        else:
            missing = OP_PARAMS[op] - set((x.get("params") or {}).keys())
            if missing:
                p.append(f"{name}: '{op}' needs params {sorted(missing)}")
        if x.get("station") not in station_ids:
            p.append(f"{name}: unknown station {x.get('station')!r}")
        if x.get("variable") not in VARIABLES:
            p.append(f"{name}: unknown variable {x.get('variable')!r} (known: {sorted(VARIABLES)})")
        has_msg, has_win = "message" in x, "from" in x or "until" in x
        if has_msg == has_win:
            p.append(f"{name}: give either 'message' (one transmit time) or 'from'/'until', not both or neither")
        if has_win:
            p += _period_problems("corrections.yaml", [x])
        if has_msg:
            try:
                as_utc(x["message"])
            except Exception:
                p.append(f"{name}: 'message' must be a UTC time like 2026-09-26T20:39:40Z")
        for k in ("reason", "by", "date"):
            if not x.get(k):
                p.append(f"{name}: '{k}' is required")
        if x.get("by") and not INITIALS.match(str(x["by"])):
            p.append(f"{name}: 'by' must be initials")

    # approvals
    for a in c.approvals:
        name = f"approvals.yaml {a.get('snapshot')}"
        f = root / str(a.get("snapshot", ""))
        if not f.is_file():
            p.append(f"{name}: snapshot file not found")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != a.get("sha256"):
            p.append(f"{name}: snapshot file does not match its sha256 (approved data must not change)")
    return p
