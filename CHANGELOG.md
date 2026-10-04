# Changelog: published data contract

Consumers (Avalanche Canada, powWX, anyone else) can watch this file for
changes to the published format. See `SCHEMA.md`.

## v1.0: 2026-10-04 (not yet published)

First version: `observations.csv`, `best.csv`, `best_hourly.csv`,
`latest.json`, `status.json`, `sites.json`, `datapackage.json`.

Compared with the Google Sheet it will eventually replace:
- Times are true UTC. The sheet's "(PST)" columns were America/Los_Angeles
  before 2026-09-22.
- Snow depth is in whole centimetres, per site and mounting period, with
  data-derived references. See `docs/DIFFERENCES_v0.md`.
- Every value carries level, approval, quality and qualifier labels.
