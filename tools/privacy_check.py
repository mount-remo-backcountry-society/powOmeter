#!/usr/bin/env python3
"""Privacy check: block private identifiers from entering the (public) repo.

Usage
  python tools/privacy_check.py              # scan all tracked files (CI)
  python tools/privacy_check.py --staged     # scan what is about to be committed
  python tools/privacy_check.py --history    # scan every file in every commit
  add --local                                # also check home coordinates (local hook only)

What it looks for (patterns only, so this file itself is safe to publish):
  * IMEI: any standalone 15-digit number (not part of a longer hex or
    alphanumeric string, so raw radio payloads, which are hex, don't trigger
    it). NOTE: no Luhn check-digit filter. The station's real Iridium IMEI
    does NOT pass Luhn, so such a filter would let the real secret through
    (found in testing, 2026-10-03). Also the 2-6-6-1 grouping with spaces
    or dashes, as printed on labels.
  * Google spreadsheet ID: 44 characters starting with "1", containing
    letters and digits (an ID may happen to be all one case)
  * Published-sheet ID: "2PACX-..."
  * Email addresses, except those listed in tools/privacy_allowlist.txt
  * Images (JPEG, HEIC, PNG) carrying a GPS location in their EXIF data
    (phone photos from site visits)
  * With --local only: coordinates within ~3 km of the home location listed
    in .private/home_coords.txt (git-ignored). The home location is never in
    the repo; that is why this check cannot run in CI.

Findings are printed masked, so a CI log never repeats the secret.
Exit code 1 if anything is found.
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "tools" / "privacy_allowlist.txt"
HOME_FILE = ROOT / ".private" / "home_coords.txt"
HOME_RADIUS_DEG = 0.03          # about 3 km in latitude

IMEI_RE = re.compile(r"(?<![0-9A-Za-z])\d{15}(?![0-9A-Za-z])")
# also as printed on labels and in some portals: 2-6-6-1 digits with spaces or dashes
IMEI_SEP_RE = re.compile(r"(?<![0-9A-Za-z-])\d{2}[- ]\d{6}[- ]\d{6}[- ]?\d(?![0-9A-Za-z-])")
SHEET_RE = re.compile(r"(?<![A-Za-z0-9_-])1[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")
PUBSHEET_RE = re.compile(r"2PACX-[A-Za-z0-9_-]{20,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FLOAT_RE = re.compile(r"-?\d{1,3}\.\d{2,}")      # 2 decimals already locate a house to ~1 km


def mask(s: str) -> str:
    return s[:3] + "..." + s[-2:] if len(s) > 6 else "..."


def load_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    return {ln.strip().lower() for ln in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def load_home() -> list[tuple[float, float]]:
    if not HOME_FILE.exists():
        return []
    out = []
    for ln in HOME_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            lat, lon = (float(x) for x in ln.split(",")[:2])
            out.append((lat, lon))
    return out


def scan_text(name: str, text: str, allow: set[str], home: list[tuple[float, float]]) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in IMEI_RE.finditer(line):
            hits.append(f"{name}:{n}: possible IMEI {mask(m.group())}")
        for m in IMEI_SEP_RE.finditer(line):
            hits.append(f"{name}:{n}: possible IMEI {mask(m.group())}")
        for m in SHEET_RE.finditer(line):
            v = m.group()
            if re.search(r"[A-Za-z]", v) and re.search(r"[0-9]", v[1:]):
                hits.append(f"{name}:{n}: possible spreadsheet ID {mask(v)}")
        for m in PUBSHEET_RE.finditer(line):
            hits.append(f"{name}:{n}: published-sheet ID {mask(m.group())}")
        for m in EMAIL_RE.finditer(line):
            if m.group().lower() not in allow:
                hits.append(f"{name}:{n}: email address {mask(m.group())}")
        if home:
            nums = [float(x) for x in FLOAT_RE.findall(line)]
            for hlat, hlon in home:
                if any(abs(x - hlat) < HOME_RADIUS_DEG for x in nums) and \
                   any(abs(x - hlon) < HOME_RADIUS_DEG * 2 for x in nums):
                    hits.append(f"{name}:{n}: coordinates near the home location")
    return hits


def _tiff_has_gps(b: bytes, start: int) -> bool:
    """True if the EXIF (TIFF) block at `start` has a GPS section with a
    latitude or longitude in it."""
    try:
        order = {b"II": "<", b"MM": ">"}[b[start:start + 2]]
        u16 = lambda o: struct.unpack_from(order + "H", b, start + o)[0]
        u32 = lambda o: struct.unpack_from(order + "I", b, start + o)[0]
        ifd0 = u32(4)
        gps = next((u32(ifd0 + 2 + 12 * i + 8) for i in range(u16(ifd0))
                    if u16(ifd0 + 2 + 12 * i) == 0x8825), None)
        if gps is None:
            return False
        return any(u16(gps + 2 + 12 * i) in (2, 4) for i in range(u16(gps)))
    except (KeyError, struct.error):
        return False


def image_has_gps(blob: bytes) -> bool:
    """Phone photos store where they were taken (EXIF GPS). Looks in JPEG and
    HEIC ("Exif\\0\\0" + TIFF) and PNG ("eXIf" chunk) files."""
    for marker in (b"Exif\x00\x00", b"eXIf"):
        i = blob.find(marker)
        while i != -1:
            if _tiff_has_gps(blob, i + len(marker)):
                return True
            i = blob.find(marker, i + 1)
    return False


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def decode(blob: bytes) -> str | None:
    if b"\x00" in blob[:8000]:
        return None             # binary
    return blob.decode("utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true")
    g.add_argument("--history", action="store_true")
    ap.add_argument("--local", action="store_true")
    a = ap.parse_args()

    allow = load_allowlist()
    home = load_home() if a.local else []
    if a.local and not home:
        print("privacy_check: WARNING .private/home_coords.txt missing; home check skipped")

    items: list[tuple[str, bytes]] = []
    if a.staged:
        for name in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0"):
            if name:
                items.append((name, git("show", f":{name}")))
    elif a.history:
        seen = set()
        for ln in git("rev-list", "--all", "--objects").decode().splitlines():
            parts = ln.split(" ", 1)
            if len(parts) == 2 and parts[0] not in seen:
                seen.add(parts[0])
                if git("cat-file", "-t", parts[0]).strip() == b"blob":
                    items.append((f"{parts[1]} ({parts[0][:8]})", git("cat-file", "-p", parts[0])))
    else:
        for name in git("ls-files", "-z").decode().split("\0"):
            if name and (ROOT / name).is_file():
                items.append((name, (ROOT / name).read_bytes()))

    hits = []
    for name, blob in items:
        text = decode(blob)
        if text is not None:
            hits += scan_text(name, text, allow, home)
        elif image_has_gps(blob):
            hits.append(f"{name}: image contains GPS location (EXIF); remove it before committing")

    mode = "staged" if a.staged else "history" if a.history else "tracked files"
    if hits:
        print(f"privacy_check: {len(hits)} finding(s) in {mode}:")
        for h in hits:
            print("  " + h)
        print("If a finding is a false positive, adjust the pattern or the allowlist; never commit the real value.")
        return 1
    print(f"privacy_check: OK ({len(items)} file(s) scanned, {mode}{', with home check' if home else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
