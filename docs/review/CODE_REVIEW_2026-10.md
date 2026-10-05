# Independent code review: phase 1 (2026-10-05)

Reviewer: Claude (Fable 5.1), working only from the repository files and
public documentation, per `docs/CODE_REVIEW_BRIEF.md`. Fresh virtual
environment (Python 3.12.11, pinned `requirements.txt`). Nothing tracked was
modified; this file is the only thing created in the repository.

What ran, and what it gave:

| Step | Result |
|---|---|
| `python -m pytest` | 2,643 passed (56 s) |
| `python -m powometer validate` | "Config OK" |
| `python -m powometer build`, twice | 569,044 observations, 358,347 best, 100,944 hourly; all CSV and JSON outputs byte-identical except `built_utc` in `status.json` |
| `python tools/privacy_check.py` / `--history` | OK: 136 tracked files, 156 history blobs; `main` equals `origin/main` |

The commands quoted below assume the repository root and the venv's Python.

## 1. Verdict

**Ready for phase 2 after changes.** The port is careful, the raw archive and
the labelling model are sound, the v0 reproduction and determinism claims
hold, and the privacy scan of the full history is clean. But two defects put
wrong snow depths into `best.csv` labelled `good` today (F1, F2), one
rounding mismatch with the firmware double-counts readings and silently
shapes the frozen-period times that the "independent" fixture also inherits
(F3), and three smaller bugs would produce wrong approved rows, wrong
snapshot windows or false clock alarms in phase 2 (F4–F6). All are small
fixes; F1–F4 should land, with tests, before anything is published.

## 2. Findings, most important first

### F1. The ultrasonic sensor's minimum-range sentinel (0.50 m) is published as a valid distance, so snow depths of 401–410 cm reach `best.csv` labelled `good`. Confirmed.

- **Where:** `powometer/qc.py:17-25` (`RANGES` lower bound 0.30 m; `NO_ECHO_M`
  covers only the far sentinel), `qc.py:46-64`.
- **What:** The MB7374 reports 500 mm for anything closer than 50 cm
  (`scripts/POW_O_METER_v1_3.ino:127`: "closer than 50 cm read as 50 cm").
  The SD record holds 310 median distances of exactly 0.498–0.500 m, in
  runs of up to 11 consecutive readings, with neighbours at 2.27 m (April
  2025), 4.6 m (June 2025, bare ground) and 1.20 m (February 2026). They are
  spurious short echoes, not snow. QC treats 4.90–5.20 m as `no_echo`
  (missing) but accepts 0.50 m as a measurement. The spike check catches
  isolated ones; runs of three or more set the rolling median to 0.50 and pass.
- **Result:** 90 median-distance rows at the floor are `good` in `best.csv`;
  their snow depths (4.51 − 0.50 = 401 cm at M1, 410 cm at M2b) are published
  as `good` (M1) or `estimate` (M2b) while the true depth was about 225 cm,
  about 0 cm and about 340 cm respectively. A 401 cm "good" depth on bare
  ground in June 2025 is the clearest case.
- **Reproduce:**
  ```
  python - <<'EOF'
  import pandas as pd
  b = pd.read_csv("out/v1/best.csv", keep_default_na=False, na_values={"value": [""]})
  d = b[(b.variable=="distance_to_surface") & (b.value<=0.50) & (b.quality=="good")]
  print(len(d), b[(b.variable=="snow_depth") & b.time_utc.isin(d.time_utc)].value.agg(["min","max"]).tolist())
  EOF
  ```
  Prints `90 [401.0, 410.0]`.
- **Fix:** Treat distance ≤ 0.50 m (or ≤ 0.51 m) like the far sentinel:
  value → missing, qualifier `sensor_min_range` (or `too_close`), quality
  `missing`; or at least `suspect`. Add a unit test with a run of five 0.500
  readings. Consider also flagging the burst when `min` is at the floor but
  `median` is not.

### F2. Corrections applied to `distance_to_surface` never reach the derived `snow_depth`: a `delete` leaves the derived depth `good` in `best.csv`. Confirmed.

- **Where:** `powometer/build.py:149-151` (QC and snow-depth derivation run
  before corrections), `powometer/corrections.py:42-96`, design §5.4 ("at
  every level").
- **What:** Snow depth is derived once in `qc.run` and copied; corrections
  then operate on whichever variable the entry names. A maintainer following
  `OPERATIONS.md` ("the variable") will naturally name the measured variable.
  `delete`, `threshold`, `spike_filter` on distance leave the derived depth
  `good`; `offset`/`drift` on distance leave the depth numerically
  inconsistent with the published distance.
- **Reproduce:** three readings at 2.0 m, one `delete` on
  `distance_to_surface` over the whole hour:
  ```
  python - <<'EOF'
  import pandas as pd
  from datetime import datetime, timedelta, timezone
  from powometer import qc, corrections
  from powometer.config import Config
  T0 = datetime(2026,1,1,tzinfo=timezone.utc)
  df = pd.DataFrame({"station_id":"powometer","site_id":"s","time_utc":[T0+timedelta(minutes=17*i) for i in range(3)],
       "variable":"distance_to_surface","statistic":"median","value":[2.0,2.0,2.0],"unit":"m","source":"sd",
       "time_method":"synced","message_utc":None,"qualifiers":""})
  c = Config(stations=[{"id":"powometer"}], sites=[], eras=[], no_echo_cm=[], visits=[], approvals=[], monitoring={},
       mounts=[{"id":"M","site":"s","from":T0-timedelta(days=1),"until":None,"reference_m":4.0,"quality":"good"}],
       corrections=[{"id":"C9","op":"delete","station":"powometer","variable":"distance_to_surface","from":T0,"until":T0+timedelta(hours=1),"reason":"t","by":"JK","date":T0.date()}])
  out = corrections.apply(qc.run(df, c), c)
  print(out[["variable","value","quality","level","qualifiers"]].to_string())
  EOF
  ```
  Distance rows become `poor`/`corrected`/`C9`; the three `snow_depth` rows
  stay `200.0 good qc`.
- **Fix:** Either derive snow depth *after* corrections (and re-derive after
  each correction that touches distance), or make a correction on
  `distance_to_surface` apply to the matching `snow_depth` rows
  (same time, statistic mapped through `DEPTH_STAT`, value offsets scaled
  ×100 and negated). Reject `variable: all` for `offset`/`drift` in
  `validate()`; adding one number to every variable is never right.

### F3. Radio and SD readings are matched with Python's round-half-to-even, but the firmware rounds half away from zero. Two readings are double-counted today; in the frozen period the same mismatch moves 267 reconstructed times by up to 87 minutes, and the v0 fixture shares the bug, so that test is not independent. Confirmed.

- **Where:** `powometer/assemble.py:125-131` (`_code`),
  `powometer/frozen.py:39-47` (`payload_code`); firmware
  `scripts/POW_O_METER_v1.1.ino:337-339` (`round()`),
  `scripts/POW_O_METER_v1_3.ino:715-717` (`lround()`), and the TRANSMIT
  row written with `String(airTemp, 1)` (one decimal) at `v1.1.ino:162`.
- **What:** `int(round(2505/10))` is 250 in Python and 251 on the station;
  `int(round(0.05*10))` is 0 in Python, while the station transmits
  `round("0.1"*10)` = 1. About 10 % of readings sit on a .5 mm/cm boundary.
  When the code mismatches, `is_duplicate` falls back to the 10-minute time
  test, which transmit-timed readings often fail (one is 62 min off).
- **Result today:** of the 74 "gap-filling" radio readings in
  `docs/DIFFERENCES_v0.md` #4, two are duplicates of SD rows already
  published: 2025-02-08 13:32:14Z (SD 14:50:00Z, 2.505 m / −13.33 °C / 76.9 %)
  and 2025-10-24 02:09:09Z (SD 01:06:55Z, 3.724 m / 0.05 °C / 99.1 %). The
  same physical reading appears twice at different times. With the SD card
  in the station all winter and a trusted clock (station-time readings carry
  the *uncorrected* device clock, SD rows the corrected one), every x.5
  reading whose clock offset exceeds 10 min will duplicate at the spring
  download.
- **Frozen period:** with firmware rounding `align()` accepts 240 anchors
  instead of 225, and 267 of the 1,584 reconstructed times change, by up to
  5,252 s. `tests/fixtures/reference_frozen_v0.csv` was built with the same
  Python `round()`, so `test_agrees_with_v0_outside_the_visit_window` cannot
  see this. The visit-download check (`test_rows_on_the_visit_download_predate_the_visit`)
  is genuinely independent but only bounds one row.
- **Reproduce:**
  ```
  python - <<'EOF'
  import math, pandas as pd
  from powometer import assemble, radio, config, frozen
  fw = lambda x: int(math.floor(abs(x)+0.5))*(1 if x>=0 else -1)
  def code(d,t,h):
      if any(v is None or pd.isna(v) for v in (d,t,h)): return None
      r = fw(h); return (fw(d*100), fw(round(t,1)*10), 0 if r>=100 else r)
  cfg, msgs = config.load(), radio.load_all()
  sdp, rp = assemble.sd_points(cfg, msgs), assemble.radio_points(cfg, msgs)
  a = assemble.merge(sdp, rp); n0 = a[a.source=="radio"].time_utc.nunique()
  assemble._code = code
  b = assemble.merge(sdp, rp); print("radio slots kept:", n0, "->", b[b.source=="radio"].time_utc.nunique())
  t0 = frozen.reconstruct(msgs)[1]
  frozen.payload_code = lambda p: code(float(p[3])/1000, float(p[6]), float(p[7]))
  t1 = frozen.reconstruct(msgs)[1]
  print("frozen rows moved:", sum(abs((x-y).total_seconds())>1 for x,y in zip(t0,t1)))
  EOF
  ```
  Prints `radio slots kept: 74 -> 72` and `frozen rows moved: 267`.
- **Fix:** one shared helper `firmware_round(x) = floor(|x| + 0.5)·sign(x)`,
  temperature rounded to one decimal first (as `TRANSMIT.csv` is), used in
  both `_code` and `payload_code`; unit tests on 2505 mm, 0.05 °C, 86.5 %.
  Then regenerate nothing: record the new anchor count and the changed rows
  in `DIFFERENCES_v0.md` as an intended difference from v0. Also widen the
  time fallback for transmit-timed readings (one queue interval, 66.5 min) or
  match on values with a ±1 cm tolerance.

### F4. A row that appears inside an approved period after approval (a late radio message) is published with an empty value, `quality` NaN and `approval: approved`, and it survives the `best.csv` filter. Confirmed.

- **Where:** `powometer/approval.py:65-69`, `powometer/build.py:157`.
- **What:** The alarm fires correctly (row counts differ), but the code then
  reindexes the snapshot onto *all* fresh rows in the window; rows without a
  snapshot counterpart get NaN value/quality/qualifiers and are stamped
  `approved`. `~quality.isin(["poor", "missing"])` lets NaN through.
- **Reproduce:** see the approval test in `tests/test_pipeline.py:148`, then
  append one extra row at 01:00 inside the window before `approval.apply`:
  the output's fourth row is `NaN NaN NaN approved` and `len(best)` is 4.
  (Script run during this review; 10 lines.)
- **Fix:** rows in the window that are not in the snapshot should keep their
  fresh labels with `approval: working` (and be listed in the alarm text), or
  be dropped from `best.csv`. Add `quality.notna()` to the `best` filter as a
  belt-and-braces guard, and a test for "extra row", "missing row" and
  "quality changed".

### F5. `as_utc()` treats a naive ISO string as *local* time, so `python -m powometer approve snow_depth 2025-01-26T18:58:34 …` (no `Z`) freezes a window shifted by the machine's UTC offset, silently. Confirmed.

- **Where:** `powometer/config.py:75`
  (`fromisoformat(...).astimezone(utc)` on a naive value),
  `powometer/__main__.py:12-36` (no validation of the CLI times).
- **Reproduce:** `python -c "from powometer.config import as_utc; print(as_utc('2025-01-26T18:58:34'))"`
  prints `2025-01-27 02:58:34+00:00` on this UTC−7 machine. YAML datetimes
  are unaffected (PyYAML yields naive datetimes, which the code treats as
  UTC), but a *quoted* YAML string would hit the same path, and CI (UTC)
  would disagree with a laptop in BC.
- **Fix:** in `as_utc`, treat a naive parsed string as UTC
  (`.replace(tzinfo=utc)`), or better, reject strings without `Z`/offset
  with a readable error; validate the CLI arguments through the same
  function before building.

### F6. `status.json`'s `clock_offset_min` pairs the latest *decodable* payload with the *last message's* transmit time. One undecodable final message turns a −1 min offset into −361 min and would open a false clock alarm in phase 2. Confirmed.

- **Where:** `powometer/build.py:56-58`.
- **Reproduce:** call `build._status` with two messages, a good one and a
  `GARBLED` payload six hours later: `clock_offset_min` becomes −361 while
  `decode_errors` is 1. (Run during this review.)
- **Fix:** keep the message together with its payload
  (`next((m, p) for m in reversed(payloads) if (p := parse_payload(m.payload_text)))`)
  and compute the offset from that message's transmit time.

### F7. Malformed or differently packaged SD input fails silently: a `LOG.CSV` with one extra column loads zero rows, a zipped download (which `OPERATIONS.md` and `raw/README.md` tell the maintainer to upload) loads zero rows, and untimed rows are never reported. Confirmed.

- **Where:** `powometer/sd.py:75` (`len(parts) == len(FIELDS)` drops every
  other row), `sd.py:164` (only `LOG.CSV` and `LOG 2.CSV` are read; no zip
  support, no `LOG 3.CSV`, and on Linux no `log.csv`), `OPERATIONS.md` "Files
  over 25 MB: zip them first", design §6.1 "Zip files are accepted".
- **Reproduce:** a temporary `raw/sd/2027-01-01/LOG.CSV` with five valid
  `Data:` rows plus an `,EXTRA` field → `len(sd.load(raw))` is 0; the same
  rows inside `LOG.zip` → 0. No message either time. (Run during this review.)
- **Impact:** a firmware that adds a column, or a maintainer who follows the
  runbook, makes a whole season vanish from the outputs with a green build.
- **Fix:** count rejected `Data:` lines per file and fail (or alarm in
  `status.json`) when a LOG file yields zero rows or more than, say, 1 %
  rejects; read `*.zip` members; glob `LOG*.CSV` case-insensitively; report
  `untimed_rows` and `rows_per_download` in `status.json`. Then fix the docs
  or the code so they agree about zips.

### F8. The carried-sync extension copies the predecessor log's *last* sync; the station's syncs alternate by about 17 minutes, so a different last sync would shift all 8,579 summer-2026 rows by 17 min with no alarm. Plausible (the current value checks out).

- **Where:** `powometer/sd.py:176-194` (`state[k] = (syncs[-1][1], syncs[-1][2])`).
- **Evidence:** the last five syncs in `raw/sd/2026-06-19/LOG.CSV` give
  offsets 2878.8, 2878.8, **2862.4**, 2879.2, 2879.2 min (the Jan-23 patch's
  minute-wrap bug, described in `scripts/clean_sd_data.py:21-26`). The code
  took 2879.2. I checked it independently against the radio: MOMSN 3128
  (2026-09-26 20:39:40Z, old firmware, five readings logged in minutes during
  the move) has header `241232` → device 2026-09-24 20:32 UTC + 2,879 min =
  20:31Z, eight minutes before its transmission. So +2,879 is right to within
  a few minutes, and the RTC drifted little over 99 days. The pipeline itself
  does not make this check; `test_summer_2026_clock_offset_is_plausible`
  only requires 2840–2900.
- **Fix:** carry the median of the last few syncs (or the last sync that
  agrees with its predecessor within 2 min), and write the carried offset
  and its source into `status.json`. Where radio headers exist, compare.

### F9. Design promises that QC does not implement: a distance step near a visit without a re-mount record, and SD/radio agreement. An unrecorded re-mount therefore produces a wrong snow depth labelled `good` for the whole following period. Plausible.

- **Where:** design §6.4 ("QC also flags distance steps near visit dates that
  have no re-mount record"), §8.1 ("SD/radio agreement"); `powometer/qc.py`
  has neither. `config/mounts.yaml` M2b already records exactly this doubt
  ("something may have changed here", uncertainty 0.6 m).
- **Fix:** implement the step check (median distance 6 h before vs after each
  visit and each ≥ 0.3 m jump anywhere, compared with `mounts.yaml`), report
  hits in `status.json`, and label the period after an unexplained step
  `estimate`. Until then, say in the design that these checks are parked.

### F10. Tests: the sheet comparison never compares times, and 42 of the 63 readings differ from the sheet by 43–130 s; `merge()` has no unit tests; several claims are only asserted. Confirmed (time difference).

- **Where:** `tests/test_pipeline.py:197-209` compares *sorted multisets* of
  values; `powometer/timing.py:25-28` uses 4 × 998 s where the sheet that
  produced the fixture used 1.1209 h (4,035 s): slot k differs by 43·k
  seconds. This is an intended change (the comment explains it) but it is
  not in `docs/DIFFERENCES_v0.md`, and the test could not have noticed.
- **Reproduce:** compare `measurement_pst + 8 h` from
  `tests/fixtures/sheet_data_since_2026-09-26.csv` with the radio
  `snow_depth` times in `best.csv` since 2026-09-26T20:45Z, both sorted:
  42 pairs differ by more than 1 s (43, 86, 130 s). (Run during this review.)
- **Also untested:** `assemble.merge` (value-code collision, missing values,
  statistic per era, a reading with no SD neighbour) has no synthetic test;
  `approval.apply` is tested for one changed value only (not count, quality
  or NaN); `hourly` is tested on one series (not quality/approval
  propagation, not the exact-hour case); `test_no_overlap_or_disorder`
  asserts a property the code enforces by construction;
  `test_carry_changes_only_previously_unrecoverable_rows` checks methods by
  time string but not values. The JS reference and the v0 fixtures were
  generated by the same author from scripts that the ports mirror line by
  line, so "reproduced exactly" means "same as the fixture", which is the
  right regression guard but not independent evidence of correctness (F3
  shows the shared blind spot).
- **Fix:** make the sheet test compare (time, value) pairs with the 43·k s
  difference stated explicitly; add `merge()` tests from F3; extend the
  approval test per F4; add the F1 run-of-floor-readings test.

### F11. Privacy safeguards: the scans are clean, but three gaps. Plausible.

- **Binary files are skipped** (`tools/privacy_check.py:96-99`). Field visits
  mention photos; a JPEG's EXIF block carries GPS coordinates, and a photo
  taken at home during a bench test would pass every check. Fix: strip EXIF
  or refuse image files in the pre-commit hook; or scan EXIF with a few lines
  of code.
- **CI detects after exposure.** On a public repository the pattern check in
  `.github/workflows/checks.yml` runs after the commit is already public, and
  edits made in the GitHub web interface (the route `OPERATIONS.md`
  recommends) never run the local hook at all. Document that web edits must
  contain only initials and times, and consider requiring pull requests from
  a private fork for anything free-text.
- **Patterns:** an IMEI written with spaces or dashes, or a spreadsheet ID
  without an upper-case letter, is not matched; `FLOAT_RE` needs three
  decimals, so `12.34,-123.45` [example replaced 2026-10-05: the original example fell inside the home check's radius] would pass the home check. Low likelihood, easy
  to tighten.
- Checked and fine: `.gitignore` covers everything the brief lists;
  `.gitattributes` keeps raw bytes; the history scan walks all refs
  (`rev-list --all --objects`), and local `main` equals `origin/main`;
  unreachable local blobs exist (`git fsck --unreachable`) but are never
  pushed. The IMEI check deliberately has no Luhn filter, as documented.

### F12. Output contract details. Confirmed.

- `_fmt` writes `-0` for negative values that round to zero
  (`powometer/build.py:41`): 495 rows in `best.csv`, 122 in
  `best_hourly.csv` (enclosure temperature). Harmless to parsers, odd to
  readers; use `v + 0.0` or `"%.0f" % abs(v)` when `v == 0`.
- `SCHEMA.md` does not state the row order (files are sorted by station,
  site, variable, statistic, time, source, not by time). Consumers will
  assume time order; document it.
- `datapackage.json` says `source` is `sd | radio` and omits `derived`;
  `SCHEMA.md` includes it.
- Design §8.2's correction example uses `to` and `params: {end_offset_cm}`;
  the code and `config/corrections.yaml` use `until` and `{start, end}`.
  `OPERATIONS.md` is right; update the design.
- `latest.json` `snow_depth.value` is `-7.0` at the new site: correct per the
  heather zero, but worth a one-line note in `SCHEMA.md` that negative small
  values are expected early in the season (it is already there, good).

### F13. Maintainability and robustness notes. Plausible.

- `assemble.py:26` hard-codes `STATION = "powometer"` and `sd.load()` /
  `radio.load_all()` read fixed folders: a second station added to
  `stations.yaml` with `publish: true` produces nothing and no message
  (requirement R5). `config.py:21` accepts `type: avalanche_canada` although
  no such decoder exists.
- `__main__.py approve` does not run `validate()` first and does not rebuild
  through `approval.apply`, so a snapshot can be written from an invalid
  config.
- `validate()` does not flag gaps between consecutive mounts (readings in a
  gap silently get no snow depth; today the gaps coincide with disturbed
  windows, which is fine but accidental) and does not check `approvals.yaml`
  `from`/`until`.
- `merge()` and `align()` are dense (`s`, `r`, `d`, `t`, `h`, `xs`, `ys`);
  a non-programmer will not follow them. A paragraph of prose above each,
  with the worked example of one duplicate, would help more than comments.
- `hourly.py:47` takes `level` from the later neighbour only; use the
  "worse" of the two as for quality.
- `spike_checks` windows are index-based, so across an SD gap the five
  "neighbours" can be days apart; make the window time-based or reset at gaps.
- Strict `strptime` formats in `radio.py` are good (loud on change); a
  missing CSV header key is also loud. Good.

## 3. Claims checked

| Claim (`docs/DIFFERENCES_v0.md` unless noted) | Result |
|---|---|
| SD recovery reproduces v0 on all 41,554 rows: time, method, visit, values | **Verified**: `test_v0_reproduced_exactly` asserts the count and compares every field in order; fixture built from `scripts/clean_sd_data.py` with the same merge rules. Regression-tight, not independent (same author, same logic). |
| Decoder and timing match "the deployed Apps Script" on 2,587 messages | **Verified against the repository's `Code.gs`**, which by its own comment (line 52) is *not yet deployed*; the deployed script used the older 4,035 s interval, so the sheet's times differ by 43 s per slot (F10). Reword. |
| Frozen reconstruction matches v0 except rows 607–934 and 1352–1358 | **Verified** (test passes, 1 s tolerance), but both sides share the rounding defect (F3). The independent check (row 723 on the 2025-02-07 download ≤ 19:45Z) **verified**. |
| 63 radio readings since the move match the sheet, depth within 1 cm | **Values verified** (sorted multisets); **times not checked by the test** and differ (F10). |
| #1 Summer 2026: 8,579 rows, constant +2,879 min | **Verified**: 8,579 `carried-sync` rows, all +2,879; independently consistent with MOMSN 3128's header (F8). |
| #2 328 rows, up to 20 h; #3 7 rows ≤ 38 s | Counts consistent with the test's exclusions; magnitude not independently re-derived. |
| #4 74 radio readings fill gaps; duplicates removed by value match | **Partly refuted**: 72 are genuine (63 after the last download, 9 in visit-day gaps of 100–224 min); **2 are duplicates** of published SD rows (F3). |
| #5–#7 references from `mounts.yaml`, whole cm from the median, true UTC | **Verified** in code and output (`best.csv` snow depth is `median` only; `-0` aside, whole numbers; times `Z`). |
| #8 Move window excludes 8 SD rows | **Verified**: 8 rows with recovered times in 18:15–20:45Z. |
| #9 C001–C003 match nothing, reported in `status.json` | **Verified**. |
| Quality table 27,139 / 20,715 / 1,965 | **Verified** exactly. |
| 66 untimed rows (plus 1,579 frozen) | **Verified**: 1,645 untimed after dedup. |
| `build.py` docstring: "Re-running gives identical files (apart from the build time)" | **Verified** (two builds). |
| `qc.py` docstring: "never changes a recorded value; it only labels" | **Refuted in letter**: `range_checks` sets no-echo distances to NaN (`qc.py:53`). Reasonable, but say so. |
| `assemble.py` docstring: SD authoritative, radio only fills, same statistic only | **Verified** for the statistic (pre-v1.3 radio minima never enter `best.csv`); duplicate removal has the F3 hole. |
| `approval.py`: "Approved data never changes silently" | **Verified** for changed values and counts (alarm fires); **F4** shows a path where *unapproved* rows are stamped approved. |
| `OPERATIONS.md`/`raw/README.md`: zip files accepted | **Refuted** (F7). |
| Design §6.4/§8.1: step-near-visit and SD/radio agreement checks | **Not implemented** (F9). |
| `CLAUDE.md`: real IMEI fails Luhn, so no Luhn filter | Consistent with the code; cannot verify the IMEI itself (not read, by design). |
| Design §5.1: raw immutability enforced by CI | **Verified** by reading `tools/raw_immutable.py` and the workflow (base..head diff over `raw/`, renames shown as deletions). Not exercised against GitHub in this review. |

## 4. What I did not check

- Anything in the forbidden paths (`misc/`, `resources/`, `data/Aquarius/`,
  `data/Google Sheet/`, `data/PCDS/`, `.private/`, the original RockBLOCK
  export, evidence files, the deployed Apps Script copies). In particular I
  could not confirm what the *deployed* script computes; the fixture only
  shows that it differed in the reading interval.
- The firmware's clock-sync behaviour beyond reading the LOG lines; the ERA2
  → ERA3 constant (3932 d 03:50:22) is taken on trust from
  `clean_sd_data.py`.
- The physical correctness of the mount references in `config/mounts.yaml`
  (4.51 / 4.60 / 5.09 / 3.12 / 3.82 m) and the 2025-02-07 visit story; I
  checked the mechanics, not the field facts.
- GitHub Actions behaviour (the workflow was read, not run), branch
  protection, and organisation ownership.
- The hourly interpolation on the real series beyond spot checks and the
  unit test.
- Performance (the build takes about two minutes here; fine for hourly runs).
- The Chart/site layer and anything in phase 2 or 3.
