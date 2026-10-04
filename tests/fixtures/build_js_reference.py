#!/usr/bin/env python3
"""Build tests/fixtures/js_reference_decode.json from the ORIGINAL JavaScript.

Runs `parsePayload`, `clockOffsetMinutes` and `measurementTimes` from
scripts/apps_script/Code.gs (the logic deployed in Google Apps Script) on
every message in raw/radio/rockblock-export-2026-09-29.csv, so the Python port
can be tested message by message. Requires the `quickjs` package, which is
only needed to regenerate this fixture (not to run the tests).

Usage: python tests/fixtures/build_js_reference.py tests/fixtures/apps_script_stubs.js
(apps_script_stubs.js mocks the Google services: Utilities, Sheets, Gmail.)
"""
import csv, json, sys
from datetime import datetime, timezone
import quickjs

ROOT = __file__.rsplit("tests", 1)[0]
stubs = open(sys.argv[1], encoding="utf-8").read()
code = open(ROOT + "scripts/apps_script/Code.gs", encoding="utf-8").read()
props = ("const PropertiesService={getScriptProperties(){return {getProperty(k){"
         "return {STATION_IMEI:'TEST-IMEI',SPREADSHEET_ID:'x'}[k];}};}};")
c = quickjs.Context(); c.eval(stubs); c.eval(props); c.eval(code)
out = []
for r in csv.DictReader(open(ROOT + "raw/radio/rockblock-export-2026-09-29.csv", encoding="utf-8")):
    if r["Direction"] != "MO":
        continue
    t = datetime.strptime(r["Date Time (UTC)"], "%d/%b/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    text = bytes.fromhex(r["Payload"]).decode("ascii", "replace").strip()
    c.set("P", text); c.set("T", int(t.timestamp() * 1000))
    res = json.loads(c.eval("""JSON.stringify((() => {
        const p = parsePayload(P); if (!p) return null;
        const tx = new Date(T); const mt = measurementTimes(p, tx);
        return {p: p, off: clockOffsetMinutes(p, tx), times: mt.times, source: mt.source};
    })())"""))
    out.append({"transmit_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "payload": text, "js": res})
json.dump(out, open(ROOT + "tests/fixtures/js_reference_decode.json", "w"), indent=0)
print(len(out), "messages;", sum(1 for o in out if o["js"]), "decoded by JS")
