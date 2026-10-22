"""Fetch new satellite messages from the Google Sheet's Messages tab, and
from the Cloudflare Worker's mailbox (worker/src/index.js).

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

The Worker's mailbox (address and read key: GitHub secrets MAILBOX_URL and
MAILBOX_READ_KEY) is saved the same way, to raw/radio/worker/. A message
that comes both ways is recognised as one by radio.load_all.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import RAW
from .decoders.powometer_payload import hex_to_text
from .radio import JSONL_INTAKES, load_console_export, load_messages_tab

TAB_DIR = RAW / "radio" / "messages_tab"
WORKER_DIR = RAW / "radio" / "worker"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"           # the sheet writes "2026-05-24 0:24:54" (UTC)
COLUMNS = ("MOMSN", "Transmit Time (UTC)", "Session Status", "CEP (km)", "Payload")


def download(url: str, timeout: int = 60, now: datetime | None = None) -> str:
    """The published CSV, asked for fresh. The plain link can come back hours
    out of date: on 2026-10-22 MOMSN 3306 was in the sheet from 18:26Z and a
    browser got it, but the runs kept getting the 16:10Z copy until at least
    21:10Z. A throw-away parameter and no-cache headers ask for a new copy;
    if Google refuses the parameter, the plain link is used."""
    stamp = int((now or datetime.now(timezone.utc)).timestamp())
    fresh = f"{url}{'&' if '?' in url else '?'}nocache={stamp}"
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache", "User-Agent": "powometer"}
    try:
        return _get(fresh, headers, timeout)
    except urllib.error.HTTPError:
        return _get(url, headers, timeout)


def _get(url: str, headers: dict, timeout: int) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
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
    for folder in JSONL_INTAKES:
        for p in sorted((radio / folder).glob("*.jsonl")):
            for m in load_messages_tab(p):
                keys.add(_key(f"{m.transmit_utc:%Y-%m-%dT%H:%M:%SZ}", m.momsn, m.payload_text))
    return keys


def fetch(url: str, raw_dir: Path = RAW, now: datetime | None = None, text: str | None = None) -> Path | None:
    """Download the tab and archive the messages not seen before. Returns the
    new file, or None when there was nothing new."""
    now = now or datetime.now(timezone.utc)
    records = parse(download(url, now=now) if text is None else text, now)
    return _archive(records, raw_dir, "messages_tab", f"{now:%Y-%m-%dT%H%M%SZ}_published-csv.jsonl")


def parse_mailbox(text: str, fetched: datetime) -> list[dict]:
    """The Worker's mailbox (JSON) as raw records, like the Messages tab's."""
    out = []
    for m in json.loads(text)["messages"]:
        if not m.get("transmit_utc") or not m.get("payload_hex"):
            continue
        datetime.strptime(m["transmit_utc"], "%Y-%m-%dT%H:%M:%SZ")      # refuses a malformed time
        out.append({
            "cep_km": m.get("cep_km"),
            "intake": "worker",
            "momsn": m.get("momsn"),
            "payload_hex": m["payload_hex"],
            "payload_text": hex_to_text(m["payload_hex"]).strip(),
            "session_status": None,               # RockBLOCK delivers only completed sessions
            "source": f"Cloudflare Worker mailbox (RockBLOCK HTTP delivery), fetched {fetched:%Y-%m-%dT%H:%M:%SZ}",
            "transmit_utc": m["transmit_utc"],
        })
    return out


def fetch_mailbox(url: str, read_key: str, raw_dir: Path = RAW, now: datetime | None = None,
                  text: str | None = None) -> tuple[Path | None, list[dict]]:
    """Read the Worker's mailbox and archive the messages not seen before
    (by either route). Returns the new file (or None) and everything the
    mailbox holds."""
    now = now or datetime.now(timezone.utc)
    if text is None:
        req = urllib.request.Request(url.rstrip("/") + "/messages",
                                     headers={"Authorization": f"Bearer {read_key}", "User-Agent": "powometer"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8")
    records = parse_mailbox(text, now)
    return _archive(records, raw_dir, "worker", f"{now:%Y-%m-%dT%H%M%SZ}_worker.jsonl"), records


def mailbox_report(records: list[dict], raw_dir: Path = RAW) -> str:
    """One line for the run's log: what the mailbox holds, and whether the
    newest message that came by the sheet is in it (the two routes should
    carry the same messages)."""
    from .radio import load_all
    if records:
        last = max(records, key=lambda r: r["transmit_utc"])
        held = f"{len(records)} message(s), newest MOMSN {last['momsn']} sent {last['transmit_utc']}"
    else:
        held = "empty"
    sheet = [m for m in load_all(raw_dir) if any(s.startswith("messages_tab:") for s in m.sources)]
    if not sheet:
        return f"mailbox: {held}"
    s = sheet[-1]
    t = f"{s.transmit_utc:%Y-%m-%dT%H:%M:%SZ}"
    inbox = any(r["transmit_utc"] == t and r["payload_text"] == s.payload_text for r in records)
    return (f"mailbox: {held}; newest sheet message (MOMSN {s.momsn}, sent {t}) "
            + ("is in the mailbox" if inbox else "is NOT in the mailbox"))


def _archive(records: list[dict], raw_dir: Path, folder: str, name: str) -> Path | None:
    known = known_keys(raw_dir)
    new, seen = [], set()
    for r in records:
        k = _key(r["transmit_utc"], r["momsn"], r["payload_text"])
        if k not in known and k not in seen:
            new.append(r)
            seen.add(k)
    if not new:
        return None
    path = raw_dir / "radio" / folder / name
    if path.exists():
        raise FileExistsError(f"{path} exists; raw files are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in sorted(new, key=lambda r: (r["transmit_utc"], r["momsn"] or 0)):
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return path
