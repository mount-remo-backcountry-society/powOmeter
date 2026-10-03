# Operations guide

Plain-language instructions for running POW-O-METER's data. Most tasks are
done in the GitHub web interface; no software needs to be installed.

> **Draft (2026-10):** headings are in place; the steps are filled in as
> each phase is built (`docs/PLAN_PHASE_0_1.md`).

## One-time setup on a new computer

1. Clone the repository.
2. Create `.private/home_coords.txt` with one line, `latitude,longitude`, of
   any location that must never appear in the repository, e.g. your home.
   This file is never committed.
3. Run `sh tools/install-hooks.sh`. From then on, every commit is checked for
   private information first.

## After a site visit
*(phase 1)* Upload the SD files; record the visit (snow depth measured under
the sensor, time, whether the sensor was re-mounted, photos).

## Adding a correction
*(phase 1)*

## Approving a season
*(phase 1)*

## When an alarm Issue opens
*(phase 2)*

## Re-exporting messages from the RockBLOCK console
*(phase 1)*

## Renewing or suspending the RockBLOCK subscription
*(to be written)*

## Adding a station
*(phase 1)*

## Monthly
*(phase 2)* Open the station site, check the build date, commit the
keep-alive file.

## Yearly
*(phase 2)* Sign in to the station's Gmail account; upgrade day for software
versions; archive the repository to Zenodo or an MRBS drive.
