# Plan: phases 0 and 1

Implements `docs/DATA_PIPELINE_DESIGN.md` v4, §13, phases 0 (Foundations) and
1 (Record). Draft, 2026-10-03.

**Nothing in these phases touches the live chain** (Gmail, Apps Script,
sheet, Looker, Avalanche Canada). Nothing is published.

---

## Decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Repo location | **Decided:** this folder |
| D2 | Private until phase 2 | **Decided:** yes. Going public can't be undone, so develop privately, scan the full history, then transfer to the MRBS organization and make it public at the start of phase 2 |
| D3 | Firmware eras | **Decided (2026-10-03):** no deployment record exists; **every firmware before v1.3 sent the minimum.** So there are two eras: before 2026-09-26 `min`, from 2026-09-26 `median` |
| D4 | Past sensor heights | **Answered:** Julian **raised the sensor several times during the season as snow built up**, and at each visit **adjusted the sheet's offset so the computed snow depth matched the depth he probed under the sensor**. So the Field-tab values are back-calculated offsets. Phase 1 records the probed depths and lets the pipeline compute each reference. The Field-tab values (2025-01-26 4.10 m, 2025-09-30 4.45 m, 2026-03-07 5.09 m, 2026-09-26 3.82 m) are therefore references for different mounting periods, not one sensor height, and **some re-mounts may be unrecorded.** Plan: task 1f detects re-mounts in the SD data and Julian confirms them |
| D5 | Python dependencies | Python 3.12 with `pandas`, `pyyaml` and `pytest`, at fixed versions (explained to Julian 2026-10-03; awaiting his OK) |

## Phase 0: Foundations (about 1–2 sessions)

**Progress (2026-10-03):**
- **Done:** 0.2–0.9. Local repo initialised; first commit `bb74719`. The
  privacy hook is installed, and the full-history scan is clean. Tested:
  - fake secrets are blocked: IMEI, email address, sheet ID, published-sheet
    ID, coordinates near a dummy home location
  - harmless strings pass
  - the check catches the **real** IMEI and sheet ID in the local deployed
    script. A Luhn filter was removed after it let the real IMEI through
  - the raw-immutability check, in a throwaway clone (add passes; modify and
    delete fail)
- **Open:**
  - 0.1: MRBS request (Julian)
  - 0.10: backup made 2026-10-03 to a sibling folder **on the same laptop**.
    It still needs copying to separate media (USB drive or personal cloud) to
    protect against losing the laptop (Julian)
  - ~~0.11~~ **done 2026-10-03:** private repo `j-krick/powOmeter` created and
    pushed. The first CI run is green: privacy scan of the tracked files and
    the full history clean; raw-immutability check passed
  - ~~home coordinates~~ done 2026-10-03; the home check passes on all
    files and the full history

| # | Task | Who | Done when |
|---|---|---|---|
| 0.1 | Ask MRBS for the GitHub organization and a second owner (needed before phase 2, not now) | Julian | Request sent |
| 0.2 | `.gitignore` written **before** `git init`'s first commit: `misc/`, `resources/`, `data/Aquarius/`, `data/Google Sheet/`, `data/Cleaned/`, the original RockBLOCK export, `scripts/Google Apps script.txt`, `claude resume session *.txt`, local secrets file, build output | Claude | `git status` on a fresh init shows none of these |
| 0.3 | **Local pre-commit hook:** blocks the home coordinates (read from an untracked local file Julian fills in) plus the patterns from 0.4. Installed by hand; documented in `OPERATIONS.md` | Claude writes it; Julian fills in the coordinates | A test commit containing a planted home coordinate is rejected |
| 0.4 | **CI pattern check** (Action, on every push): 15-digit IMEI, Google spreadsheet-ID format, email addresses. Plain Python with regexes, no third-party scanner | Claude | A planted IMEI fails CI |
| 0.5 | **Raw-immutability check** (CI): any modification or deletion of an existing file under `raw/` fails | Claude | Editing a raw file fails CI; adding one passes |
| 0.6 | **Sanitised RockBLOCK export:** a copy with the `Approx Lat/Lng` column removed, for `raw/radio/`. The original stays local; both checksums go in its README | Claude | Column absent; row count 2,588 |
| 0.7 | **Scrubbed Apps Script copy:** IMEI and spreadsheet ID replaced by `PropertiesService` lookups, including in `testParser()` samples. The deployed script is unchanged for now (it's switched outside the season) | Claude | 0.4 passes on it |
| 0.8 | `LICENSE` (MIT) and `LICENSE-DATA` (CC BY 4.0, crediting MRBS), marked *draft pending MRBS* | Claude | Files present |
| 0.9 | Skeleton `README.md` (including "must stay public once public"), `CLAUDE.md` (rules), `OPERATIONS.md` (headings only) | Claude | Files present |
| 0.10 | Back up the local-only folders somewhere other than this laptop | Julian | Done |
| 0.11 | Create the **private** GitHub repo; first push | Julian (or Claude with approval) | CI green; history scan (0.4 rules over all commits) clean |

**Phase 0 exit:** a planted secret is blocked locally and in CI; the history
scan is clean; the backup is done; the MRBS request is sent.

## Phase 1: Record (about 4–6 sessions)

### 1a. Layout and raw import

```
raw/radio/rockblock-export-2026-09-29.csv     (sanitised, from 0.6)
raw/radio/2026/09.jsonl, 10.jsonl …           (Messages-tab rows with MOMSN, from the latest sheet export, as a fixture)
raw/sd/<visit-id>/…                           (moved from data/Site Visits/, names unchanged as visit IDs)
config/stations.yaml, sites.yaml, firmware_versions.yaml, field_visits.yaml,
       corrections.yaml, approvals.yaml, monitoring.yaml
powometer/ (package)  ·  tests/ (+ tests/fixtures/)  ·  approved/ (empty)
```

- **Moving `data/Site Visits/` to `raw/sd/`** happens after 1b has produced
  its reference output, so the old script still finds its input.
- **Seed `corrections.yaml`** with C001 (backyard tests up to 2025-01-25
  18:11 UTC, plus the 2025-01-26 19:01:45 bundle) and C002 (the 2026-09-26
  visit bundle, transmitted 20:39:40 UTC).
- **Seed `field_visits.yaml`** from D4, and `sites.yaml` from
  `powWX/config/locations.yaml`.

### 1b. Reference outputs (before refactoring anything)

1. **Re-run the existing `scripts/clean_sd_data.py` on all 17 folders.**
   `data/Cleaned/` is stale: built from 15 folders on 2026-09-20. Its output
   becomes `tests/reference/` (local-only if large; a hashed summary is
   committed).
2. **Extract a post-2026-09-22 slice of the sheet's Data rows** from the
   latest export, as the radio reference.

### 1c. Package modules, in build order

| Module | Content | Source |
|---|---|---|
| `decoders/powometer.py` | Payload decode; statistic from `firmware_versions.yaml` | `parsePayload` (`v2.js:267`) |
| `timing.py` | Header time, clock offset, ±30 min fallback, reading interval | `v2.js:296–344` |
| `radio.py` | Load console export + jsonl; key `(transmit_utc, payload)`; both date formats | new |
| `sd.py` | Parse LOG.CSV, timestamp repair, frozen-period reconstruction, deduplicate overlapping downloads | `clean_sd_data.py`, `reconstruct_frozen_period.py` |
| `merge.py` | SD authoritative, radio fills, same statistic only | new |
| `snowdepth.py` | `zero_depth_distance − distance` per site; negative → `suspect` | new |
| `qc.py` | Range, step, spike, no-echo, SD/radio agreement | partly `qc_row` |
| `corrections.py` | Operation log: delete (→ `poor`), spike_filter, threshold, gap_fill, offset, drift; validation | new |
| `labels.py`, `approval.py` | Level/approval/quality/qualifiers; snapshot write and compare | new |
| `hourly.py` | On-the-hour interpolation (`interpolated`), maximum gap | new |
| `build.py` | `python -m powometer build` → `out/` (local, git-ignored): the v1 files from the design §6.6 | new |
| `validate.py` | `python -m powometer validate` for config files (used in pull-request checks in phase 2) | new |

### 1d. Tests (`tests/`, run in CI on every push)

| Test | Fixture |
|---|---|
| Decode matches the JS implementation on every historic payload | Payloads from the sanitised export |
| Station-clock timing and fallback (±29/±31 min, unreadable header, month boundary) | As in the 2026-09-29 Apps Script tests |
| Console-export format ≡ Messages-tab format for the same message | The 22 messages since 2026-09-22 |
| v1.3 statistic change: no minimum/median mixing; pre-v1.3 radio minimum flagged | Messages either side of 2026-09-26 |
| Overlapping SD downloads deduplicated | `2026-09-26_01` vs `_02` |
| SD rebuild matches the reference values | 1b output |
| Radio rebuild matches post-2026-09-22 sheet rows, except documented differences | 1b slice |
| Snow depth derivation and site split at 2026-09-26T21:31Z | Config + sample |
| Each correction operation, and `delete` → `poor` | Small synthetic series |
| Approved snapshot: an unchanged rebuild passes; a changed rebuild raises the alarm | Synthetic |
| On-the-hour series: correct instants, no value across long gaps | Synthetic |
| PCDS daylight-saving conversion | Parked with the overlay (needs the deferred PCDS downloads) |
| Two rebuilds byte-identical | Full build |

### 1f. Reconstruct the mounting history (needs Julian)

1. **Detect re-mounts in the SD record:** steps in distance at, or near,
   site-visit dates (17 download folders, 14 visit dates), plus the readings
   disturbed during each visit.
2. **List each candidate** with date, distance before and after, and the
   Field-tab value if one exists.
3. **Julian confirms or rejects each one** and gives the reference where he
   knows it.
4. **Unknown references stay unknown:** that period's snow depth gets
   quality `estimate` or stays `working`, rather than inventing a value.
   A season-end bare-ground reading (distance at melt-out) gives a check on
   the last re-mount of each season.
5. **The result goes into `field_visits.yaml`,** with the method and
   uncertainty for each reference.

This likely explains the old site's −0.54 m floor: a reference taken as
*height above snow + probed depth* in mid-winter carries the probe's error
for the whole period that follows.

### 1e. Documentation

- `SCHEMA.md` and `CHANGELOG.md` (contract v1, draft).
- `OPERATIONS.md`: the site-visit and correction runbooks.
- `CLAUDE.md` filled in.
- A **differences report**: every intended difference between the rebuild
  and the sheet (timezone fix, corrections, the median, the statistic
  split), with counts.

**Phase 1 exit (from the design):** reference tests pass; documented
differences only; two rebuilds identical. Then: an independent **code
review** before phase 2.

---

## Independent checks

| When | What | Why |
|---|---|---|
| **Before the repo becomes public (start of phase 2)** | A fresh-session review of the full git history and `.gitignore` against the design §10 | Going public is the one irreversible step |
| **End of phase 1, before publishing** | Independent code review: correctness of the decoder, timing, merge, corrections and snapshots against the tests and the design | This is where errors would reach users |
| This plan | Optional | Phases 0–1 are private and reversible, and the exit criteria are hard tests against existing outputs |
