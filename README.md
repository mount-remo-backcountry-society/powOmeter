# POW-O-METER

A volunteer-built snow and weather station at the top of Shames Mountain, BC
(1,150 m), owned by the **Mount Remo Backcountry Society (MRBS)**. It measures
snow depth, air temperature, humidity and pressure, and reports by Iridium
satellite.

This repository holds the station's **raw data, the code that turns it into
published data, and the documentation** to run it.

> **Status (2026-10):** under construction (phase 0 of
> `docs/PLAN_PHASE_0_1.md`). Nothing is published from here yet; the
> existing Google Sheet remains the live source.

## Where things are

| Path | What |
|---|---|
| `raw/` | Raw data exactly as received. **Never edited**; a check rejects changes |
| `config/` | Stations, sites, field visits, corrections, approvals *(phase 1)* |
| `powometer/` | The processing code *(phase 1)* |
| `scripts/` | Station firmware, field card, the Google Apps Script intake, older processing scripts |
| `docs/` | Design, plan, research, independent review |
| `tools/` | Privacy and raw-data checks |
| `OPERATIONS.md` | How to run the station's data, in plain language |
| `CLAUDE.md` | Instructions for AI assistants working on this repository |

## Important rules

- **Raw data is never edited.** Corrections go in `config/corrections.yaml`.
- **Nothing private is committed.** See the list at the top of `.gitignore`.
  Install the local check once per computer: `sh tools/install-hooks.sh`.
- **Once public, this repository must stay public.** GitHub Actions is free
  only for public repositories; a private repo would run out of free minutes.
- **Station data are not a forecast.** See `LICENSE-DATA.md`.

## Licences

Code: MIT (`LICENSE`). Station data: CC BY 4.0, crediting MRBS
(`LICENSE-DATA.md`). Both confirmed by MRBS (2026-10).

## Repository

`github.com/mount-remo-backcountry-society/powOmeter`, owned by the MRBS
GitHub organization.

## Contact

Mount Remo Backcountry Society. Maintainer: Julian Krick.
