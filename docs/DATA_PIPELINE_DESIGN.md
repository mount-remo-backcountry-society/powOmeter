# POW-O-METER data platform v2: design

**Status:** draft v4, 2026-10-03. Incorporates the independent review
(`docs/review/REVIEW_2026-10.md`, verdict "sound with changes"; findings
F1–F16 are referenced where they apply). Nothing is built yet. Each phase
(§13) needs Julian's approval.

**How to read this.** §1–§3: purpose and why change. §4–§12: the design.
§13: what is built now and what is parked. §14–§15: risks, decisions, open
questions. Supporting documents: `docs/research/STANDARDS_AND_PUBLISHING.md`,
`docs/research/evidence/` (licensing), `docs/review/REVIEW_2026-10.md`.

---

## 1. Background

**POW-O-METER** is a snow and weather station at the top of Shames Mountain,
BC (1,150 m). It is owned by the **Mount Remo Backcountry Society (MRBS)**,
which paid for the components, and built and maintained by volunteer Julian
Krick.

- **Hardware:** Feather M0, MB7374 ultrasonic sensor (it measures *distance*
  to the surface), SHT31 temperature and humidity sensor, BMP390 pressure
  sensor, RockBLOCK 9603 Iridium modem, solar and LiPo power.
- **Data:** it wakes about every 17 min and logs a burst of ultrasonic
  readings (min, max, median), temperature, humidity, pressure and battery to
  SD. One reading in four is queued for the radio and sent at 7 scheduled
  hours a day (`POW_O_METER_v1_3.ino:79`). Before firmware v1.3 the radio
  carried the burst **minimum**; since v1.3 (deployed 2026-09-26) it carries
  the **median** (`IMPROVEMENTS.md` Part 1 item 8).
- **Location:** relocated about 70 m on 2026-09-26, same elevation, away
  from a wind-affected spot.
- **Current review practice:** at the end of each season Julian loads the SD
  data into Aquarius Time-Series (his employer's system) and corrects snow
  depth there: offset from field measurements, outlier filtering, drift.

**powWX** is Julian's separate, personal, experimental forecasting project
(`github.com/j-krick/powWX`, public). It currently reads POW-O-METER data from
the Google Sheet.

```
Current: RockBLOCK ─email─▶ Gmail ─5-min trigger─▶ Apps Script ─▶ Google Sheet ─┬▶ Looker Studio
                                                                                ├▶ Avalanche Canada
                                                                                └▶ powWX
         SD card ─each visit─▶ local folders ─scripts / Aquarius─▶ cleaned CSVs (unpublished)
```

## 2. Goal and requirements

**Goal:** better weather data for ski-hill and backcountry users, to help
them plan trips and be safer.

| # | Requirement (Julian, 2026-09-29) | Consequences |
|---|---|---|
| R1 | Free or very cheap | A public repo with Pages and Actions (Actions is only free on public repos, F3). Satellite credits remain the only running cost |
| R2 | Accessible and open source | Public repo, open licences, downloadable CSV with standard names |
| R3 | A non-programmer can take over, with AI help | Few platforms; CSV/YAML edited in the GitHub web interface; alerts as GitHub Issues; `OPERATIONS.md`; `CLAUDE.md`; MRBS-owned accounts; no dependency on tools only Julian has |
| R4 | powWX and POW-O-METER independent, yet integrated | Published files only; one-way dependency |
| R5 | More stations later | Station ID everywhere; stations in config; one decoder per source type |
| R6 | Safer trip planning | Data age computed live in the browser; labels visible; stale data marked; local time shown; terms of use; link to the Avalanche Canada forecast |

Presentation: two separate, linked sites (station data; forecast). Julian's
direction (2026-10-02): **start building, settle details along the way.**
Only decisions that are expensive to change later are fixed now (§13).

## 3. Why change

The Google Sheet does four jobs at once (database, processing, correction log
and public API) and Gmail acts as the message queue. Every failure found in
September 2026 traces back to that:

| Failure | Evidence |
|---|---|
| Ingest stuck from 2026-05-14 (re-read one message 37,044 times) | `IMPROVEMENTS.md` Part 3 |
| 8,487 duplicate rows | ibid.; `dedupeDataSheet()` run of 2026-09-22 |
| "(PST)" columns really America/Los_Angeles; archive 8 h off, +1 h after 2026-03-08 | ibid.; `data/Cleaned/README.md` |
| Readers can see an empty hourly tab | `scripts/Google Apps script v2.js:494` |
| Corrections leave no trace | rows deleted by hand, 2026-09-29 |
| Changes in meaning not announced | timezone 2026-09-22; site move 2026-09-26 |
| SD and radio never combined | `scripts/clean_sd_data.py`, `data/Cleaned/` |
| No alerting | this project |

The chain **works now** (fixed 2026-09-22). This is about robustness,
handover and openness. **The live chain is not touched during the winter
season**; consumers switch in spring or summer.

## 4. Ownership and accounts

| Asset | Owner |
|---|---|
| Station repo, data and site | **MRBS GitHub organization `mount-remo-backcountry-society`** (created 2026-10-04; repo transferred; owner so far: Julian), at least two owners. **It must exist before anything is published (phase 2)**: GitHub redirects repo links after a transfer but **not Pages addresses**, so publishing under a personal account would break every consumer at handover (F1). Phases 0–1 may run under Julian's account |
| The station's shared Gmail account (intake mailbox), Google Sheet, Apps Script | MRBS; a second person with access. Check the 5-min trigger is owned by this account. **Sign in at least once a year**: Google deletes accounts after 2 years without a human sign-in (F12) |
| RockBLOCK account and billing | MRBS; a second person with access |
| powWX | Julian personally |

Rule: MRBS assets never depend on Julian's personal projects or tools
(powWX, his employer's Aquarius).

## 5. Principles

1. **Raw is immutable**, enforced by a CI check that rejects changes to
   existing files under `raw/` (F16).
2. **Corrections are data:** an operation log applied to raw, with git
   history as the audit trail.
3. **Approved means frozen.** Approving a period commits a snapshot; rebuilds
   are compared against it, and a difference raises a warning, never a silent
   change (F4).
4. **Snow depth is derived, not corrected:** `snow_depth =
   zero_depth_distance − distance`, at every level (F6). The reference
   changes every time the sensor is re-mounted. Julian raises it during the
   season as snow builds up, so each re-mount is a field-visit record with its
   own reference value.
5. **Every value carries labels:** level, approval, quality, qualifiers.
6. **Point readings are the data.** Hourly values are interpolated
   on-the-hour points, labelled as such, never means (F7).
7. **Publish always; alarm on change.** Publishing never stops because of a
   data problem. Problems go in `status.json`, and a separate check opens or
   closes a GitHub Issue when the alarm state changes (F2).
8. **One owner per fact; one-way dependency; plain files over servers.**
9. **Firmware and payload unchanged** (field constraints,
   `scripts/FIELD_CARD.md`).

## 6. Architecture

```
 INTAKE                                   STATION REPO (public)                         PUBLISHED (Pages)
 RockBLOCK → Gmail → Apps Script ─▶ Messages tab ─published CSV─┐
 RockBLOCK console export (backstop) ───────────────────────────┼▶ raw/radio/
 SD card, each visit (drag & drop, zip OK) ─────────────────────┼▶ raw/sd/<visit>/
 (Avalanche Canada / PCDS: only after MoTI permission, §10)     │
                                       config/ · powometer/ (Python) · tests/
                                       Action "publish" (hourly, off the hour) ──────▶ v1/*.csv, *.json, site
                                       Action "check" (after publish) ──────────────▶ GitHub Issue on alarm change
```

### 6.1 Intake

- **Radio:** the existing Gmail + Apps Script intake stays. The publish
  Action reads the sheet's `Messages` tab through its **published CSV link**
  (no secret in the repo, F8) and appends new messages to `raw/radio/`. It
  parses the text, never date cells.
- **Backstop:** the RockBLOCK console export (email delivery has no documented
  retry). The loader accepts its format (`29/Sep/2026 18:16:25`, hex
  payload), and `OPERATIONS.md` explains how to re-export it and drop it in.
- **SD:** after each visit, upload to `raw/sd/<visit-id>/` by drag and drop.
  Zip files are accepted, because a long winter's `LOG.CSV` can exceed
  GitHub's 25 MiB browser limit (F12). Overlapping downloads, such as the two
  copies of 2026-09-26, are deduplicated on load.

### 6.2 Raw archive (append-only, CI-enforced)

| Path | Content |
|---|---|
| `raw/radio/messages_tab/<fetch>.jsonl` | **One new file per fetch**, holding only messages not seen before: `transmit_utc, momsn, session_status, cep_km, payload_text, payload_hex, intake, received_utc`. Files are never appended to: messages can arrive months late (MOMSN 2430 from March 2026 was ingested on 2026-09-22), and the raw-immutability check rejects any change to an existing file. Key `(transmit_utc, payload)`; MOMSN where known. (Corrected 2026-10-04 from a monthly-file layout, which would have required appends) |
| `raw/radio/rockblock-export-2026-09-29.csv` | Console history, 2,588 messages since 2024-04-18; **the `Approx Lat/Lng` column dropped for all rows** (F10). Original kept locally, with its checksum in `data/RockBLOCK/README.md` |
| `raw/sd/<visit-id>/` | SD files as downloaded (17 download folders from 14 visit dates so far) |

Known facts (`data/RockBLOCK/README.md`):
- **Backyard tests:** everything up to 2025-01-25 18:11 UTC was sent from
  home. The first message from the mountain (2025-01-26 19:01:45 UTC) still
  carries readings queued at home. Deployment started 2025-01-26 18:58:34 UTC
  (`clean_sd_data.py:50`).
- **Summer 2026:** nothing reached RockBLOCK from 2026-05-28 to 2026-09-26
  (subscription suspended). From 2026-05-28 to 06-19 the modem still reported
  151 successful sends; from 06-19 to 09-26 it was disconnected. Delivery can
  only be confirmed on the receiving side.

### 6.3 Configuration (YAML, edited in the GitHub web interface; validated in pull requests)

| File | Content |
|---|---|
| `stations.yaml` | Stations: id, name, operator, source type (selects the decoder, F16), variables, licence |
| `sites.yaml` | Sites per station: coordinates, elevation, from/until. POW-O-METER: `shames_top_a` until 2026-09-26T21:31Z, then `shames_top_b` |
| `firmware_versions.yaml` | Firmware eras: until 2026-09-26 (pre-v1.3; individual versions unrecorded), radio statistic `min`; from 2026-09-26 (v1.3), `median`. No-echo values 498/499/500 cm (F5) |
| `field_visits.yaml` | Per visit, **what was observed**: date and time, initials; **snow depth probed under the sensor**; re-mount yes/no; a direct sensor-to-ground measurement if one was taken; photos; notes. **The pipeline computes the zero-depth reference itself:** distance reading at the visit + probed snow depth, from that visit until the next one. That's how Julian calibrated in practice (he adjusted the sheet's offset so the computed depth matched his probe), and how Aquarius uses field visits. The old Field-tab values were these back-calculated offsets, not tape measurements |
| `corrections.yaml` | Operation log (§8.2) |
| `approvals.yaml` | Approved periods, each pointing to its snapshot file (§8.3) |
| `monitoring.yaml` | Season dates, thresholds |

### 6.4 Processing (Python package `powometer/`)

| Step | Notes / reuse |
|---|---|
| Decode (one module per source type) | POW-O-METER payload: `parsePayload` (`Google Apps script v2.js:267`), ported with tests. The statistic is stamped from `firmware_versions.yaml` |
| Time readings | `headerTimeMs`, `clockOffsetMinutes`, `measurementTimes` (`:296`, `:315`, `:327`) |
| SD records, historic timestamp repair | `clean_sd_data.py`, `reconstruct_frozen_period.py`, refactored to return records. Repaired timestamps get `timestamp_repaired`; the reconstructed 2025-01-31 → 02-17 period gets `timestamp_estimated` |
| Merge | SD is authoritative; radio fills the rest. **Same statistic only**: snow depth uses the median; pre-v1.3 radio minima are kept but flagged, not mixed in |
| Snow depth | `zero_depth_distance(site, t) − distance` at every level, the reference switching at each re-mount; negative values flagged `suspect`, not clipped. QC also flags distance steps near visit dates that have no re-mount record, a likely missing entry |
| QC → corrections → labels → snapshots | §8 |
| On-the-hour series | Interpolated from neighbouring point readings, `statistic: point`, qualifier `interpolated`; no value where the gap exceeds the limit |

Full rebuild from raw on every run (the whole SD record is about 41,500 rows,
so it's small).

### 6.5 Labels

| Label | Values |
|---|---|
| **level** | `raw` (as recorded) · `qc` (automatic checks) · `corrected` (reviewed corrections) |
| **approval** | `working` · `in_review` · `approved`, shown on the site as "Provisional" or "Approved" (F14) |
| **quality** | WaterML 2.0 codes, registered by WMO as "WaterML2": `good`, `suspect`, `estimate`, `poor`, `unchecked`, `missing` |
| **qualifiers** | e.g. `site_visit`, `sensor_fault`, `interpolated`, `gap_filled`, `timestamp_repaired`, `timestamp_estimated`, `decode_error` |

### 6.6 Published outputs: contract v1 (kept small, F13)

| File | Content |
|---|---|
| `v1/observations.csv` | All point readings, all levels, long format |
| `v1/best.csv` | Best available point readings: approved snapshot where it exists, otherwise `qc`. Values with quality `poor` are excluded |
| `v1/best_hourly.csv` | On-the-hour interpolated series from `best`, for comparison with forecasts and other stations |
| `v1/latest.json` | Current conditions for the site |
| `v1/status.json` | Per station: last observation time, battery, clock offset, alarms, counts of decode errors, `schema_version`, build time |
| `v1/sites.json` | Stations and sites, with coordinates, periods, licence per source and time convention per source |
| `v1/datapackage.json` | Column definitions, units, CF names, licences |
| `SCHEMA.md`, `CHANGELOG.md` | The human-readable contract, and one place for consumers to watch changes (F16) |

Columns: `time_utc` (RFC 3339 with `Z`; the instant of the reading),
`station_id`, `site_id`, `variable`, `statistic`, `value`, `unit`, `level`,
`approval`, `quality`, `qualifiers`.

| variable | CF standard name | Published unit |
|---|---|---|
| `snow_depth` | `surface_snow_thickness` | cm, whole numbers |
| `distance_to_surface` | none | m |
| `air_temperature` | `air_temperature` | °C |
| `relative_humidity` | `relative_humidity` | % |
| `air_pressure` | `surface_air_pressure` | hPa |
| `battery_voltage` | none | V |

Parquet is added only if a consumer asks for it.

### 6.7 Publishing and monitoring (F2, F3)

- **Publish** runs hourly at an off-the-hour minute (GitHub delays jobs
  scheduled on the hour). It always deploys. A decode error marks that
  message `quality: missing` with qualifier `decode_error` and counts it in
  `status.json`; it never stops publishing.
- **Check** runs after publish. It compares the alarm state with the
  previous run and **opens a GitHub Issue** when an alarm starts (no message
  for more than 12 h in season, clock offset over 30 min, battery under
  3.5 V, decode errors, a snapshot mismatch). It **closes the Issue** when the
  alarm clears. MRBS owners "watch" the repo, so alerts go to whoever the
  organization says, not to whoever created the workflow. One email per
  event, not 24 a day.
- **The site computes data age in the visitor's browser** from the last
  observation time, so a stopped pipeline still shows as stale.
- **Keep-alive:** GitHub disables schedules after 60 days without repo
  activity (confirmed), and silently. `OPERATIONS.md` has a first-of-month
  task: open the site, check the build date, commit the dated keep-alive file.
- **Cost rule** (in `README.md`): the repo must stay public. Actions is only
  free on public repos; hourly builds would exceed the private allowance.

## 7. Presentation: two linked sites

- **Station site (MRBS):**
  - current conditions first (mobile-first)
  - charts per station and site, with labels
  - downloads
  - station health
  - **times shown in local time (America/Vancouver), labelled as such**
    (files stay UTC)
  - overlays only once licensed (§10)
- **Forecast site (powWX):** as now. Each site works without the other; a
  shared link bar connects them and the Avalanche Canada forecast.
- **Safety (R6):**
  - live data age
  - stale data marked
  - "Provisional" or "Approved" visible
  - disclaimer
  - **terms of use agreed with the MRBS board, as a phase 3 exit criterion**
- Static page using Chart.js, as its own copy (no shared code with powWX).

## 8. Quality control, corrections and review

### 8.1 Automatic QC (level `qc`)

Range, step and spike checks; no-echo values to `missing`; SD/radio
agreement; negative snow depth flagged `suspect`. Rules set labels and never
delete. The same rules apply to third-party data once licensed: the Kasiks
High temperature fault of 2026-09-25 would fail the neighbour check.

### 8.2 Corrections: an operation log (level `corrected`)

```yaml
- id: C001
  op: delete            # delete | spike_filter | threshold | gap_fill | offset | drift
  station: powometer
  variable: snow_depth
  from: 2026-09-26T19:00:00Z
  to:   2026-09-26T21:31:00Z
  reason: "Readings disturbed during the 2026-09-26 site visit"
  by: JK
  date: 2026-09-29
- id: C002
  op: drift
  station: powometer
  variable: snow_depth
  from: 2026-09-26T21:31:00Z
  to:   2027-06-15T00:00:00Z
  params: {end_offset_cm: -4}     # e.g. from the season-end zero-depth measurement
  reason: "Bare-ground reading 4 cm off at season end"
  by: JK
  date: 2027-06-20
```

- **`delete` doesn't remove values.** It sets quality `poor` and a qualifier,
  so the published record stays auditable, and `best.csv` excludes them
  (F14).
- **The zero-depth reference is not a correction** (§5.4). Corrections only
  handle drift and offsets beyond it.
- **Validation:** every entry is checked in the pull request (known
  operation, valid range, a reason given), so errors appear as a readable
  comment.

### 8.3 Approval

To approve a period, add it to `approvals.yaml` and commit a snapshot file
(`approved/<station>/<period>/<variable>.csv`, with a hash). Published data
for approved periods comes from the snapshot. Rebuilds are still computed and
compared, and a difference raises an alarm (§6.7), never a silent change.

### 8.4 Aquarius

- **Transitional:** past seasons corrected in Aquarius may be imported as
  snapshots, with provenance (the export as delivered, the source SD
  downloads, who and when). **The import refuses any time shift against
  raw**; the old archive was 8 h off unnoticed.
- **Going forward:** corrections are recorded in `corrections.yaml`, so the
  record never requires Aquarius (R3). The employer's agreement to this use
  is an open question.

### 8.5 Later: a light review app (parked)

A separate project editing `corrections.yaml`, `field_visits.yaml` and
`approvals.yaml`, with a chart preview. **Caveat (F15):** "no server" is
unverified. GitHub's web login flow needs a token exchange that a static page
can't do alone. The options are a personal access token pasted by the user, a
tiny free proxy, or editing in GitHub itself. The data model doesn't depend
on that choice.

## 9. Integration with powWX

- powWX reads `v1/sites.json` and, optionally, `best.csv` or
  `best_hourly.csv`. No shared code; the station never reads from powWX.
- powWX caches the last good files and degrades gracefully.
- powWX's own changes happen when Julian chooses. They also fix its
  historical POW-O-METER series: sheet rows before 2026-09-22 are
  America/Los_Angeles time, not UTC−8 (F9).
- **Licensing:** powWX already publishes MoTI #58 values. Same question,
  same ministry (§10).

## 10. Hosting, licence and privacy

**Hosting:** public repo with Pages, under the MRBS organization before
phase 2 (§4).

**Licences:**
- Code: MIT, and POW-O-METER data: CC BY 4.0 crediting MRBS (both *to
  confirm with MRBS*).
- **BC MoTI observations are "Access Only"**: reproduction needs written
  permission. Station locations are under the Open Government Licence – BC.
  Verified 2026-10-02; the records are saved in `docs/research/evidence/`
  because both are marked "PENDING ARCHIVE".
- **Until permission arrives:** no MoTI values in the public repo, the files
  or on the site. Showing them on a chart also counts as reproduction (F11).
- **Julian's permission request** should name both projects (POW-O-METER and
  powWX), both uses (display and download), and the PCDS-sourced history.

**Local only** (`.gitignore` before the first commit):
- `misc/`, `resources/`, `data/Aquarius/`, `data/Google Sheet/`
- the original RockBLOCK export
- the old Apps Script
- `data/Cleaned/` (derived)
- `claude resume session *.txt` (F10)

**Safeguards (F10):**
- The public CI check scans only for **patterns**: a 15-digit IMEI, the
  spreadsheet-ID format, email addresses.
- The home-coordinate check stays local, as a pre-commit hook installed by
  hand, so the repo never contains the coordinates it protects.
- The committed Apps Script copy is scrubbed of the IMEI and spreadsheet ID,
  including its test samples. Those values move to Apps Script Properties.
- Volunteers are recorded by initials.
- Local-only files are backed up somewhere other than this laptop.

## 11. Handover (R3)

| Artefact | Content |
|---|---|
| `README.md` | What this is, owners, site, how to get help, **the repo must stay public** |
| `OPERATIONS.md` | Plain-language runbooks:<br>• after a site visit (upload SD, zip if large; add the visit with the zero-depth distance and photos)<br>• adding a correction<br>• approving a season<br>• **what an alarm Issue means**<br>• re-exporting from the RockBLOCK console<br>• renewing or suspending RockBLOCK<br>• adding a station<br>• **monthly:** check the site and build date, commit the keep-alive<br>• **yearly:** sign in to the Gmail account, hold an "upgrade day" for software versions, archive the repo to Zenodo or an MRBS drive (F16) |
| `CLAUDE.md` | Rules and context for AI assistants |
| Pinned dependencies | Lockfile and pinned Action versions, so builds don't break on their own (F12) |

## 12. What stays and what goes

| Component | Fate |
|---|---|
| Gmail + Apps Script intake | Stays |
| Sheet `Data` / `Resampled` | Written as now during the transition. Outside the season: regenerate the `Data` tab from v1, or publish an erratum about the pre-2026-09-22 LA-time rows (F9) |
| Looker Studio | In parallel until the station site is proven |
| Avalanche Canada's sheet access | Keeps working; v1 offered when ready |

## 13. Scope: core now, parked later

**Fixed now** (expensive to change later):
- raw archive layout
- MRBS ownership before publishing
- privacy before the first push
- the published contract, including the time convention (point instants,
  UTC), station and site IDs, labels, and the firmware-era table (F15)

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Foundations** | Repo (Julian's account is OK), `.gitignore`, local hook and CI pattern check, raw-immutability check, sanitised export, scrubbed Apps Script copy, licence files, backup of local-only files | A planted test secret is blocked; nothing sensitive in history. The MRBS organization is requested |
| **1. Record** | Raw archive; `powometer/` (decoders, timing, SD, merge with statistic, snow-depth derivation, QC, operation log, labels, snapshots, on-the-hour series); config; `tests/` with committed fixtures | A rebuild reproduces the post-2026-09-22 Data rows (except documented differences) and `data/Cleaned/UTC/` values. Named tests pass: both DST changes, overlapping SD downloads, both radio date formats (the 22 messages since 2026-09-22 as a fixture), the v1.3 statistic change, an approved-snapshot mismatch. Two rebuilds are identical |
| **2. Publish** | Requires the MRBS organization. Publish + check Actions, v1 outputs (POW-O-METER only), alarm Issues, keep-alive | Two weeks of clean runs; an injected fault opens and then closes an Issue |
| **3. Station site** | Mobile-first site, labels, local time, live data age, downloads, link bar | Terms of use agreed with MRBS; used instead of Looker for a month |

**Parked:**
- nearby-station overlay (needs MoTI permission)
- powWX integration
- Avalanche Canada on v1
- RockBLOCK HTTP intake: it must answer HTTP 200 within 3 s; retries 14
  times over about 6 days; fields documented (F15)
- review app
- importing past Aquarius seasons
- Windy / CWOP / Zenodo publishing
- NetCDF, Parquet
- an external temperature sensor for the ultrasonic

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Private data leaks (irreversible) | Phase 0 safeguards before the first push |
| MoTI data published without permission | None in the public repo until written permission |
| Consumers break at handover | MRBS organization before phase 2 (F1) |
| Silent failure: schedule disabled, or the pipeline stopped | Live data age in the browser; monthly check; Issue alarms (F2, F3) |
| Approved data changes silently | Snapshots and comparison (F4) |
| Mixed min/median statistics produce a 7 cm artefact | Firmware-era table; merge by statistic (F5) |
| Snow depth reference ambiguity | One derivation, one measured reference (F6) |
| Repo becomes private and costs money | Cost rule in `README.md` |
| Only copy of the raw archive is on GitHub | Yearly archive to Zenodo or an MRBS drive |
| Builds break as tools age | Pinned versions; yearly upgrade day; `CLAUDE.md` |
| Live chain disrupted in winter | Consumers switch only outside the season |
| Users treat station data as a forecast | Disclaimer, terms of use, Avalanche Canada link |

## 15. Decisions, open questions, unverified assumptions

**Decisions**

| Date | Decision |
|---|---|
| 2026-09-29 | Raw-first pipeline in the style of powWX; separate station repo; public repo + Pages; Looker in parallel; the station owns site history; two linked sites |
| 2026-09-30 | MRBS owns the station project; powWX stays personal; one-way dependency; PCDS as the archive source; QC on third-party data |
| 2026-10-02 | Labels (`raw`/`qc`/`corrected`; approvals; WaterML2 quality); snow depth in whole cm; CF names; operation log; Aquarius optional; MoTI values withheld until permission; build phases 0–3, park the rest |
| 2026-10-03 | Review findings adopted: MRBS organization before publishing; publish-always plus Issue alarms; approval snapshots; firmware-era statistics; snow depth as a derivation; point readings plus an on-the-hour series instead of hourly means; published-CSV intake; pattern-only CI check; smaller output set; handover routines |

**Open questions**
1. The MRBS organization and second owner; licence confirmation; terms of
   use.
2. MoTI permission (pending; to cover both projects).
3. Which Google account owns the Apps Script trigger?
4. What would Avalanche Canada want from v1?
5. Is Julian's employer comfortable with Aquarius being used for this station?

**Unverified assumptions**
- Whether commits made by the build itself count as activity for GitHub's
  60-day rule. The monthly human keep-alive covers this either way.
- Whether RockBLOCK HTTP delivery follows the redirect that an Apps Script
  web app responds with (only relevant if the parked intake is built).
- PCDS latency and DST handling (deferred tests, `data/PCDS/README.md`).
- Research-report items marked *[unverified]*.

## Appendix: evidence index

| Topic | Where |
|---|---|
| Independent review | `docs/review/REVIEW_2026-10.md` |
| Standards and publishing research | `docs/research/STANDARDS_AND_PUBLISHING.md` |
| Licensing evidence | `docs/research/evidence/` |
| Firmware, fixes | `scripts/POW_O_METER_v1_3.ino`, `IMPROVEMENTS.md`, `scripts/FIELD_CARD.md` |
| Current Apps Script | `scripts/Google Apps script v2.js` |
| SD processing | `scripts/clean_sd_data.py`, `scripts/reconstruct_frozen_period.py`, `data/Cleaned/README.md` |
| Radio history | `data/RockBLOCK/README.md` |
| Nearby stations | `data/PCDS/README.md` |
| powWX | `C:\Users\Julian.Krick\src\powWX` |
