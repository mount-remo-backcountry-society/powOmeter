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

The **publish** workflow is started every 10 minutes and whenever a
satellite message arrives, by the intake Worker (next section). GitHub's own
5-minute schedule is a backup only: on its own GitHub ran it about every 2–5
hours. Each run:
1. fetches new satellite messages from the Worker's mailbox into
   `raw/radio/worker/` and from the sheet's Messages tab into
   `raw/radio/messages_tab/` (a message that comes both ways is kept once),
   and new readings of the nearby stations into `raw/avcan/` (a new file
   only when there is something new)
2. rebuilds everything
3. adds the newest webcam frames to the time lapse
4. publishes the station page and the files
5. opens or closes alarm Issues

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

## The intake Worker (Cloudflare)

A small program on Cloudflare's free plan, in the station's Cloudflare
account (signed up with the station's Gmail address). Its code is in
`worker/`; GitHub deploys it by itself when a change to `worker/` reaches
`main` (workflow **worker**). It:
- receives each satellite message from RockBLOCK directly (in addition to
  the email), checks RockBLOCK's signature and the station's IMEI, keeps
  only what is published anyway (not the IMEI or the position) for 30 days,
  and starts a publish run;
- starts a publish run every 10 minutes;
- hands its mailbox to each publish run (with the read key).

**Secrets, and where they live.** Never put any of them in a file.

| Secret | Where | What |
|---|---|---|
| `STATION_IMEI` | Cloudflare: Worker → Settings → Variables and Secrets | the station's IMEI (RockBLOCK console) |
| `READ_KEY` | Cloudflare, same place | a long random string; also GitHub `MAILBOX_READ_KEY` |
| `GH_DISPATCH_TOKEN` | Cloudflare, same place | GitHub key that may start runs (below) |
| `MAILBOX_URL` | GitHub: Settings → Secrets and variables → Actions | the Worker's address, e.g. `https://powometer-intake.<name>.workers.dev` |
| `MAILBOX_READ_KEY` | GitHub, same place | the same string as `READ_KEY` |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | GitHub, same place | let the **worker** workflow deploy |

**Setting it up (once):**
1. Sign up at dash.cloudflare.com with the station's Gmail address (Free
   plan). Under Workers & Pages, pick the `workers.dev` name.
2. Storage & Databases → Workers KV → Create: name `powometer-mailbox`.
   Put its ID (not secret) into `worker/wrangler.toml` (`id = "…"`).
3. My Profile → API Tokens → Create Token → template "Edit Cloudflare
   Workers" → your account → Create. Store it in GitHub as
   `CLOUDFLARE_API_TOKEN`, and the Account ID (Workers & Pages overview,
   right-hand side) as `CLOUDFLARE_ACCOUNT_ID`.
4. Run the **worker** workflow (Actions tab → worker → Run workflow) or
   merge the change with the KV id: it deploys the Worker.
5. The GitHub key: github.com → your picture → Settings → Developer
   settings → Personal access tokens → Fine-grained tokens → Generate.
   Resource owner `mount-remo-backcountry-society`; only the `powOmeter`
   repository; permission **Actions: Read and write**, nothing else; the
   longest expiry allowed. The organization may have to approve it
   (organization Settings → Personal access tokens → Pending requests).
6. Make a read key on your own computer, e.g. in PowerShell
   `[guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()`.
7. In Cloudflare (Worker → Settings → Variables and Secrets → Add, type
   *Secret*): `STATION_IMEI`, `READ_KEY`, `GH_DISPATCH_TOKEN`.
8. In GitHub: `MAILBOX_URL` and `MAILBOX_READ_KEY`.
9. Check: within 10 minutes the Actions tab shows a publish run started by
   "workflow_dispatch", and the Worker's Observability tab (Cloudflare)
   shows no errors.
10. RockBLOCK (when decided): RockBLOCK console → Delivery Groups → add an
    **HTTP_JSON** destination `<Worker address>/rockblock` next to the email.
    Keep the email. Not HTTP_POST or HTTP_POST_INSECURE: they send no
    signature, so the Worker rejects every message (checked 2026-10-22).

**Checks and trouble:**
- *No runs every 10 minutes:* Cloudflare → Worker → Observability.
  "GH_DISPATCH_TOKEN is not set" or "HTTP 401" means the GitHub key is
  missing or expired.
- *"rejected" in Observability:* a message failed the signature or IMEI
  check; RockBLOCK shows it as "Invalid Response". RockBLOCK retries for
  about 6 days, and the email route still carries it. "no valid RockBLOCK
  signature" on every message: check that the destination is HTTP_JSON.
- *"mailbox FAILED" in a publish run:* the read key or address in GitHub
  does not match the Worker. Publishing continues from the sheet.
- The GitHub key belongs to the person who made it and expires: renew it
  (Yearly) and when handing over.

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

## The station page

<https://mount-remo-backcountry-society.github.io/powOmeter/>. The page's
files are in `site/`: `index.html` (text and layout), `app.js` (what it
calculates), `style.css` (colours), `terms.html` (terms of use).

- **What the colours mean:** no colour, the newest reading is under 6 hours
  old (normal: the station sends 7 times a day). **Amber:** 6 to 12 hours,
  probably only a delay. **Red, values struck through:** over 12 hours, the
  same limit as the "no message" alarm. A separate red message, "the data
  processing has stopped", means the publish workflow itself has not run
  for 3 hours.
- **"Updated 3 min ago · Refresh"** under the title is when this phone or
  computer last fetched the files, not the age of the readings. The page
  fetches them again every 10 minutes, whenever it comes back on screen (a
  phone app from the home screen has no reload button), and on **Refresh**,
  which also reloads the newest webcam images.
- **Times on the page** are BC time, a fixed UTC−7 (the constant
  `BC_OFFSET_H` in `app.js`). If BC ever changes its clocks again, change
  that one line. The data files stay in UTC.
- **Snow depth under 15 cm** shows as "Under 15 cm" on the main view, and
  the snow chart shades that band: over bare ground the readings scatter
  between about −14 and +7 cm, and thin snow squashes the heather, so
  shallower readings are not a depth yet. "More → Detailed readings" and
  the files keep the real value. The limit is `MIN_DEPTH_CM` in `app.js`;
  check it against a probe reading at the first real snowfall.
- **Depth change** (24, 48, 72 h, under the snow depth) compares the newest
  reading with the one nearest 24/48/72 h before it (within 3 h). It is a
  change in depth, not new snow (snow settles, wind moves it), and shows
  only when both are 15 cm or more; with old data it is hidden. Settings:
  `CHANGE_HOURS` and `CHANGE_TOL_H` in `app.js`.
- **Charts:** the buttons choose 24 h, 3, 7 or 30 days (3 by default,
  `DEFAULT_DAYS`; the browser remembers the choice). Touching or pointing at
  a chart shows the values in each chart's title line, with short station
  names ("Kasiks High"; `SHORT_NAMES` in `app.js`, add one for a new
  station), and the time on the time axis; a sideways drag moves through
  time, and the values clear 3 s after the finger lifts. The temperature
  chart draws 0 °C as a bold line.
- **Time axis:** up to 3 days, hours (24 h clock, as many as fit: every
  1, 3, 6 or 12 h) with the date in a row underneath; 7 days, only the
  dates (weekdays on a phone); 30 days, a date about every 7th day.
- **Changing a link** in the bar at the top (powWX, avalanche.ca): edit it
  in both `site/index.html` and `site/terms.html`.
- **Check the page's calculations:** open `selftest.html` on the published
  site; every line should say OK.
- **(computer) See the page in any state** (fresh, late, stale, storm,
  alarms, a faulty nearby station, …) with made-up data:
  `python tools/preview_site.py` lists the states;
  `python tools/preview_site.py storm` serves one at <http://localhost:8000>.
  `python tools/preview_site.py live` shows the files of your last build.

## Nearby stations

Kasiks High, Kasiks Low and Shames (bottom of the hill) belong to the BC
Ministry of Transportation & Infrastructure. They come from Avalanche
Canada's station API, which keeps only about 7 days, so a few days of
failed fetches lose nothing.

- **Switched on only with the Ministry's written permission on file**
  (CLAUDE.md rule 3). They are on: the record is
  `docs/permissions/moti-2026-10.md`. Its conditions (credit the Ministry,
  say it is not liable) are met by the `licence` text in
  `config/stations.yaml`; keep both if you change it. The Ministry asked that
  its reply not be given to AI tools: never paste it into an assistant.
  For another station: save a record of the permission in
  `docs/permissions/` (no names or email addresses of people), then in
  `config/stations.yaml` set `permission:` to that file, `licence:` to the
  permission's terms, and `enabled:` and `publish:` to true. `validate`
  refuses without the file.
- **A faulty sensor** (like Kasiks High's temperature since 2026-09-25) is
  labelled `suspect` automatically when it disagrees with every other
  station, and left out of the page's chart. To mark a period by hand, add
  a `delete` correction for that station (see "Adding a correction").
- **A new kind of reading appears** (e.g. snow height in winter): the
  station's new field names are listed in `status.json` under
  `info` → `nearby_unmapped_fields`. An AI assistant can add them to
  `powometer/decoders/avalanche_canada.py`.
- **If a station stops:** its time in `status.json` → `nearby` stops
  moving. Nothing to do on our side unless Avalanche Canada changed its
  API; the fetch prints the error in the workflow log.

## Webcams

Shames Mountain's two webcams (chairlift, parking lot), set up in
`config/webcams.yaml`. Like the nearby stations, they are **switched on
only with Shames Mountain's written permission on file**
(`enabled: true`, `permission:` naming the file in `docs/permissions/`).
They are on: the record is `docs/permissions/shames-webcams-2026-10.md`.
Its conditions (credit Shames Mountain, say it is not liable) are met by
`credit` in `config/webcams.yaml`. As with the Ministry, never paste Shames
Mountain's reply into an AI assistant.

- The page shows each camera's newest image, loaded from Shames Mountain's
  own address, and a **3-day time lapse**: one daylight frame an hour
  (night frames are left out), shrunk to 960 pixels. Visitors play it,
  drag the slider, or step one image back or forward with ‹ and ›.
- The frames are kept **in the published site only, never in git** (git
  keeps every file forever: about 3 GB a season). Between runs they
  are kept in the workflow's cache; if that is ever lost, the next run takes
  them back from the live site, and if that fails too, the time lapse
  simply starts again.
- A camera that is off for the season serves the same image for weeks; it
  is added once and drops out after 3 days.
- When a camera has sent no new image for 24 hours (longer than a night),
  the page says "No new image since …: the camera may be off" under its
  name. The cameras belong to Shames Mountain; tell them if it lasts.
- If Shames Mountain changes the image addresses, update the `url:` lines.
- Nothing about the webcams can stop the publishing.

## Monthly

1. Open the published page, "More → Station health", and check that
   "Files built" is recent.
2. Edit `KEEPALIVE.md` in the GitHub web interface: put today's date, and
   commit. GitHub silently switches off hourly workflows in a repository
   with no activity for 60 days; this prevents it.

## Yearly
Renew the GitHub key `GH_DISPATCH_TOKEN` before it expires (GitHub emails a
week ahead): make a new one as in "The intake Worker", step 5, and replace the
Cloudflare secret.
*(phase 2)* Sign in to the station's Gmail account; upgrade day for software
versions (`requirements.in` → `requirements.txt`); archive the repository to
Zenodo or an MRBS drive.
