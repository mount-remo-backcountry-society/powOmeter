# RockBLOCK message history, raw

`messages-export-2026-09-29.csv` is the full message history of the station's
RockBLOCK 9603, exported by Julian from the RockBLOCK web console on
2026-09-29. **Raw record: do not edit.** Corrections belong in config, not in
this file (see `docs/DATA_PIPELINE_DESIGN.md` §3).

- Original file name: `messages-1790725506336.csv`
- SHA-256: `57199b4789523c284e0a1bfde3b0425468e3b65b999f693c1cf6b0891f85c2fb`
- 2,588 messages, 2024-04-18 03:17:12 → 2026-09-29 18:16:25 UTC; 2,587 MO,
  1 MT; 3,204 credits

## Public copy

The original stays **local only** (git-ignored), because its `Approx Lat/Lng`
column locates a private address. The repository holds a sanitised copy:

- `raw/radio/rockblock-export-2026-09-29.csv`: identical rows and fields,
  with the `Approx Lat/Lng` column removed for **all** rows (no UTF-8 BOM).
- SHA-256: `1f715d4aa79b34cad79dcba4d0e168fb26a60cff7eb457f679d166e4b5c613ed`

## Columns

`Date Time (UTC)` (format `29/Sep/2026 18:16:25`), `Device`, `Direction`
(MO/MT), `Payload` (hex, exactly as sent), `Approx Lat/Lng` (Iridium estimate,
accurate to a few km at best), `Payload (Text)`, `Length (Bytes)`, `Credits`.

**No MOMSN column.** Match to other records on `(Date Time (UTC), Payload)`.

## Caveats

- **Backyard tests before deployment.** Everything up to 2025-01-25 18:11 UTC
  was sent from home during testing: about 24 km from the station by the
  Iridium location, against about 2 km afterwards. The first message from the
  mountain (2025-01-26 19:01:45) still carries readings queued at home
  (distance about 1.00 m). The SD-derived deployment start is
  2025-01-26 18:58:34 UTC (`scripts/clean_sd_data.py:50`).
- **Summer 2026 gap.** Nothing between 2026-05-28 23:02:42 and
  2026-09-26 20:39:40 (the subscription was suspended over summer). Two
  periods inside it:
  - **2026-05-28 → 2026-06-19 (real time):** the station logged **151
    "Message sent!"**, i.e. the modem reported success while nothing reached
    RockBLOCK (`data/Site Visits/2026-06-19/LOG.CSV`).
  - **2026-06-19 → 2026-09-26:** the modem was disconnected at the
    2026-06-19 visit. There were 1,536 attempts, all failing with error 5,
    "no modem" (`data/Site Visits/2026-09-26_02/LOG.CSV`).
  - The station clock ran about 2 days behind throughout, so the LOG.CSV
    timestamps read 2 days earlier than the real dates above.
  - The SD downloads cover the whole period.
- **Early formats.** Before the v1.x payload format there are
  comma-separated messages (April 2024), "Hello World", and in December 2024
  readings with a 3-digit humidity field ("100"). All are tests.
- **Privacy.** `Approx Lat/Lng` for the backyard tests locates a private
  address. The public copy drops this column for **all** rows (simpler and
  safer than picking rows; independent review F10, 2026-10-02).
