# POW-O-METER — improvement list

Status as of 20 Sep 2026. Ordered by priority. Part 1 is built; the rest is not.

---

## Part 1 — done, in `scripts/POW_O_METER_v1_3.ino`

New files: `POW_O_METER_v1_3.ino`, `SET_RTC.ino`, `FIELD_CARD.md`.

**The radio payload format is unchanged**, so the Google Apps Script needs no
simultaneous change. One moving part at a time.

| # | Change | Why it matters |
|---|---|---|
| 1 | **Era-agnostic time decode.** `AT-MSSTM` read directly; ticks decoded against a table of known epochs; the build date is the lower bound so only one candidate can survive. | The actual cause of the 2026 failure. Also makes ERA4 a one-line edit instead of a redesign. |
| 2 | **Raw MSSTM hex logged on every sync.** | January would have been a ten-minute diagnosis instead of a five-month one. Any future era change is recoverable from one SD download. |
| 3 | **Two-strike validation gate.** A >24 h correction is held until a second reading confirms the modem advanced by the same amount the RTC did. | Stops a bad read corrupting a good clock *and* stops a good read being rejected forever. The v1.2 draft would have locked out permanently on first boot. |
| 4 | **No out-of-range `DateTime` anywhere.** All date maths via `unixtime`/`TimeSpan`. | The Jan-23 patch wrote impossible values (hour 26, day 32) into the RTC on **15.2%** of syncs. |
| 5 | **RTC health.** `begin()` retried 3×; `rtc.start()` clears the PCF8523 STOP bit; a clock that has not advanced since the last boot is flagged untrusted. | `begin()` failed 1,471 times in Feb 2025 and the clock froze for days. Neither was detected. |
| 6 | **Sync-only modem session.** When the clock is untrusted the station opens a session that reads network time without sending a message — **no Iridium credits**. | Breaks the deadlock: a bad clock is repaired *by* the modem, so refusing to power it up would make the fault permanent. |
| 7 | **Ultrasonic filter enabled.** `triggerPin` held HIGH for 7.5 s (the MB7374 filter needs 40 readings to initialise), then 5 samples at 800 ms. | The snow-optimised filter you paid for has never run. 8.7% of bursts currently disagree by >100 mm, 4.5% by >500 mm. |
| 8 | **Median transmitted, not minimum.** | The minimum is the statistic most sensitive to spurious short echoes; it sits ~69 mm below the burst median, biasing snow depth ~7 cm high. |
| 9 | **`sprintf` buffer overflow fixed**, and all payload fields clamped. | `"%+04d"` into a `char[4]` overflowed by one byte on **every** message. A `pulseIn` timeout spike used to emit a 6-character field that both overflowed the buffer and made the message unparseable. |
| 10 | **Modem retry actually retries** (`modem.sleep()` before the second `begin()`). | All 21 failures in 2026 were a 300 s timeout followed by an instant `ISBD_IS_ASLEEP` — there was effectively no retry. The first v1.3 draft toggled the sleep pin by hand, which IridiumSBD's internal state cannot see, so its retry was dead code too; corrected 2026-09-24 after an independent review. |
| 11 | **Pointer-arithmetic log bugs fixed**; dead delays removed (2 s serial wait, ~3.2 s per-cycle status blink). | Undefined behaviour, and the removed delays roughly pay for the ultrasonic warm-up. |
| 12 | **Boot self-test block** printed over serial. | You are flashing in the field with no way to troubleshoot. This gives a go/no-go in 30 seconds. |
| 13 | **`prepMsg()` null/empty guards.** | If `/TRANSMIT.csv` is missing or truncated, the old code evaluated `out_batt_v[num_rows - 1]` = `NULL[-1]`. On a SAMD21 that is a **hard fault with the TPL5110 still holding power on** — the station hangs and flattens the battery. Reachable any time an SD write fails on a transmission hour. |
| 14 | **Ultrasonic readings range-validated** (300–5000 µs). | A garbage value of **1.5e20 mm** is present in the real 2026 record. Out-of-range readings are now discarded instead of averaged in. |
| 15 | **Dead SHT31 reports −99.9 °C, not 0.0 °C.** | `String(NaN).toFloat()` silently became 0.0, so a failed sensor read as 0 °C and 100 % RH — both entirely plausible at a snow station in winter. The fault could hide for weeks. |
| 16 | **No-target sentinel kept at 499 cm.** | Caught by the stress test: the fallback had to round to 499, not 500, or the Apps Script would stop recognising "no reading". Real no-target values measure 4986–4992 mm. |
| 17 | **QuickStats dependency removed.** | `sampleUltrasonic()` sorts its own 5-element array, so an AVR-only library is no longer needed on an ARM board. |

### How this was verified

**It compiles.** A self-contained `arduino-cli` toolchain was set up and both
sketches build clean for the real target, `adafruit:samd:adafruit_feather_m0`
(Adafruit SAMD core 1.7.17):

| Sketch | Flash | RAM free |
|---|---|---|
| `POW_O_METER_v1_3.ino` | 77,864 B — **29%** of 262,144 | ~27.7 KB |
| `SET_RTC.ino` | 43,624 B — 16% | ~28.1 KB |

With `--warnings all` the only remaining sketch-level warning is a pre-existing
unused `timestamp` parameter in `sendMsg()`, carried over from v1.1. The RAM
figure matches the ~25,631 bytes `freeMemory()` reported in the real field logs,
which is a useful independent sanity check.

Beyond the compile, the logic was validated against the real record rather than
by inspection:

- **Clock logic replayed against all 1,026 real sync events** from the June 2026
  `LOG.CSV`, under eight starting conditions (correct clock, 2 days behind,
  2 days ahead, 5 months stale, untrusted, and with 1-in-7 / 1-in-3 / every-other
  modem read corrupted). Zero wrong decisions in every run; garbage was never
  accepted; a wrong clock was always repaired.
- **`prepMsg()` compared byte-for-byte against 3,052 real transmissions.** Output
  identical to the old code on every one, so the wire format is provably unchanged.
  Modelling the device's float32 semantics reproduces all 1,100 logged messages
  exactly, including the battery-digit quirk in Part 5.
- **Full state machine simulated** over 2.1 days per scenario for every fault seen
  in the logs. All recoverable faults converge, data keeps flowing throughout, and
  the transmission rate stays inside the 7/day budget.
- **Fault injection** across sensor, battery, SD and payload failures — see below.
- **Structural lint** plus a consistency check that re-reads the shipped `.ino` and
  confirms every constant, guard and buffer size still matches the model that was
  actually replayed against the data.

### Fault injection results

Every case below was exercised against the v1.3 logic. In all of them the station
survives, keeps logging, and emits a payload the Apps Script regex still parses.

| Fault injected | Behaviour |
|---|---|
| SHT31 dead (NaN) | temp field `-999`, logged as INVALID. Old code sent `+000`, indistinguishable from 0 °C |
| BMP390 dead | `0,0` to `DATA.csv` only; never reaches the radio |
| Ultrasonic: all timeouts | reports 4990 mm → payload `499` = the sheet's "no reading" |
| Ultrasonic: one spurious short echo | median rejects it. **Old code transmitted it**, because it sent the minimum |
| Ultrasonic: `pulseIn` spike of 1e6 | discarded as out of range |
| Ultrasonic: floating pin (pure garbage) | all five rejected → 4990 mm |
| Battery 3.45 V / 3.30 V / 0.00 V | charge digit 1 / 0 / 0; payload stays fixed-width |
| SD dead at boot | `tracking()` returns −1, transmit block never entered |
| `TRANSMIT.csv` missing on a transmit hour | guard returns empty, transmission skipped. **Old code: hard fault** |
| `TRANSMIT.csv` header-only or columns missing | same guard |
| `TRANSMIT.csv` timestamp truncated | second guard catches it |
| Everything failing at once | payload still parseable, every field an obvious sentinel |

**Still not verified:** nothing has run on real hardware. The compile proves the
code builds and links for the Feather M0; it cannot prove the MB7374 filter
behaves as its datasheet says, or that the modem retry recovers a real timeout.
Those are the two things worth checking in the first SD download.

---

## Part 2 — deliberately deferred

These were in the original plan and were dropped once it became clear there is no
bench test and no troubleshooting in the field. Each adds a new runtime failure
mode for a benefit that can wait for the next trip.

| Deferred | Why |
|---|---|
| Reading the era table and transmission hours from `/CONFIG.CSV` | An untested SD parser at a remote station is real risk, and ERA4 is a 2036 problem. The epoch table is a one-line edit in the sketch. |
| Changing transmission scheduling to "hour crossed" rather than "hour matched" | The current scheme demonstrably works (1,079 sends) and the 5-reading fallback already covers skipped hours. Changing it alters cost and coverage with no way to test. |
| Adding a month field to the payload | A coordinated two-sided change with the Apps Script. Should not ride along with a field trip. |
| Binary payload packing to halve credit cost | Worth doing — bundles of 5 readings cross the 50-byte segment boundary and cost two credits — but it is a payload change. |

---

## Part 3 — Google Apps Script (do this soon; it does not need a field trip)

**Written 22 Sep 2026, not yet deployed:** `scripts/Google Apps script v2.js`
fixes items 1–5 below and the stuck-ingest bug, and adds a `Messages` sheet
with a station-clock check. It was tested offline against the 123 real email
bodies in the Errors log and the full exported Data sheet. The parser matches
the old one on all 6,686 historical readings, and ingest is idempotent under
re-delivery. `dedupeDataSheet()` removes 8,487 duplicate rows and keeps 6,667.

**The pipeline is dead, not just untidy.** Ingest has been stuck since
2026-05-14, re-reading one unparseable notification (MOMSN 2941,
`Session Status: 13`, `No Data`) **37,044 times**. The `Data` sheet has not grown
since 2026-05-28, so the transmissions between then and the June shutdown were
never ingested — they are recoverable from the SD archive or the RockBLOCK
message history. The 5-minute trigger is still firing today.

Likely mechanism: on an unparseable message the handler marks
`getMessages()[0]` read and returns *without archiving the thread*, so a thread
holding any other unread message keeps matching `is:unread`.

Also to fix:

1. **`isDuplicate()` has never worked.** It compares a UTC-formatted measurement
   time against column A, which holds transmit time in PST. Consequence:
   **8,473 copies of one transmission make up 56% of the `Data` sheet.**
2. **The "(PST)" columns are not PST.** They are `America/Los_Angeles`, which
   applies DST, while the firmware writes fixed UTC−8. One hour off for roughly
   half the record.
3. `logProcessingError()` logs normal progress on every run — the Errors sheet is
   ~187k rows and 35 MB of XML.
4. `resampleData()` rewrites the whole Resampled sheet on every email; O(n²) over
   a season, heading for the 6-minute execution limit.
5. `getLatestFieldReference()` reads only the last `Field` row, so re-running the
   script would silently rewrite history with a different sensor height.

---

## Part 4 — data and archive

1. **Recover the ~656 mislabelled rows** at the head of `DATA.csv`. They are *not*
   bench-test artifacts as the handoff says: 2015-04-12 + 3932 d = **2026-01-16**,
   a real site visit. That is live station data mislabelled by the epoch bug.
2. **Check the Aquarius timezone.** The day-level errors are already corrected
   (a lag scan against the transmit-time series gives exactly 0 in every week of
   Feb–May 2026), but the `ISO 8601 UTC` column appears to hold **PST**: diurnal
   fits put the daily temperature maximum at 13–15 h in that column, and Shames'
   solar noon is 20:36 UTC.
3. **Sensor height reference looks ~0.54 m too small** — snow depth sits on a
   −0.54 m floor at bare ground. Re-measure on this trip.
4. **Min/max naming is inverted.** `Max_snow_depth` has more negative values than
   `Min_snow_depth` (31% vs 20.5%), because `maxDistance` yields the *minimum*
   depth.

---

## Part 5 — known, minor, left alone

- **Battery digit is one bucket low at exact thresholds.** `strtof("3.80")`
  gives a `float` marginally below the `double` literal `3.8`, so the comparison
  fails. Verified: modelling the device's float32 semantics reproduces all 1,100
  logged messages exactly. Cosmetic; changing it would shift the meaning of a
  field that already has five seasons of history.
- **72 "Power down failed" events** — the TPL5110 did not cut power and `loop()`
  ran again. v1.3 makes a second attempt before giving up. This is the likely
  source of the sub-60-second duplicate rows in the Aquarius record.
