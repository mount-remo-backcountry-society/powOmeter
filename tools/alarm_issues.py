#!/usr/bin/env python3
"""Open and close GitHub Issues for station alarms (design §6.7).

Usage (in the publish workflow, with GH_TOKEN set):
  python tools/alarm_issues.py out/v1/status.json

Each alarm in status.json has a stable id. An alarm without an open Issue
gets one (label "alarm"); an open alarm Issue whose alarm has cleared is
closed with a comment. People who "watch" the repository get one email per
event. Needs the GitHub CLI `gh` (preinstalled on GitHub's runners).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone

LABEL = "alarm"
MARKER = re.compile(r"<!-- alarm-id: (\S+) -->")
HELP = ("What to do: see OPERATIONS.md, \"When an alarm Issue opens\". "
        "This Issue closes by itself when the alarm clears. "
        "Station data are not a forecast.")


def plan(alarms: list[dict], open_issues: list[dict]) -> tuple[list[dict], list[int]]:
    """(alarms needing a new Issue, numbers of Issues to close)."""
    open_ids = {}
    for i in open_issues:
        m = MARKER.search(i.get("body") or "")
        if m:
            open_ids[m.group(1)] = i["number"]
    current = {a["id"] for a in alarms}
    to_open = [a for a in alarms if a["id"] not in open_ids]
    to_close = sorted(n for id_, n in open_ids.items() if id_ not in current)
    return to_open, to_close


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main(path: str) -> int:
    status = json.load(open(path, encoding="utf-8"))
    alarms = status.get("alarms", [])
    gh("label", "create", LABEL, "--color", "D93F0B", "--description", "Station alarm (opened by the publish workflow)", "--force")
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "200",
                           "--json", "number,body"))
    to_open, to_close = plan(alarms, issues)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for a in to_open:
        title = "Alarm: " + (a["message"] if len(a["message"]) <= 90 else a["message"][:87] + "...")
        body = f"{a['message']}\n\nStarted: {now} (build of {status.get('built_utc')}).\n\n{HELP}\n\n<!-- alarm-id: {a['id']} -->"
        gh("issue", "create", "--label", LABEL, "--title", title, "--body", body)
        print(f"opened: {a['id']}")
    for n in to_close:
        gh("issue", "close", str(n), "--comment", f"Cleared: {now}.")
        print(f"closed: #{n}")
    print(f"alarm_issues: {len(alarms)} alarm(s); {len(to_open)} opened, {len(to_close)} closed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out/v1/status.json"))
