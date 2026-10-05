# Operations guide

Plain-language instructions for running POW-O-METER's data. Most tasks are
done in the GitHub web interface at
<https://github.com/mount-remo-backcountry-society/powOmeter>; no software
is needed. Tasks marked **(computer)** need the repository on a computer with
Python. An AI assistant can walk you through them: point it at `CLAUDE.md`.

**Golden rules:** never edit or delete anything under `raw/`; never put
names, email addresses or home locations into the repository.

---

## One-time setup on a computer (computer)

1. Install Python 3.12 and Git, then clone the repository.
2. `python -m venv .venv`, then `.venv/Scripts/pip install -r requirements.txt`
   (on Mac/Linux: `.venv/bin/pip`).
3. Create `.private/home_coords.txt` with one line, `latitude,longitude`, of
   any location that must never appear in the repository. It is never
   committed.
4. `sh tools/install-hooks.sh`. From then on, every commit is checked for
   private information first.
5. Check everything works: `.venv/Scripts/python -m pytest` (all tests pass)
   and `.venv/Scripts/python -m powometer build`.

## After a site visit

1. **Copy the SD card's files** into a new folder `raw/sd/<YYYY-MM-DD>/`
   (`_02` for a second download on the same day), unchanged. In GitHub:
   **Add file → Upload files**, drag the files in, and type the folder name
   in front of the file name. Files over 25 MB: zip them first (zip files
   in the folder are read directly; any file whose name starts with `LOG`
   and ends `.CSV`, in any letter case, is read).
   **Upload photos without location.** Phone photos carry GPS coordinates;
   the commit check refuses images that do. Web edits on GitHub skip your
   computer's check, so type only initials and times there.
2. **Record the visit** in `config/field_visits.yaml` (pencil icon to edit).
   Copy an existing entry and change it:
   - `date`, and `who` as **initials only**
   - `disturbed`: when people were at the station, in **UTC**. Local time in
     BC is UTC−7 all year, so add 7 hours
   - `remount`: true, false, probable or unknown
   - the **snow depth you probed under the sensor**; on bare ground, a tape
     measurement from the sensor face to the ground
3. **If you re-mounted the sensor** (raised, lowered, moved), close the
   current entry in `config/mounts.yaml` with an `until` time and add a new
   one. The new reference = old reference + the jump in distance at the
   re-mount, or a tape measurement on bare ground. Mark it `estimate` if
   unsure.
4. Commit. The checks run automatically; a red cross on the commit means a
   problem. Open it to read the message.
5. After the next build, check `status.json` → `info` → `sd_downloads`: each
   LOG file is listed with the lines read, lines rejected as unreadable,
   rows kept, rows whose time could not be recovered, and any clock offset
   carried over from the previous download. An unreadable file, or more
   than 1 % rejected lines, also appears under `alarms`.

## Adding a correction

1. Edit `config/corrections.yaml`; copy an existing entry.
2. Give it the next id (`C004` …), the operation (`delete`, `offset`, `drift`,
   `threshold`, `spike_filter`, `gap_fill`), the variable, either one
   `message` (a satellite transmit time) or a `from`/`until` window in UTC,
   a **reason**, your initials and today's date.
3. Commit. `delete` never removes data: it marks values `poor`, so they drop
   out of `best.csv` but stay visible and auditable. A correction to
   `distance_to_surface` also changes the snow depth calculated from it;
   `offset` and `drift` need one named variable, not `all`.
4. If `status.json` lists your correction under
   `corrections_matching_nothing`, its time or variable doesn't match any
   data. Check it.

## Approving a season (computer)

After reviewing a season (usually after the SD download at season end):

1. Make any corrections first (above), and rebuild:
   `python -m powometer build`.
2. Freeze the period:
   `python -m powometer approve snow_depth 2025-01-26T18:58:34Z 2025-09-28T18:03:32Z 2025-season JK`
   (variable, from, until, a name, your initials).
3. Paste the printed lines into `config/approvals.yaml`, then commit both
   the snapshot under `approved/` and `approvals.yaml`.
4. From then on, that period is published from the snapshot. If a later
   change would alter it, the build raises an alarm instead of changing
   approved data.

## Re-exporting messages from the RockBLOCK console

The RockBLOCK history is the backstop if the email intake misses messages.

1. In the RockBLOCK web console, export the message history as CSV.
2. **Delete the `Approx Lat/Lng` column** (it can locate where test messages
   were sent from).
3. Upload it as a NEW file `raw/radio/rockblock-export-<YYYY-MM-DD>.csv`.
   Never replace the old one. Duplicates are merged automatically.

## Adding a station

1. Add it to `config/stations.yaml` with `enabled: false` and
   `publish: false`.
2. Check its data licence **before** publishing anything (BC Ministry
   stations are "Access Only": written permission needed).
3. A new kind of station needs a decoder in `powometer/decoders/`. Ask a
   programmer or an AI assistant, pointing it at `CLAUDE.md`.

## Renewing or suspending the RockBLOCK subscription
*(to be written)* Note: while suspended, the station still reports
"Message sent!", but nothing arrives. Only the receiving side shows delivery.

## The publish workflow

Every hour (once the repository is public) the **publish** workflow:
fetches new satellite messages from the sheet's Messages tab into
`raw/radio/messages_tab/` (a new file only when there is something new),
rebuilds everything, publishes the files, and opens or closes alarm Issues.

- **Run it by hand:** Actions tab → **publish** → **Run workflow**.
- **Test the alarms:** run it by hand with some text in *inject_alarm*
  (e.g. `test`). An Issue titled "Alarm: TEST …" opens; run it again with
  the box empty and the Issue closes.
- **The Messages-tab link** is stored as the secret `MESSAGES_CSV_URL`
  (Settings → Secrets and variables → Actions). It comes from the sheet:
  File → Share → Publish to web → Messages tab, CSV. Never put the link in a
  file. If it leaks: Publish to web → Stop publishing, publish again, and
  replace the secret.
- **If the workflow itself fails** (red cross in the Actions tab), GitHub
  emails whoever last changed the workflow. Open the failed run and read the
  first red step.

## When an alarm Issue opens

GitHub emails everyone who watches the repository (top right: **Watch** →
**All activity**). The Issue closes by itself when the alarm clears.

| Alarm | What it means | What to do |
|---|---|---|
| No satellite message for 12 h (in season, Oct–mid June) | The station, its modem or the email/sheet chain stopped | Check the RockBLOCK console for recent messages. If they are there, the Gmail/Apps Script/sheet chain is stuck: check the sheet's Messages tab and the script's Executions. If not, the station: battery, snow on the antenna, or a hang (no hardware watchdog: a hang drains the battery) |
| Station clock off by more than 30 min | The real-time clock drifted or reset | Data are still timed (from the transmit time). Reset the clock at the next visit; check the RTC coin cell |
| Battery below 3.5 V | Not enough charge (cold, short days, snow on the panel) | Watch the trend; clear the solar panel at the next chance |
| Messages could not be decoded | A garbled or unexpected payload | Look at the message in the RockBLOCK console. One-off: ignore, it clears after a day. Repeating: firmware or modem problem |
| Approved data differs from its snapshot | A code or config change would alter approved data | Investigate before anything else; never just re-approve (CLAUDE.md rule 7) |
| SD file unreadable / many unreadable lines | An SD download is damaged or in a new format | Check the uploaded files against the card |
| Distance step at a visit without a re-mount | Probably an unrecorded re-mount, or fresh snow | If re-mounted: add a mount in `config/mounts.yaml`. If snowfall: nothing; the label stays "estimate" until the next mount change |
| TEST (injected) | Someone tested the alarms | Nothing; the next normal run closes it |

## Monthly

1. Open the published page and check that "Files built" is recent.
2. Edit `KEEPALIVE.md` in the GitHub web interface: put today's date, and
   commit. GitHub silently switches off hourly workflows in a repository
   with no activity for 60 days; this prevents it.

## Yearly
*(phase 2)* Sign in to the station's Gmail account; upgrade day for software
versions (`requirements.in` → `requirements.txt`); archive the repository to
Zenodo or an MRBS drive.
