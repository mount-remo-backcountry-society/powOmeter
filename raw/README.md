# Raw data: never edited

Everything under `raw/` is stored exactly as received. A CI check rejects
any change to an existing file, so new data is only ever **added**.
Corrections belong in `config/corrections.yaml`, never here.

| Folder | Content |
|---|---|
| `radio/` | Satellite messages. `rockblock-export-2026-09-29.csv` is the RockBLOCK console history (2,588 messages since 2024-04-18), sanitised: the `Approx Lat/Lng` column is removed. Provenance and caveats: `data/RockBLOCK/README.md` |
| `sd/<visit-id>/` | SD-card downloads, one folder per download, named by date (`_02`, `_03` for later downloads on the same day). Files as copied from the card: `DATA.CSV`, `LOG.CSV`, `TRACKING.CSV`, `TRANSMIT.CSV` (some visits also have `… 2.CSV` copies) |

## Known properties of the SD files

- **Device times are fixed UTC−8**, from the station's real-time clock,
  which was wrong for long periods (Iridium time-reference error from
  2026-01-14, frozen clock 2025-01-31 → 02-17, about 2 days behind in summer
  2026). The pipeline repairs times and labels them; the files are not
  changed.
- **Downloads overlap.** The card is copied without being cleared, so later
  downloads repeat earlier rows. The pipeline deduplicates them.
- Rows before 2025-01-26 18:58:34 UTC are bench and transit readings, before
  deployment.
- The files contain no coordinates, IMEI or personal data (checked
  2026-10-02 and by `tools/privacy_check.py`).

## Adding a new download

Create `raw/sd/<YYYY-MM-DD>/` and upload the card's files unchanged (drag
and drop in the GitHub web interface works; zip a file larger than 25 MB).
See `OPERATIONS.md`.
