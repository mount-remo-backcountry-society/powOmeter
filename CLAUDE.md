# Instructions for AI assistants

You are helping maintain POW-O-METER, a volunteer-run snow station owned by
the Mount Remo Backcountry Society. The people asking may not be programmers:
explain plainly, do one thing at a time, and confirm before anything
irreversible.

## Read first

- `docs/DATA_PIPELINE_DESIGN.md`: the design (current: v4). Its §5
  principles are rules, not suggestions.
- `docs/PLAN_PHASE_0_1.md`: what is being built now.
- `OPERATIONS.md`: routine tasks.

## Hard rules

1. **Never modify or delete files under `raw/`.** Raw data is immutable; CI
   rejects changes. Add new files; put corrections in
   `config/corrections.yaml`.
2. **Never commit private material.** Respect `.gitignore`. Never print or
   commit the station IMEI, the spreadsheet ID, email addresses of people,
   or the maintainer's home location. Run `python tools/privacy_check.py`
   before committing.
3. **Never commit BC MoTI observation data** (Kasiks High/Low, Shames #58)
   unless `docs/` records written permission from the Ministry. Their licence
   is "Access Only".
4. **The published format is a contract** (`SCHEMA.md` from phase 1).
   Changes are add-only; never rename or reorder columns. Record changes in
   `CHANGELOG.md`.
5. **One-way dependency:** this repo never reads from powWX (a separate,
   personal project).
6. **Don't change anything live during the winter season** (the Gmail /
   Apps Script / Google Sheet intake, and what consumers read).
7. **Approved data is frozen** (design §8.3). Never regenerate an approved
   snapshot to make a test pass; a mismatch is an alarm to investigate.

## Facts that are easy to get wrong

- Times in data files are **UTC**. The station's own log uses **fixed
  UTC−8** all year. BC civil time is UTC−7 all year. PCDS uses Pacific time
  **with** DST.
- Snow depth = mount reference − measured distance (`config/mounts.yaml`).
  The reference changes **every time the sensor is re-mounted**: it was
  raised during seasons as snow built up and lowered in June 2026. Prefer
  bare-ground medians; at a re-mount, new = old + the distance step.
- The station's real Iridium IMEI does **not** pass the Luhn check digit:
  never filter IMEIs by Luhn in the privacy check.
- Raw radio intake writes one NEW file per fetch (`raw/radio/messages_tab/`);
  never append, because raw files are immutable.
- Run: `python -m powometer validate | build | approve …`; tests: `python -m pytest`.
- Before firmware v1.3 (deployed 2026-09-26) the radio sent the burst
  **minimum**; since then, the **median**.
- The station was relocated about 70 m on 2026-09-26, moved 18:15–20:45 UTC
  (11:15–13:45 local): two sites (`config/sites.yaml`); snow depth is never
  pooled across them. (powWX's 21:31Z is when firmware v1.3 started.)
