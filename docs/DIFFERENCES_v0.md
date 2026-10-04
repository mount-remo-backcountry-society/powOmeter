# Differences from the original processing (phase 1 exit report)

The phase 1 exit criterion: the new pipeline reproduces the original results
except for documented, intended differences. This lists every difference,
with counts from the build of 2026-10-04. Every item is covered by a test in
`tests/`.

## Reproduced exactly

| What | Test |
|---|---|
| SD timestamp recovery and values (`scripts/clean_sd_data.py`): **all 41,554 rows**: time, method, source visit and every value | `test_sd.py::test_v0_reproduced_exactly` |
| Payload decoding and reading times (deployed Apps Script): **all 2,587 messages** | `test_decode_timing.py` |
| Frozen-period reconstruction, outside the rows listed below | `test_frozen.py` |
| Radio readings since the move: the Google Sheet's **63 readings**, same values, snow depth within 1 cm | `test_pipeline.py::test_matches_sheet_since_the_move` |

## Intended differences

| # | Difference | Rows | Why | Evidence |
|---|---|---|---|---|
| 1 | **Summer 2026 recovered** (2026-06-19 → 09-26) | 8,579 SD rows | The original left them untimed: no clock syncs while the modem was disconnected. They now continue the June clock at a constant +2,879 min | Matches the June syncs (+47 h 59 min) and the Messages-tab offsets |
| 2 | **Frozen period re-timed** around the 2025-02-07 visit | 328 rows (607–934), up to 20 h | The full radio history has 9 visit messages the sheet lacked; the station logged about 120 readings in an hour during the visit | The SD download made **at** that visit already contains rows up to index 723 |
| 3 | Frozen period, rows 1352–1358 | 7 rows, ≤ 38 s | One extra radio anchor (2025-02-15 04:34:16) | – |
| 4 | **Radio readings fill SD gaps** | 74 readings | The SD card was out of the station during downloads, plus everything since the last download | Duplicates removed by matching the encoded values within 3 h |
| 5 | **Snow-depth references** from mounting periods (`config/mounts.yaml`) instead of the Field-tab offsets | All snow depth | Bare-ground medians where available (4.51 / 4.60 / 3.12 m); tape 3.82 m; Oct 2025–Jun 2026 estimates | Re-mount analysis, `docs/PLAN_PHASE_0_1.md` §1f |
| 6 | **Snow depth in whole cm, from the median** distance | – | WMO practice and the CIMO Guide; the radio sent the minimum before v1.3 | Review F5, F14 |
| 7 | **Times in true UTC** | All | The sheet used America/Los_Angeles (before 2026-09-22) or fixed UTC−8 | – |
| 8 | **Move window excluded** (2026-09-26 18:15–20:45 UTC) | 8 SD rows | No site during the move | `config/sites.yaml` |
| 9 | Corrections C001–C003 currently match no rows | – | The affected readings are already excluded (outside a site, or duplicates of SD rows). Kept as a record; reported in `status.json` | – |

## Snow depth quality in `best.csv`

| Quality | Rows |
|---|---|
| good | 27,139 |
| estimate (reference uncertain, Oct 2025 – Jun 2026) | 20,715 |
| suspect (spikes, site visits, negative depth) | 1,965 |

## Still untimed

66 SD rows have no recoverable time. Their station-clock times are
nonsense (years 2000, 2009, 2050), mostly logged around the 2025-12-30 and
2026-01-23 visits while the clock was being reset or patched. They stay in
`raw/` and are not published. (Another 1,579 rows lack clock syncs but are
the frozen-clock block, reconstructed from radio anchors as above.)
