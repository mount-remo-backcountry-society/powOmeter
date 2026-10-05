"""Phase 2: fetching messages, health checks, alarm Issues."""
import importlib.util
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from powometer import RAW, intake, monitor
from powometer.config import load
from powometer.radio import Message, load_all

NOW = datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)
HEADER = ("Processed (UTC),MOMSN,Transmit Time (UTC),Session Status,CEP (km),Payload,Readings,"
          "Battery Level,Station Clock Offset (min),Result\n")
# A real message already archived (2026-09-29 export), one new message with a
# one-digit hour as the sheet writes it, and the same MOMSN as an empty session.
CSV = HEADER + (
    "2026-09-29 18:20:00,3149,2026-09-29 18:16:25,0,3,{old},1,8,0,ok: 1 rows\n"
    "2026-10-05 0:35:00,3190,2026-10-05 0:31:10,13,4,,,,,no data (session status 13)\n"
    "2026-10-05 0:35:00,3190,2026-10-05 0:31:42,0,4,0417313381+07096,1,3,-2,\"ok: 1 rows, station time\"\n"
)


@pytest.fixture
def raw(tmp_path):
    shutil.copytree(RAW / "radio", tmp_path / "radio")
    return tmp_path


def old_payload():
    return next(m for m in load_all() if m.momsn == 3149).payload_text


def test_fetch_saves_only_new_messages_in_a_new_file(raw):
    text = CSV.format(old=old_payload())
    path = intake.fetch("", raw, NOW, text)
    assert path.name == "2026-10-05T050000Z_published-csv.jsonl"
    recs = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert [(r["momsn"], r["transmit_utc"], r["payload_text"]) for r in recs] == [
        (3190, "2026-10-05T00:31:10Z", ""), (3190, "2026-10-05T00:31:42Z", "0417313381+07096")]
    assert intake.fetch("", raw, NOW + timedelta(hours=1), text) is None      # nothing new
    assert any(m.payload_text == "0417313381+07096" for m in load_all(raw))


def test_fetch_never_overwrites_a_raw_file(raw):
    text = CSV.format(old=old_payload())
    intake.fetch("", raw, NOW, text)
    with pytest.raises(FileExistsError):
        intake.fetch("", raw, NOW, text.replace("3190", "3191"))


def test_changed_sheet_layout_fails_loudly(raw):
    with pytest.raises(ValueError, match="expected columns"):
        intake.fetch("", raw, NOW, "MOMSN,When,Data\n1,2,3\n")


# --- health checks --------------------------------------------------------

def msg(minutes_ago, payload="0417313381+07096"):
    return Message(NOW - timedelta(minutes=minutes_ago), payload, momsn=1)


def ids(alarms):
    return [a["id"] for a in alarms]


def test_healthy_station_has_no_alarms():
    # header 04 17:31 station time (UTC-8) = 05 01:31 UTC; sent 01:31 UTC
    assert monitor.check(load(), [Message(datetime(2026, 10, 5, 1, 31, tzinfo=timezone.utc),
                                          "0417313381+07096")], NOW) == []


def test_no_message_only_in_season():
    m = [Message(datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc), "0407003381+07096")]
    assert ids(monitor.check(load(), m, NOW)) == ["no_message"]          # 14 h, October
    summer = datetime(2026, 7, 5, 5, 0, tzinfo=timezone.utc)
    m = [Message(summer - timedelta(days=3), "0121003381+07096")]   # 07-01 21:00 station time
    assert monitor.check(load(), m, summer) == []


def test_season_wraps_over_new_year():
    c = load()
    assert monitor.in_season(c, datetime(2027, 1, 15)) and monitor.in_season(c, datetime(2026, 10, 1))
    assert not monitor.in_season(c, datetime(2026, 8, 1))


def test_clock_battery_and_decode_alarms():
    sent = datetime(2026, 10, 5, 2, 31, tzinfo=timezone.utc)      # header says 01:31 UTC: 60 min off
    a = monitor.check(load(), [Message(sent, "0417311381+07096")], NOW)
    assert ids(a) == ["clock_offset", "battery"]                  # level 1 = 3.4-3.5 V
    assert "below 3.5 V" in a[1]["message"]
    bad = Message(NOW - timedelta(hours=1), "garbled")
    assert "decode_errors" in ids(monitor.check(load(), [msg(30), bad], NOW))
    old_bad = Message(NOW - timedelta(days=2), "garbled")
    assert "decode_errors" not in ids(monitor.check(load(), [old_bad, msg(30)], NOW))


def test_build_and_injected_alarms_have_stable_ids():
    a = monitor.check(load(), [], datetime(2026, 7, 1, tzinfo=timezone.utc), ["SD x: unreadable"], "fault")
    b = monitor.check(load(), [], datetime(2026, 7, 2, tzinfo=timezone.utc), ["SD x: unreadable"], "fault")
    assert ids(a) == ids(b) and ids(a)[1] == "test-injected" and ids(a)[0].startswith("build-")


# --- alarm Issues ---------------------------------------------------------

spec = importlib.util.spec_from_file_location("alarm_issues", Path(__file__).parent.parent / "tools" / "alarm_issues.py")
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)


def test_issues_open_once_and_close_when_cleared():
    open_issues = [{"number": 7, "body": "x\n<!-- alarm-id: battery -->"},
                   {"number": 8, "body": "y\n<!-- alarm-id: no_message -->"},
                   {"number": 9, "body": "opened by a person, no marker"}]
    alarms = [{"id": "battery", "message": "low"}, {"id": "clock_offset", "message": "off"}]
    to_open, to_close = ai.plan(alarms, open_issues)
    assert [a["id"] for a in to_open] == ["clock_offset"]
    assert to_close == [8]
