# Differences from the original processing (phase 1 exit report)

The phase 1 exit criterion: the new pipeline reproduces the original results
except for documented, intended differences. This lists every difference,
with counts from the build of 2026-10-04, after the code-review fixes.
Every item is covered by a test in `tests/`.

## Reproduced exactly

| What | Test |
|---|---|
| SD timestamp recovery and values (`scripts/clean_sd_data.py`): **all 41,554 rows**: time, method, source visit and every value | `test_sd.py::test_v0_reproduced_exactly` |
| Payload decoding and reading times: **all 2,587 messages**, against `scripts/apps_script/Code.gs` in this repo (the reference; not the deployed copy, which still uses the old reading interval, see #10) | `test_decode_timing.py` |
| Frozen-period reconstruction, outside the rows listed below | `test_frozen.py` |
| Radio readings since the move: the Google Sheet's **63 readings**, same values, snow depth within 1 cm, times to the second apart from #10 | `test_pipeline.py::test_matches_sheet_since_the_move`, `::test_times_match_sheet_since_the_move` |

## Intended differences

| # | Difference | Rows | Why | Evidence |
|---|---|---|---|---|
| 1 | **Summer 2026 recovered** (2026-06-19 → 09-26) | 8,579 SD rows | The original left them untimed: no clock syncs while the modem was disconnected. They now continue the June clock at a constant +2,879 min | Matches the June syncs (+47 h 59 min) and the Messages-tab offsets |
| 2 | **Frozen period re-timed** around the 2025-02-07 visit | 328 rows (607–934), up to 20 h | The full radio history has 9 visit messages the sheet lacked; the station logged about 120 readings in an hour during the visit | The SD download made **at** that visit already contains rows up to index 723 |
| 3 | Frozen period, outside the visit | 126 of 1,256 rows by more than 1 s; 21 by more than 2 min (max 49 min, rows 1250–1599) | **250 radio anchors instead of 175**: the original compared values with Python rounding (half to even) where the firmware rounds half away from zero, so it missed about 1 in 10 matches (code review F3) | Fewer irregular steps between consecutive rows: 12 instead of 20 |
| 4 | **Radio readings fill SD gaps** | 72 readings (63 since the move) | The SD card was out of the station during downloads, plus everything since the last download | Duplicates removed by matching the values the firmware would have sent (its rounding, both encodings on exact ties) within 3 h |
| 5 | **Snow-depth references** from mounting periods (`config/mounts.yaml`) instead of the Field-tab offsets | All snow depth | Bare-ground medians where available (4.51 / 4.60 / 3.12 m); tape 3.82 m; Oct 2025–Jun 2026 estimates | Re-mount analysis, `docs/PLAN_PHASE_0_1.md` §1f |
| 6 | **Snow depth in whole cm, from the median** distance | – | WMO practice and the CIMO Guide; the radio sent the minimum before v1.3 | Review F5, F14 |
| 7 | **Times in true UTC** | All | The sheet used America/Los_Angeles (before 2026-09-22) or fixed UTC−8 | – |
| 8 | **Move window excluded** (2026-09-26 18:15–20:45 UTC) | 8 SD rows | No site during the move | `config/sites.yaml` |
| 9 | Corrections C001–C003 currently match no rows | – | The affected readings are already excluded (outside a site, or duplicates of SD rows). Kept as a record; reported in `status.json` | – |
| 10 | Reading times within a message | 43 s per reading slot (0, 43, 86, 130, 173 s) | The deployed Apps Script spaces readings 1.12094444 h apart; the measured interval is 4 × 998 s = 1.10889 h | Median of 8,577 gaps in the 2026-09-26 SD download |
| 11 | **0.50 m distances are missing** (qualifier `too_close`) | 310 median readings (1,245 incl. min/max) | The sensor's minimum-range reading: in storms, echoes off falling snow. The original turned them into 3–4 m of snow | JK, 2026-10-04; most are May–July 2025, see below |

## Snow depth quality in `best.csv`

| Quality | Rows |
|---|---|
| good | 27,100 |
| estimate (reference uncertain, Oct 2025 – Jun 2026) | 20,686 |
| suspect (spikes within ±1 h, site visits, negative depth) | 1,723 |

No visit shows a distance step of 0.15 m or more without a recorded
re-mount (the closest: 2026-03-08, −0.13 m).

## 0.50 m readings without snow

Of the 1,245 `too_close` readings, 997 are from April–July 2025 (430 in
May alone, many days in a row), when there was no snow to echo from. Their
cause is unknown: something in or on the sensor cone (insects, water)
would fit. They are `missing` either way. Open question for the next site
visit.

## Still untimed

66 SD rows have no recoverable time. Their station-clock times are
nonsense (years 2000, 2009, 2050), mostly logged around the 2025-12-30 and
2026-01-23 visits while the clock was being reset or patched. They stay in
`raw/` and are not published. (Another 1,579 rows lack clock syncs but are
the frozen-clock block, reconstructed from radio anchors as above.)
