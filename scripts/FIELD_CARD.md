# POW-O-METER — field card

Print this. One page of things to do at the station, in order, plus what "good"
looks like.

---

## Before you leave the house

1. **Compile both sketches** (Verify) in your IDE. They have already been
   compiled clean here for `adafruit:samd:adafruit_feather_m0` — v1.3 uses
   **29% of flash** and leaves ~27 KB of RAM free — but your library versions
   may differ from the ones below, so check before you drive out.

   Known-good versions used for that build:

   | Library | Version |
   |---|---|
   | Adafruit SAMD core | 1.7.17 |
   | RTClib | 2.1.4 |
   | Adafruit SHT31 Library | 2.2.2 |
   | Adafruit BMP3XX Library | 2.1.6 |
   | Adafruit BusIO | 1.17.4 |
   | SD | 1.3.0 |
   | CSV Parser | 1.4.1 |
   | IridiumSBD (mikalhart) | 2.0 |
   | MemoryFree | any ARM-capable version (`sbrk`-based, not the AVR-only one) |

   **QuickStats is no longer needed** — v1.3 sorts its own array, so that
   AVR-only library has been dropped.

   If the build fails, the likeliest cause is an old **RTClib** missing
   `rtc.start()`, `rtc.lostPower()` or `rtc.initialized()`. Update RTClib, or
   comment out those three lines (only `start()` actually matters).

2. Open `SET_RTC.ino` and set **`UTC_OFFSET_HOURS`** = how many hours to ADD to
   your laptop's clock to get UTC. **BC is UTC−7 year round, so use 7.** Don't
   reason about "PST" or "PDT" — just use your laptop's actual offset, then
   verify (step 4). Compile it too.
3. Pack: laptop + USB cable, **two new CR1220 coin cells**, a spare SD card
   (FAT32, 4–8 GB), tape measure, multimeter.

---

## At the station, in this order

**1. Pull the old SD card first.** Copy `DATA.CSV`, `LOG.CSV`, `TRACKING.CSV`
off it before touching anything. That is the record of what the station has
been doing since June.

**2. Replace the CR1220 coin cell.** It has been in since at least early 2025.
A weak cell is the most likely common cause of both the `rtc.begin()` failures
and the frozen-clock episodes in Feb 2025.

**3. Insert the SD card** (new one, or the old one after copying).

**4. Flash `SET_RTC.ino`.** Press Upload directly — not Verify then Upload —
because the time is captured at compile. Open the serial monitor at
**115200 baud** and check:

```
RTC (UTC): 2026-11-05T20:14:32   |   station clock (UTC-8): 2026-11-05T12:14:32
```

**Check the `RTC (UTC)` figure against time.is/UTC** (or a phone set to UTC).
That is the one that matters — if UTC is right, the station is right, whatever
offset you entered. Don't check it against local time.

The `station clock` column is fixed UTC−8 by design and does not shift. With BC
on UTC−7 it reads **one hour behind local civil time, all year**. That is
intentional — it keeps 17 months of existing record continuous.

**5. Flash `POW_O_METER_v1_3.ino`.** Watch the self-test block:

```
=== POW-O-METER v1.3 ===
--- self test ---
build   : Nov  5 2026 11:58:03
SD      : OK
RTC     : OK  trusted=YES  UTC=2026-11-05T20:14:47  PST=2026-11-05T12:14:47
SHT31 T : 1.42
BMP390 P: 884.31
battery : 3.86
-----------------
```

**Everything must read OK and `trusted=YES`.** If not, see the table below.

**6. Test that the coin cell holds the time.** Do this *before* reconnecting
the modem, so the station cannot quietly re-sync the clock during the test.
Step 5 does not prove the cell works: with USB attached, the RTC is powered
from USB the whole time.

   a. Note where the RTC board's **VIN** wire goes: Feather 3V/USB pin (after
      the TPL5110, so switched off every cycle) or straight to the battery /
      charge controller (always on). Write it down.
   b. Remove **all** power: USB, **and** the supply into the TPL5110. In
      daylight the solar panel can power the board through the charge
      controller, so disconnect at the TPL5110 input, not just the battery.
   c. Wait **at least 1 minute**. The board's capacitors can keep the RTC
      ticking for several seconds.
   d. Reconnect power, plug in USB, open the serial monitor at 115200, and press
      the Feather's **reset** button. Reset reboots the processor without
      cutting power to the RTC.
   e. Read the `RTC` line:
      - `trusted=YES` and UTC still right → **the cell is good.**
      - `trusted=NO`, or a year of 2000 → **the cell is not holding.** See
        "Coin cell fails the test" below.

**7. Watch one full loop** (about 25 seconds). You should see a `Data:` line
with plausible numbers, then `Done loop ...`. The ultrasonic reading takes ~11 s
now — that pause is the MB7374's snow filter initialising, and it is expected.

**8. Reconnect the modem's negative terminal.**

**9. Measure and write down:**
   - sensor height above **ground** (m)
   - sensor height above **snow** (m)
   - time a few TPL5110 cycles with your watch, so we know the real period

Add a row to the `Field` sheet when you get home. Measure carefully — the
archive shows snow depth sitting on a −0.54 m floor at bare ground, which
suggests the current reference is about half a metre too small.

---

## If the self-test is not clean

| Symptom | What it means | Do this |
|---|---|---|
| `SD : FAIL` | card not readable | Try the spare card, formatted FAT32 |
| `RTC : FAIL` | I2C not responding | Re-seat the coin cell and the RTC wiring, power-cycle, retry |
| `trusted=NO` in step 5 | clock is out but the RTC works | Re-run `SET_RTC` once. If it persists, **fine to leave**: it repairs itself within about 2 hours (see below) |
| `trusted=NO` after the step 6 test | coin cell not holding the time | See "Coin cell fails the test" below |
| `SHT31 T : nan` | temp/humidity sensor down | Station still runs; note it and carry on |
| `BMP390 P : 0` | pressure sensor down | Station still runs; note it and carry on |

**LED blink codes** (they repeat once at boot): 2 = SHT31, 3 = BMP390,
4 = RTC, 5 = SD.

### Coin cell fails the test

1. Measure the cell at the RTC board's battery pin. A fresh CR1220 reads
   about 3 V. Re-seat it, then try the second cell.
2. Re-run `SET_RTC`, then `POW_O_METER_v1_3`, then the step 6 test. A cell fitted
   while the station was unpowered leaves the RTC's battery backup **disabled**
   until something sets the time, and `SET_RTC` enables it. The test fails if
   it runs before `SET_RTC`, even with a good cell.
3. Still failing: the holder or the RTC board is at fault. **Deploy anyway.**
   What that means depends on the VIN wiring from step 6a:
   - **RTC always powered:** almost no effect. The time is only lost when the
     main battery is disconnected.
   - **RTC switched by the TPL5110:** the clock resets to 2000-01-01 on
     *every* wake-up and can never hold. The station still runs safely:
     - Radio data keeps flowing, about 4 messages a day on the 5-reading
       trigger. The Google Sheet times readings from the email's transmit
       time, so the sheet is unaffected.
     - `DATA.CSV` timestamps all read about 1999-12-31 16:00. The rows stay in
       order, and each clock sync logs the correct time in `LOG.CSV` about every
       67 min, so the record can be rebuilt afterwards.
     - **Cost: battery.** On every 67-minute cycle without a send, the station
       opens a sync-only modem session. That's about 17 a day, each ~15 s, no
       credits. Modem power-ups go from ~7 a day to ~21.
     - From home, the `Messages` sheet shows large, erratic values in
       "Station Clock Offset" instead of staying within about ±30 min.

---

## What happens in the first two hours, unattended

- Measurements every ~17 min, written to `DATA.CSV` regardless of anything else.
- Every 4th wake-up (~67 min) a reading is queued for the radio. It goes out at
  the scheduled hours, or after 5 readings have piled up.
- If the clock is not trusted, the station additionally opens a **sync-only**
  modem session — no message, **no Iridium credits** — purely to repair the clock.
- A clock error larger than 24 h is **deliberately not applied on the first
  reading**. It is held, and applied once a second reading confirms it. That is
  by design — do not wait around for it.

Recovery times measured in simulation, across every fault state that appears in
the real logs:

| Fault | Clock correct after |
|---|---|
| Clock reset once, e.g. power lost during a cell swap (year 2000) | ~0.8 h |
| 5-month-stale clock | ~0.8 h |
| Clock 2 days out (today's state) | ~5.3 h |
| Stalled oscillator | ~5.3 h |
| `rtc.begin()` failing for 10 cycles | ~10.9 h |

In every one of those, **data keeps reaching the radio the whole time** and the
transmission rate stays inside the normal 7/day budget. Even with the RTC
permanently dead the station keeps transmitting on the queue-depth trigger
(~4/day), so nothing is lost except timestamp accuracy in an unused field.

These times are for a clock that is wrong **once**. A coin cell that stays dead
is different: if the RTC is switched by the TPL5110, the time is lost again on
every wake-up and never holds. See "Coin cell fails the test" above.

---

## Do not wait for a transmission

Transmit windows are 01, 06, 08, 10, 15, 20, 22 PST, so you would likely be
waiting hours. The radio path is unchanged from the version that made 1,079
successful sends, and the clock read happens *after* the message is sent, so a
clock problem cannot stop a transmission. Check the RockBLOCK console or the
Gmail account once you are home.

The Google Sheet pipeline was fixed on 2026-09-22. Each RockBLOCK email now gets
a row in the `Messages` sheet. The "Station Clock Offset" column should settle
within about ±30 min once the clock has synced.

---

## Next SD download — what to look for

In `LOG.CSV`, every clock sync now records the raw modem value:

```
 - CLOCK: MSSTM=0x1a2b3c4d ticks=439041101 era=ERA3 -> 2026-11-05T20:14:47Z
 - CLOCK: applied -3 s -> 2026-11-05T20:14:47
```

Good signs: `era=ERA3` every time, small `applied` values, no `HELD pending`
lines after the first hour, no `impossible` timestamps anywhere.

If every boot logs `RTC present but NOT trusted (lostPower=1 ...)`, the coin
cell is not holding the time (see "Coin cell fails the test").

If you ever see `matches NO KNOWN ERA`, Iridium has changed epoch again. The
raw hex in that line plus the true UTC of the message is all that is needed to
compute the new epoch — one line added to the era table in the sketch fixes it.
