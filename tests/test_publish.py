"""Phase 2: fetching messages, health checks, alarm Issues."""
import importlib.util
import io
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


def mailbox(*msgs):
    """The Worker's /messages answer (worker/src/index.js)."""
    return json.dumps({"messages": [{"transmit_utc": t, "momsn": n, "cep_km": 4, "payload_hex": p.encode().hex()}
                                    for t, n, p in msgs]})


def test_mailbox_saves_only_new_messages_in_a_new_file(raw):
    box = mailbox(("2026-09-29T18:16:25Z", 3149, old_payload()), ("2026-10-05T00:31:42Z", 3190, "0417313381+07096"))
    path, held = intake.fetch_mailbox("", "", raw, NOW, box)
    assert len(held) == 2
    assert path.relative_to(raw).as_posix() == "radio/worker/2026-10-05T050000Z_worker.jsonl"
    recs = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert [(r["momsn"], r["transmit_utc"], r["payload_text"], r["intake"]) for r in recs] == [
        (3190, "2026-10-05T00:31:42Z", "0417313381+07096", "worker")]
    assert intake.fetch_mailbox("", "", raw, NOW + timedelta(hours=1), box)[0] is None  # nothing new
    assert sum(m.payload_text == "0417313381+07096" for m in load_all(raw)) == 1


def test_a_message_that_comes_both_ways_is_one_message(raw):
    intake.fetch_mailbox("", "", raw, NOW, mailbox(("2026-10-05T00:31:42Z", 3190, "0417313381+07096")))
    path = intake.fetch("", raw, NOW + timedelta(minutes=5), CSV.format(old=old_payload()))
    recs = [json.loads(line) for line in open(path, encoding="utf-8")]
    # the sheet saves its own copy of what the Worker already had (the routes are compared)
    assert [r["transmit_utc"] for r in recs] == ["2026-10-05T00:31:10Z", "2026-10-05T00:31:42Z"]
    same = [m for m in load_all(raw) if m.payload_text == "0417313381+07096"]
    assert len(same) == 1 and same[0].session_status == 0
    assert sorted(s.split(":")[0] for s in same[0].sources) == ["messages_tab", "worker"]
    assert intake.fetch("", raw, NOW + timedelta(minutes=15), CSV.format(old=old_payload())) is None
    assert intake.fetch_mailbox("", "", raw, NOW + timedelta(minutes=15),
                                mailbox(("2026-10-05T00:31:42Z", 3190, "0417313381+07096")))[0] is None


def test_mailbox_report_says_whether_the_newest_sheet_message_came_both_ways(raw):
    last = [m for m in load_all(raw) if any(s.startswith("messages_tab:") for s in m.sources)][-1]
    t = f"{last.transmit_utc:%Y-%m-%dT%H:%M:%SZ}"
    _, held = intake.fetch_mailbox("", "", raw, NOW, mailbox((t, last.momsn, last.payload_text)))
    assert intake.mailbox_report(held, raw) == (f"mailbox: 1 message(s), newest MOMSN {last.momsn} sent {t}; "
                                                f"newest sheet message (MOMSN {last.momsn}, sent {t}) is in the mailbox")
    assert intake.mailbox_report([], raw).startswith("mailbox: empty; ")
    assert intake.mailbox_report([], raw).endswith("is NOT in the mailbox")


class FakeGoogle:
    """Stands in for urlopen: records each request, refuses some links."""
    def __init__(self, refuse=lambda url: False):
        self.requests, self.refuse = [], refuse

    def __call__(self, req, timeout):
        self.requests.append(req)
        if self.refuse(req.full_url):
            raise intake.urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
        return io.BytesIO(HEADER.encode())


def test_sheet_is_asked_for_a_fresh_copy(monkeypatch):
    google = FakeGoogle()
    monkeypatch.setattr(intake.urllib.request, "urlopen", google)
    assert intake.download("https://example.org/pub?output=csv", now=NOW) == HEADER
    (req,) = google.requests
    assert req.full_url == f"https://example.org/pub?output=csv&nocache={int(NOW.timestamp())}"
    assert req.get_header("Cache-control") == "no-cache"


def test_plain_link_if_google_refuses_the_extra_parameter(monkeypatch):
    google = FakeGoogle(refuse=lambda url: "nocache=" in url)
    monkeypatch.setattr(intake.urllib.request, "urlopen", google)
    assert intake.download("https://example.org/pub", now=NOW) == HEADER
    assert [r.full_url for r in google.requests] == [f"https://example.org/pub?nocache={int(NOW.timestamp())}",
                                                     "https://example.org/pub"]


def test_malformed_mailbox_fails_without_saving(raw):
    worker = raw / "radio" / "worker"
    before = sorted(worker.iterdir()) if worker.exists() else []    # the real archive's files
    with pytest.raises(ValueError):
        intake.fetch_mailbox("", "", raw, NOW, mailbox(("17/Oct/2026", 1, "0417313381+07096")))
    assert (sorted(worker.iterdir()) if worker.exists() else []) == before


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


LIVE = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
ROUTES = {"route_missing_hours": 1, "routes_since": "2026-10-22T18:21:00Z"}


def by(minutes_ago, *routes, momsn=3310):
    return Message(LIVE - timedelta(minutes=minutes_ago), "2304003385+00490", momsn=momsn,
                   sources=tuple(f"{r}:x.jsonl" for r in routes))


def test_route_alarm_when_one_route_misses_a_message_for_an_hour():
    assert monitor.route_alarms(ROUTES, [by(90, "worker", "messages_tab")], LIVE) == []
    assert monitor.route_alarms(ROUTES, [by(30, "worker")], LIVE) == []            # the sheet is slower
    (a,) = monitor.route_alarms(ROUTES, [by(90, "worker", momsn=3309), by(30, "worker")], LIVE)
    assert a["id"] == "route_messages_tab"
    assert a["message"].startswith("1 message(s) came by the Cloudflare Worker but not by the Google Sheet "
                                   "within 1 h (MOMSN 3309 sent 2026-10-23 10:30 UTC); Avalanche Canada")
    (a,) = monitor.route_alarms(ROUTES, [by(90, "messages_tab")], LIVE)
    assert a["id"] == "route_worker" and "Observability" in a["message"]


def test_route_alarm_ignores_old_messages_and_can_be_switched_off():
    before = Message(datetime(2026, 10, 22, 16, 8, tzinfo=timezone.utc), "2208073382-01795",
                     sources=("messages_tab:x.jsonl",))                    # MOMSN 3305: no Worker yet
    assert monitor.route_alarms(ROUTES, [before], datetime(2026, 10, 22, 20, tzinfo=timezone.utc)) == []
    assert monitor.route_alarms(ROUTES, [by(25 * 60, "worker")], LIVE) == []       # older than a day: cleared
    assert monitor.route_alarms({}, [by(90, "worker")], LIVE) == []
    assert "route_messages_tab" in ids(monitor.check(load(), [by(90, "worker")], LIVE))  # config is on


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
