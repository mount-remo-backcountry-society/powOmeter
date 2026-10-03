#!/usr/bin/env python3
"""Fail if any EXISTING file under raw/ was modified, deleted or renamed.

Adding new raw files is fine; changing old ones is not (design §5.1).

Usage: python tools/raw_immutable.py BASE_SHA HEAD_SHA
A BASE of all zeros (first push of a branch) checks nothing.
"""
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    base, head = sys.argv[1], sys.argv[2]
    if set(base) == {"0"}:
        print("raw_immutable: first push of this branch; nothing to compare")
        return 0
    out = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", base, head, "--", "raw/"],
        check=True, capture_output=True, text=True).stdout
    bad = [ln for ln in out.splitlines() if ln and ln[0] in "MDT"]
    if bad:
        print("raw_immutable: raw files must never change. Offending entries:")
        for ln in bad:
            print("  " + ln)
        print("Add a NEW file instead (e.g. a later download), and put corrections in config/corrections.yaml.")
        return 1
    added = sum(1 for ln in out.splitlines() if ln.startswith("A"))
    print(f"raw_immutable: OK ({added} raw file(s) added, none changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
