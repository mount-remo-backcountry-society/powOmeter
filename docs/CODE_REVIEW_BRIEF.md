# Independent code review brief: phase 1

For an independent reviewer (another AI model or a person) who has **not**
seen the conversation in which this code was written. It says what to check,
not what to conclude.

## Your task

Review the phase 1 code of the POW-O-METER data pipeline for correctness,
robustness and maintainability **before anything is published** (phase 2
publishes data that ski-hill and backcountry users may rely on). The code was
written by an AI assistant with the owner. Treat its comments, docstrings and
documents as claims to verify, not as facts.

**Rules:**
- **Do not modify, commit or push anything tracked in the repository.**
- You may run the tests and the build. They write only to `out/` and
  temporary folders, both git-ignored.
- You may create scratch files outside the repository.
- **Do not read** `misc/`, `resources/`, `data/Aquarius/`,
  `data/Google Sheet/`, `data/PCDS/`, `.private/`,
  `data/RockBLOCK/messages-export-*.csv`, `docs/research/evidence/*.json` or
  `*.html`, `scripts/Google Apps script.txt` or
  `scripts/Google Apps script v2.js`. They are private, licensed or contain
  identifiers.
- Do not contact anyone or any external service except to read public
  documentation.

## Background

- **The station:** a volunteer-built snow station (Feather M0, ultrasonic
  snow-depth sensor, temperature/humidity, pressure, Iridium modem) owned by
  the Mount Remo Backcountry Society.
- **Readings:** about every 17 min to an SD card; a subset by satellite
  about hourly.
- **Clock problems:** the station clock was wrong for long periods
  (Iridium time-reference change, a frozen clock, about 2 days behind in
  summer 2026), so much of the code recovers true times.
- **Maintenance:** the owner is not the only intended maintainer. A
  non-programmer must be able to take over with AI help.
- **Design and plan:** `docs/DATA_PIPELINE_DESIGN.md` (v4) and
  `docs/PLAN_PHASE_0_1.md`.

## Set up and run

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Mac/Linux: .venv/bin/pip
.venv/Scripts/python -m pytest                     # expect 2,643 passed
.venv/Scripts/python -m powometer validate
.venv/Scripts/python -m powometer build            # writes out/v1/
```

## Read, in this order

1. `docs/DIFFERENCES_v0.md`: what the pipeline claims to reproduce and to
   change, with counts.
2. `SCHEMA.md`: the published contract.
3. `powometer/`, in pipeline order: `decoders/powometer_payload.py`,
   `timing.py`, `radio.py`, `sd.py`, `frozen.py`, `assemble.py`, `qc.py`,
   `corrections.py`, `approval.py`, `hourly.py`, `build.py`, `config.py`,
   `__main__.py`.
4. `config/*.yaml`: the comments explain each file.
5. `tests/`, and how each fixture was built (`tests/fixtures/build_*.py`).
6. `tools/privacy_check.py`, `tools/raw_immutable.py`,
   `.github/workflows/checks.yml`, `.gitignore`, `.gitattributes`.
7. For comparison: the original scripts `scripts/clean_sd_data.py`,
   `scripts/reconstruct_frozen_period.py`, `scripts/apps_script/Code.gs`.

## Questions to answer

1. **Correctness of the claims in `docs/DIFFERENCES_v0.md`.** Do the tests
   actually prove what the document says ("reproduced exactly", "byte
   identical", "matches the sheet")? Could a test pass while the claim is
   false? Is anything only *asserted*?
2. **Time handling.** Timezone-aware UTC throughout? Naive versus aware
   datetimes mixed anywhere? The fixed UTC−8 station clock, DST in other
   sources, month boundaries, interval-end versus instant semantics, the
   end-exclusive windows.
3. **Clock recovery** (`sd.py`, `frozen.py`): the carried-sync extension and
   the predecessor rule; the frozen-period anchor alignment. What inputs would
   silently produce wrong times? Are the independent checks in the tests
   genuinely independent?
4. **Merging SD and radio** (`assemble.py`): can a reading be double-counted
   or wrongly dropped? Value-code collisions within 3 h; readings with
   missing values; the firmware-era statistic.
5. **Snow depth and QC** (`qc.py`, `config/mounts.yaml`): the reference
   lookup at period boundaries, gaps between mounts, rounding, label
   inheritance, threshold choices. Could a safety-relevant wrong value reach
   `best.csv` labelled "good"?
6. **Corrections and approval:** each operation's semantics, order effects,
   and the snapshot comparison (could approved data change without an
   alarm?).
7. **Outputs:** does `build.py` deliver what `SCHEMA.md` promises: columns,
   order, units, missing values, sorting, JSON types? Determinism?
8. **Privacy safeguards:** can anything private reach the repo? Check the
   patterns, the allowlist, the history scan, the masked output, and the
   `.gitignore` coverage. **Run the history scan yourself.**
9. **Robustness to future data:** a new SD download, a late radio message,
   a new firmware era, a new station, an empty or malformed file. Which of
   these fail loudly, and which fail silently?
10. **Maintainability for a non-programmer with AI help:** naming, comments,
    error messages, `OPERATIONS.md` steps. Would the next maintainer
    understand where to look?
11. **Tests:** important behaviour that is untested; tests that are tautological
    or too loose; fixtures that could drift from their sources.
12. **Anything else** that should be fixed before phase 2 publishes data.

## Report format

Write your report to `docs/review/CODE_REVIEW_2026-10.md`. This is the only
file you may create inside the repository; leave it uncommitted for the owner.

1. **Verdict** in 2–4 sentences: ready for phase 2, ready after changes, or
   not ready.
2. **Findings, most important first.** For each: file:line; what is wrong;
   a concrete input or scenario that triggers it; the impact (wrong
   published value, silent failure, privacy, maintainability); a suggested
   fix. Label each **Confirmed** (reproduced: give the command or test) or
   **Plausible** (reasoned).
3. **Claims checked:** which statements in `docs/DIFFERENCES_v0.md` and the
   docstrings you verified or refuted, and how.
4. **What you did not check.**

Be specific and concise. A finding with a reproducing command is worth more
than ten opinions.
