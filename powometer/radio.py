"""Load satellite messages from raw/radio/.

Two sources, combined into one list of messages:

* `rockblock-export-*.csv`: RockBLOCK console history (no MOMSN; times like
  `29/Sep/2026 18:16:25` UTC; payload as hex). The backstop for everything.
* `messages_tab/*.jsonl`: one file per fetch from the Apps Script's Messages
  tab (MOMSN, session status, CEP). Files are only ever ADDED, one per fetch,
  never appended to, because raw/ is immutable and messages can arrive
  months late.

A message is identified by (transmit_utc, payload text). When both sources
have it, the result keeps the export's payload and adds the MOMSN, status and
CEP from the Messages tab.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from . import RAW
from .decoders.powometer_payload import hex_to_text


@dataclass(frozen=True)
class Message:
    transmit_utc: datetime
    payload_text: str
    momsn: int | None = None
    session_status: int | None = None
    cep_km: float | None = None
    sources: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[datetime, str]:
        return (self.transmit_utc, self.payload_text)


def _utc(s: str, fmt: str) -> datetime:
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def load_console_export(path: Path) -> list[Message]:
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("Direction") != "MO":
                continue
            out.append(Message(
                transmit_utc=_utc(r["Date Time (UTC)"], "%d/%b/%Y %H:%M:%S"),
                payload_text=hex_to_text(r["Payload"]),
                sources=(f"console:{path.name}",),
            ))
    return out


def load_messages_tab(path: Path) -> list[Message]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if not r.get("transmit_utc"):
                continue
            text = r.get("payload_text") or hex_to_text(r.get("payload_hex", ""))
            out.append(Message(
                transmit_utc=_utc(r["transmit_utc"], "%Y-%m-%dT%H:%M:%SZ"),
                payload_text=text.strip(),
                momsn=r.get("momsn"),
                session_status=r.get("session_status"),
                cep_km=r.get("cep_km"),
                sources=(f"messages_tab:{path.name}",),
            ))
    return out


def load_all(raw_dir: Path = RAW) -> list[Message]:
    """All messages, de-duplicated on (transmit_utc, payload), sorted by time.
    Messages without a payload ("No Data" sessions) are kept out of the data
    path but counted by the caller if needed."""
    radio = raw_dir / "radio"
    msgs: list[Message] = []
    for p in sorted(radio.glob("rockblock-export-*.csv")):
        msgs += load_console_export(p)
    for p in sorted((radio / "messages_tab").glob("*.jsonl")):
        msgs += load_messages_tab(p)

    merged: dict[tuple[datetime, str], Message] = {}
    for m in msgs:
        if not m.payload_text:
            continue
        prev = merged.get(m.key)
        if prev is None:
            merged[m.key] = m
        else:
            merged[m.key] = replace(
                prev,
                momsn=prev.momsn if prev.momsn is not None else m.momsn,
                session_status=prev.session_status if prev.session_status is not None else m.session_status,
                cep_km=prev.cep_km if prev.cep_km is not None else m.cep_km,
                sources=tuple(sorted(set(prev.sources) | set(m.sources))),
            )
    return sorted(merged.values(), key=lambda m: m.transmit_utc)
