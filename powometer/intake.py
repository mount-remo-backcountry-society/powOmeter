"""Fetch new satellite messages from the Google Sheet's Messages tab.

The tab is published as CSV ("Publish to web"); its link is a GitHub
secret (MESSAGES_CSV_URL), never stored in the repository, because the
privacy check treats published-sheet IDs as private.

Each fetch that finds messages not yet in raw/radio/ writes them to a NEW
file, raw/radio/messages_tab/<fetch time>_published-csv.jsonl. Files are
never appended to or rewritten (raw data is immutable). A fetch with
nothing new writes nothing.

Only these columns are kept: message number (MOMSN), transmit time, session
status, position accuracy (CEP, km) and the payload. The tab's other
columns are derived by the Apps Script and are recomputed here anyway.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import RAW
from .radio import load_console_export, load_messages_tab

TAB_DIR = RAW / "radio" / "messages_tab"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"           # the sheet writes "2026-05-24 0:24:54" (UTC)
COLUMNS = ("MOMSN", "Transmit Time (UTC)", "Session Status", "CEP (km)", "Payload")


def download(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _num(v: str, kind=int):
    v = (v or "").strip()
    return kind(v) if v else None


def parse(text: str, fetched: datetime) -> list[dict]:
    """Rows of the published CSV as raw records (same fields as the other
    messages_tab files). Refuses a CSV without the expected columns, so a
    changed sheet layout fails loudly instead of archiving nonsense."""
    rows = list(csv.DictReader(io.StringIO(text)))
    missing = [c for c in COLUMNS if rows and c not in rows[0]]
    if missing or not text.strip():
        raise ValueError(f"Messages tab CSV lacks the expected columns {missing or list(COLUMNS)}")
    out = []
    for r in rows:
        t = (r["Transmit Time (UTC)"] or "").strip()
        if not t:
            continue
        transmit = datetime.strptime(t, TIME_FORMAT)
        payload = (r["Payload"] or "").strip()
        out.append({
            "cep_km": _num(r["CEP (km)"], float),
            "intake": "messages_tab",
            "momsn": _num(r["MOMSN"]),
            "payload_hex": payload.encode("ascii", "replace").hex(),
            "payload_text": payload,
            "session_status": _num(r["Session Status"]),
            "source": f"Messages tab of the Google Sheet, published CSV fetched {fetched:%Y-%m-%dT%H:%M:%SZ}",
            "transmit_utc": transmit.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return out


def _key(transmit_utc: str, momsn, payload: str) -> tuple:
    # The same MOMSN can appear twice: an empty session and a delivered one
    # (MOMSN 3184 on 2026-10-04), so the key includes time and payload.
    return (transmit_utc, momsn, payload)


def known_keys(raw_dir: Path = RAW) -> set[tuple]:
    """Keys of every message already archived, including "No Data" sessions."""
    keys = set()
    radio = raw_dir / "radio"
    for p in sorted(radio.glob("rockblock-export-*.csv")):
        for m in load_console_export(p):
            keys.add(_key(f"{m.transmit_utc:%Y-%m-%dT%H:%M:%SZ}", m.momsn, m.payload_text))
    for p in sorted((radio / "messages_tab").glob("*.jsonl")):
        for m in load_messages_tab(p):
            keys.add(_key(f"{m.transmit_utc:%Y-%m-%dT%H:%M:%SZ}", m.momsn, m.payload_text))
    return keys


def fetch(url: str, raw_dir: Path = RAW, now: datetime | None = None, text: str | None = None) -> Path | None:
    """Download the tab and archive the messages not seen before. Returns the
    new file, or None when there was nothing new."""
    now = now or datetime.now(timezone.utc)
    records = parse(download(url) if text is None else text, now)
    known = known_keys(raw_dir)
    new, seen = [], set()
    for r in records:
        k = _key(r["transmit_utc"], r["momsn"], r["payload_text"])
        if k not in known and k not in seen:
            new.append(r)
            seen.add(k)
    if not new:
        return None
    path = raw_dir / "radio" / "messages_tab" / f"{now:%Y-%m-%dT%H%M%SZ}_published-csv.jsonl"
    if path.exists():
        raise FileExistsError(f"{path} exists; raw files are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in sorted(new, key=lambda r: (r["transmit_utc"], r["momsn"] or 0)):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return path
