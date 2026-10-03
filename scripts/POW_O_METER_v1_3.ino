/* ===========================================================================
 * POW-O-METER v1.3  --  Shames Mountain snow station, 2026-27 season
 * ---------------------------------------------------------------------------
 * WHY THIS VERSION EXISTS
 *
 * Iridium switched the network from ERA2 to ERA3 on 14 Jan 2026 18:08 UTC.
 * IridiumSBD::getSystemTime() hardcodes the ERA2 epoch, so from that moment it
 * returned a time exactly 3932 days 03:50:22 in the past.  The Jan-23 field
 * patch (+11y -3mo +4d +4h -9min) was a calendar-unit approximation of that
 * fixed duration, so it stepped at month boundaries: 0 -> +24h (4 Feb) ->
 * -48h (7 Mar) -> -24h (6 Apr) -> -48h (7 May).  It also wrote impossible
 * values into the RTC (hour 26, day 32) on 15.2% of syncs.
 *
 * CHANGES FROM v1.2 / v1_2_ERA3_fix
 *   1. Era-agnostic time decode.  AT-MSSTM is read directly and decoded
 *      against a table of known epochs; the build date is the lower bound, so
 *      only one candidate can ever survive.  The raw hex is always logged.
 *   2. Two-strike validation gate.  A correction larger than 24 h is held
 *      until a second, independently consistent reading confirms it.  The
 *      station can never lock itself out of a correct time.
 *   3. All date arithmetic goes through unixtime / TimeSpan.  There is no
 *      DateTime(y, m+a, d+b, ...) anywhere in this file.
 *   4. RTC health: retry begin(), call start() (the PCF8523 STOP bit survives
 *      a battery event), detect a stalled clock across boots.
 *   5. Ultrasonic: the MB7374's snow filter is allowed to initialise (it needs
 *      ~7 s of RX held high) and the MEDIAN is transmitted, not the minimum.
 *   6. sprintf buffer overflow in prepMsg() fixed; all payload fields clamped
 *      so the wire format stays fixed-width whatever the sensor reports.
 *   7. Modem retry actually retries: modem.sleep() before the second
 *      begin(), so IridiumSBD's internal state really power-cycles.
 *
 * THE RADIO PAYLOAD FORMAT IS UNCHANGED.  DDHHMMB + DDD(+/-)TTTHH, colon
 * separated.  The Google Apps Script needs no simultaneous change.
 *
 * FIELD NOTE: on first boot the serial monitor prints a self-test block.
 * Read it before you walk away.  See FIELD_CARD.md.
 * =========================================================================== */

  #define ISBD_SUCCESS             0
  #define ISBD_ALREADY_AWAKE       1
  #define ISBD_SERIAL_FAILURE      2
  #define ISBD_PROTOCOL_ERROR      3
  #define ISBD_CANCELLED           4
  #define ISBD_NO_MODEM_DETECTED   5
  #define ISBD_SBDIX_FATAL_ERROR   6
  #define ISBD_SENDRECEIVE_TIMEOUT 7
  #define ISBD_RX_OVERFLOW         8
  #define ISBD_REENTRANT           9
  #define ISBD_IS_ASLEEP           10
  #define ISBD_NO_SLEEP_PIN        11
  #define ISBD_NO_NETWORK          12
  #define ISBD_MSG_TOO_LONG        13

  /*Include the libraries we need*/
  #include <Arduino.h>
  #include <Wire.h>
  #include "Adafruit_SHT31.h"
  #include "Adafruit_BMP3XX.h" // BMP390 Library
  #include <time.h>
  #include "RTClib.h"           //Needed for communication with Real Time Clock
  #include <SPI.h>              //Needed for working with SD card
  #include <SD.h>               //Needed for working with SD card
  #include <IridiumSBD.h>       //Needed for communication with IRIDIUM modem
  #include <CSV_Parser.h>       //Needed for parsing CSV data
  // QuickStats removed in v1.3: sampleUltrasonic() now sorts its own 5-element
  // array, so the AVR-only library is no longer a dependency.
  #include <MemoryFree.h>

  /* Pin Definitions */
  const byte chipSelect = 4;   // Chip select pin for SD card
  const byte led = 8;          // Built in led pin
  const byte vbatPin = 9;      // Batt pin
  const byte triggerPin = 10;  // Range start / stop pin for MaxBotix MB7374 ultrasonic ranger
  const byte pulsePin = 12;    // Pulse width pin for reading pw from MaxBotix MB7374 ultrasonic ranger
  const byte IridSlpPin = 13;  // Sleep control pin for Iridium modem
  const byte donePin = 5;      // Done pin - This is for the TPL5110. Must go LOW then HIGH.

  /* Transmission Times */
  const uint8_t transmissionHours[] = { 1, 6, 8, 10, 15, 20, 22 };
  const int numTransmissionTimes = sizeof(transmissionHours) / sizeof(transmissionHours[0]);

  /* ---------------------------------------------------------------------------
   * Iridium system-time epochs.  AT-MSSTM returns a bare 32-bit count of 90 ms
   * frames since the start of the current era -- it does NOT say which era it
   * is in, so the epoch is an assumption we have to supply.
   *
   * Source: https://docs.groundcontrol.com/iot/rockblock/user-manual/iridium-time
   *
   * WHEN ERA4 ARRIVES (hard deadline 2037-05-16, when the ERA3 counter reaches
   * 2^32; historically Iridium switches a few months early) add one line here
   * with the new epoch as a UNIX timestamp and reflash.  If you are reading a
   * log where the clock stopped syncing, the log records the raw hex, and the
   * new epoch is simply:   epoch = true_utc_unixtime - ticks * 0.09 s
   * ------------------------------------------------------------------------- */
  struct IridiumEra { const char *name; uint32_t epoch; };
  const IridiumEra IRIDIUM_ERAS[] = {
    { "ERA1", 1173325821UL },   // 2007-03-08 03:50:21 UTC
    { "ERA2", 1399818235UL },   // 2014-05-11 14:23:55 UTC
    { "ERA3", 1739556857UL }    // 2025-02-14 18:14:17 UTC
  };
  const uint8_t NUM_ERAS = sizeof(IRIDIUM_ERAS) / sizeof(IRIDIUM_ERAS[0]);

  /* Plausible-time window, anchored on the build date.  The station cannot be
   * running before the firmware that runs it was compiled, which is what lets
   * era selection work even when the RTC is garbage. */
  const uint32_t ERA_WINDOW_BACK = 86400UL;                        // 1 day of build-clock slack
  const uint32_t ERA_WINDOW_FWD  = 12UL * 365UL * 86400UL + 3UL * 86400UL;  // ~12 years of service

  /* Clock-sync gates */
  const uint32_t CLOCK_NEAR_GATE      = 86400UL;     // apply silently inside 24 h
  const int32_t  CLOCK_CONSIST_GATE   = 300;         // two proposals must agree to 5 min
  const uint32_t CLOCK_PENDING_MAXAGE = 48UL * 3600UL;

  /* Ultrasonic timing -- see MB7374 datasheet.  The snow filter "will initialize
   * 40 readings (about 7 seconds) after ... the RX pin is brought high and held
   * high", and the filtered free-run output updates at 1.33 Hz. */
  const uint16_t US_WARMUP_MS        = 7500;
  const uint16_t US_SAMPLE_GAP_MS    = 800;
  const uint8_t  US_SAMPLES          = 5;
  const uint32_t US_PULSE_TIMEOUT_US = 1000000UL;    // explicit; never block forever
  // No-target fallback. MUST round to 499 cm: that is the sensor's own
  // no-return value (measured 4986-4992 mm across the 2026 record) and the
  // Apps Script treats 4.99 m as "no reading". 4999 would round to 500 and
  // silently stop being recognised as a null.
  const float    US_MAX_RANGE_MM     = 4990.0;
  // Physically possible pulse widths (1 us per mm). The datasheet says objects
  // closer than 50 cm read as 50 cm and the ceiling is 5 m, so anything outside
  // this band is electrical noise or a floating pin, not a distance.
  const unsigned long US_MIN_VALID_US = 300UL;
  const unsigned long US_MAX_VALID_US = 5000UL;

  /* State files on the SD card */
  const char *PENDING_FILE  = "/CLKSYNC.CSV";
  const char *LASTBOOT_FILE = "/LASTBOOT.CSV";

  /* Global state */
  bool sdOk = false;
  bool rtcOk = false;
  bool rtcTrusted = false;
  uint32_t BUILD_UNIX = 0;
  DateTime default_time(2000, 1, 1, 0, 0, 0);

  /*Define Iridium serial communication as Serial1 */
  #define IridiumSerial Serial1
  IridiumSBD modem(IridiumSerial, IridSlpPin);

  RTC_PCF8523 rtc;
  Adafruit_SHT31 sht31 = Adafruit_SHT31();
  Adafruit_BMP3XX bmp;

  String myHeader = "datetime,batt_v,memory,snow_depth_min_mm,snow_depth_max_mm,snow_depth_median_mm,air_2m_temp_deg_c,air_2m_temp_rh_prct,baro_temp_deg_c,baro_pressure_hPa";
  String transmitHeader = "datetime,batt_v,snow_depth_mm,air_2m_temp_deg_c,air_2m_temp_rh_prct";

  /* Function Prototypes */
  void initializeSensors();
  void initClock();
  void selfTest();
  void takeMeasurement(float &battVoltage, int &memory, float &minDistance, float &maxDistance,
                       float &medianDistance, float &airTemp, float &airHumidity,
                       float &baroTemp, float &baroPressure);
  int  tracking();
  String prepMsg();
  void writeToCSV(String header, String data, String filename);
  void logMessage(const String &message);
  int  sendMsg(String msg, DateTime timestamp);
  void syncOnly();
  void syncClock();
  bool readMsstmTicks(uint32_t &ticks, String &rawHex);
  bool selectEra(uint32_t ticks, uint32_t rtcUnix, bool trusted, uint32_t &outUnix, const char *&outName);
  bool loadPending(uint32_t &pCand, uint32_t &pRtc);
  void savePending(uint32_t pCand, uint32_t pRtc);
  void clearPending();
  bool readLastBoot(uint32_t &prev);
  void writeLastBoot(uint32_t nowU);
  void sampleUltrasonic(float &minDistance, float &maxDistance, float &medianDistance);
  String sampleSHT();
  String sampleBMP();
  float sampleBatteryVoltage();
  void powerDown();
  void blinky(int16_t n, int16_t high_ms, int16_t low_ms, int16_t btw_ms);
  int  freeMemory();

  /* =========================================================================== */
  /* ========== SETUP ========================================================== */
  /* =========================================================================== */

  void setup(void) {
    Serial.begin(115200);
    // Bounded wait for a serial monitor. In the field nothing is attached, so
    // this costs 1.5 s instead of the unconditional 2 s delay v1.2 used.
    unsigned long t0 = millis();
    while (!Serial && (millis() - t0) < 1500) { }
    Serial.println(F("=== POW-O-METER v1.3 ==="));

    pinMode(led, OUTPUT);        digitalWrite(led, LOW);
    pinMode(triggerPin, OUTPUT); digitalWrite(triggerPin, LOW);
    pinMode(pulsePin, INPUT);
    pinMode(donePin, OUTPUT);    digitalWrite(donePin, LOW);
    pinMode(IridSlpPin, OUTPUT); digitalWrite(IridSlpPin, LOW);

    BUILD_UNIX = DateTime(F(__DATE__), F(__TIME__)).unixtime();

    modem.sleep();   // unchanged from v1.1/v1.2 -- proven harmless in the field

    sdOk = SD.begin(chipSelect);
    if (!sdOk) {
      Serial.println(F("SD Initialization Failed"));
      Serial.flush();
      blinky(5, 100, 100, 1000);
      powerDown();
    }

    initializeSensors();
    initClock();
    selfTest();
  }

  /* =========================================================================== */
  /* ========== MAIN LOOP ====================================================== */
  /* =========================================================================== */

  void loop(void) {
    // Read time and convert UTC to PST (UTC-8, year round, by design)
    // Copy-initialised rather than default-constructed then assigned: RTClib's
    // DateTime has no user-defined operator=, so assignment raises a
    // -Wdeprecated-copy warning.
    DateTime timestamp = rtcOk ? (rtc.now() - TimeSpan(8 * 3600)) : default_time;

    float battVoltage, minDistance, maxDistance, medianDistance;
    float airTemp, airHumidity, baroTemp, baroPressure;
    int memory;

    takeMeasurement(battVoltage, memory, minDistance, maxDistance, medianDistance,
                    airTemp, airHumidity, baroTemp, baroPressure);

    String allData = timestamp.timestamp() + "," +
                     String(battVoltage, 2) + "," +
                     String(memory) + "," +
                     String(minDistance, 2) + "," +
                     String(maxDistance, 2) + "," +
                     String(medianDistance, 2) + "," +
                     String(airTemp, 2) + "," +
                     String(airHumidity, 2) + "," +
                     String(baroTemp, 2) + "," +
                     String(baroPressure, 2);

    writeToCSV(myHeader, allData, "/DATA.csv");
    logMessage("Data: " + allData);

    int nTracking = tracking();

    if (nTracking >= 4) {
      logMessage("Number of rows in /TRACKING.csv is >= 4");
      SD.remove("/TRACKING.csv");
      logMessage("Remove tracking");

      // Data keeps flowing even when the clock is broken. The Apps Script
      // derives measurement times from the gateway transmit time, not from the
      // DDHHMM header, so a wrong header costs accuracy in one unused field --
      // losing the data entirely would be far worse.
      //
      // What an untrusted clock DOES change is scheduling: timestamp.hour() is
      // then meaningless, and if it happened to sit on a transmission hour the
      // station would fire on every bundle and burn ~3x the Iridium credits.
      // So while the clock is untrusted we ignore the hour schedule and fall
      // back to the "5 queued readings" trigger, which is self-limiting.
      bool clockTrusted = rtcOk && rtcTrusted;

      // v1.3: the MEDIAN goes on the radio, not the minimum. The minimum is the
      // statistic most sensitive to spurious short echoes (blowing snow, mast
      // reflections) and measured ~69 mm below the burst median over the 2026
      // record, biasing snow depth about 7 cm high.
      String transmitData = timestamp.timestamp() + "," +
                            String(battVoltage, 2) + "," +
                            String(medianDistance) + "," +
                            String(airTemp, 1) + "," +
                            String(airHumidity, 0);

      logMessage("Write to /TRANSMIT.csv: " + transmitData);
      writeToCSV(transmitHeader, transmitData, "/TRANSMIT.csv");

      CSV_Parser cp("sffff", true, ',');
      cp.readSDfile("/TRANSMIT.csv");
      int num_rows_transmit = cp.getRowsCount();
      logMessage("Number of rows in /TRANSMIT.csv: " + String(num_rows_transmit));

      bool sessionRun = false;
      for (int i = 0; i < numTransmissionTimes; i++) {
        logMessage("Current hour = " + String(timestamp.hour()) +
                   ", Transmission hour = " + String(transmissionHours[i]));

        bool hourMatch = clockTrusted && (timestamp.hour() == transmissionHours[i]);
        if (hourMatch || num_rows_transmit >= 5) {
          String msg = prepMsg();
          if (msg.length() < 8) {           // header is 7 chars + at least one reading
            logMessage("No usable message this cycle - skipping transmission");
            break;
          }
          logMessage(msg);

          int irid_err = sendMsg(msg, timestamp);
          sessionRun = true;

          if (irid_err == 0) {
            logMessage("Message sent! Removing /TRANSMIT.csv");
            SD.remove("/TRANSMIT.csv");
          } else {
            logMessage("Transmission failed. Error = " + String(irid_err));
          }
          break;
        }
      }

      // No message this cycle and the clock needs help: open a sync-only
      // session. AT-MSSTM needs network registration, not a message, so this
      // repairs the clock within ~2 h without spending any Iridium credits.
      if (!sessionRun && !clockTrusted) {
        logMessage("RTC not trusted and no message due - sync-only session (no credits)");
        syncOnly();
      }

      if (num_rows_transmit >= 10) {
        logMessage("n_transmit >= 10, too much to transmit, delete /TRANSMIT.csv");
        SD.remove("/TRANSMIT.csv");
      }
    }

    logMessage("Done loop ...");
    powerDown();
    delay(3000);
    powerDown();          // second attempt: the first failed 72 times in 2026
    delay(3000);
    logMessage("Power down failed - TPL5110 did not cut power");
  }

  /* =========================================================================== */
  /* ========== CLOCK ========================================================== */
  /* =========================================================================== */

  /* Initialise the RTC and decide whether its current value can be trusted. */
  void initClock() {
    rtcOk = false;
    for (uint8_t i = 0; i < 3 && !rtcOk; i++) {
      rtcOk = rtc.begin();
      if (!rtcOk) delay(200);
    }

    if (!rtcOk) {
      rtcTrusted = false;
      logMessage("RTC Initialization Failed - Using Default Time");
      blinky(4, 100, 100, 1000);
      return;
    }

    // The PCF8523 has a STOP bit that survives a battery event. If it is set the
    // oscillator is halted and now() returns the same value forever -- exactly
    // the frozen-clock signature seen in Feb 2025. start() clears it.
    rtc.start();

    bool lost = rtc.lostPower() || !rtc.initialized();

    uint32_t nowU = rtc.now().unixtime();
    bool plausible = (nowU >= (BUILD_UNIX - ERA_WINDOW_BACK)) &&
                     (nowU <= (BUILD_UNIX + ERA_WINDOW_FWD));

    uint32_t prevBoot = 0;
    bool havePrev = readLastBoot(prevBoot);
    bool stalled = havePrev && (nowU <= prevBoot);

    rtcTrusted = (!lost && plausible && !stalled);

    if (!rtcTrusted) {
      logMessage("RTC present but NOT trusted (lostPower=" + String(lost ? 1 : 0) +
                 " plausible=" + String(plausible ? 1 : 0) +
                 " stalled=" + String(stalled ? 1 : 0) +
                 ") - next Iridium time will be accepted unconditionally");
    }

    writeLastBoot(nowU);
  }

  /* Ask the modem for network time and decide whether to apply it.
   * This mirrors a reference implementation replayed against 1,026 real 2026
   * sync events; do not reorder the checks. */
  void syncClock() {
    uint32_t ticks = 0;
    String rawHex = "";

    if (!readMsstmTicks(ticks, rawHex)) {
      logMessage(" - CLOCK: no usable AT-MSSTM response (raw hex='" + rawHex + "') - RTC unchanged");
      return;
    }

    uint32_t rtcUnix = rtcOk ? rtc.now().unixtime() : 0;
    uint32_t candUnix = 0;
    const char *eraName = NULL;

    if (!selectEra(ticks, rtcUnix, rtcTrusted, candUnix, eraName)) {
      // This is what a new Iridium era looks like. Nothing is applied; the hex
      // below is all you need to work out the new epoch.
      logMessage(" - CLOCK: MSSTM=0x" + rawHex + " ticks=" + String(ticks) +
                 " matches NO KNOWN ERA - RTC unchanged. New epoch = true_utc - ticks*0.09s");
      return;
    }

    DateTime cand(candUnix);
    logMessage(" - CLOCK: MSSTM=0x" + rawHex + " ticks=" + String(ticks) +
               " era=" + String(eraName) + " -> " + cand.timestamp() + "Z");

    if (!rtcOk) {
      logMessage(" - CLOCK: RTC hardware unavailable - cannot apply");
      return;
    }

    if (!rtcTrusted) {
      rtc.adjust(cand);
      rtcTrusted = true;
      clearPending();
      logMessage(" - CLOCK: RTC was untrusted; set to " + rtc.now().timestamp());
      return;
    }

    int32_t diff = (int32_t)(candUnix - rtcUnix);
    uint32_t adiff = (candUnix > rtcUnix) ? (candUnix - rtcUnix) : (rtcUnix - candUnix);

    if (adiff < CLOCK_NEAR_GATE) {
      rtc.adjust(cand);
      clearPending();
      logMessage(" - CLOCK: applied " + String(diff) + " s -> " + rtc.now().timestamp());
      return;
    }

    // Correction larger than 24 h. Hold it until a second reading confirms that
    // the modem's clock advanced by the same amount the RTC did -- which proves
    // the modem is self-consistent and the RTC is simply offset.
    uint32_t pCand = 0, pRtc = 0;
    if (loadPending(pCand, pRtc)) {
      uint32_t age = (rtcUnix > pRtc) ? (rtcUnix - pRtc) : 0;
      if (age > 0 && age < CLOCK_PENDING_MAXAGE) {
        int32_t dModem = (int32_t)(candUnix - pCand);
        int32_t dRtc   = (int32_t)(rtcUnix - pRtc);
        int32_t mism   = dModem - dRtc;
        if (mism < 0) mism = -mism;
        if (mism < CLOCK_CONSIST_GATE) {
          rtc.adjust(cand);
          clearPending();
          logMessage(" - CLOCK: large correction " + String(diff) +
                     " s CONFIRMED by two consistent reads. RTC repaired -> " + rtc.now().timestamp());
          return;
        }
        logMessage(" - CLOCK: pending read disagrees by " + String(mism) + " s - discarding it");
      }
    }

    savePending(candUnix, rtcUnix);
    logMessage(" - CLOCK: proposal differs by " + String(diff) +
               " s (>24h). HELD pending confirmation at the next transmission.");
  }

  /* Read AT-MSSTM directly. Returns false for a missing response, a
   * "no network service" reply, or the 0xFFFFFFFF "time not yet acquired" value. */
  bool readMsstmTicks(uint32_t &ticks, String &rawHex) {
    rawHex = "";
    while (IridiumSerial.available()) IridiumSerial.read();

    IridiumSerial.print("AT-MSSTM\r");

    String response = "";
    unsigned long start = millis();
    while ((millis() - start) < 3000) {
      while (IridiumSerial.available()) {
        char c = (char)IridiumSerial.read();
        if (response.length() < 200) response += c;   // bounded: never grows without limit
      }
      if (response.indexOf("OK") != -1 || response.indexOf("ERROR") != -1) break;
    }

    int idx = response.indexOf("-MSSTM:");
    if (idx == -1) return false;

    int i = idx + 7;                                   // strlen("-MSSTM:")
    while (i < (int)response.length() && response.charAt(i) == ' ') i++;
    while (i < (int)response.length() && isHexadecimalDigit(response.charAt(i)) && rawHex.length() < 8) {
      rawHex += response.charAt(i);
      i++;
    }

    if (rawHex.length() == 0) return false;            // e.g. "no network service"
    if (rawHex.equalsIgnoreCase("ffffffff")) return false;

    ticks = (uint32_t)strtoul(rawHex.c_str(), NULL, 16);
    if (ticks == 0) return false;
    return true;
  }

  /* Decode the tick count against every known epoch and return the only
   * candidate that can be real. The 64-bit multiply is required: ticks*90
   * overflows 32 bits about 50 days into an era. */
  bool selectEra(uint32_t ticks, uint32_t rtcUnix, bool trusted,
                 uint32_t &outUnix, const char *&outName) {
    uint64_t totalMillis = (uint64_t)ticks * 90ULL;
    uint32_t secs = (uint32_t)(totalMillis / 1000ULL);

    uint32_t lo = BUILD_UNIX - ERA_WINDOW_BACK;
    uint32_t hi = BUILD_UNIX + ERA_WINDOW_FWD;

    bool found = false;
    uint32_t best = 0;
    const char *bestName = NULL;

    for (uint8_t i = 0; i < NUM_ERAS; i++) {
      uint32_t cand = IRIDIUM_ERAS[i].epoch + secs;    // max 2,126,103,913 - fits in uint32
      if (cand < lo || cand > hi) continue;

      if (!found) {
        best = cand; bestName = IRIDIUM_ERAS[i].name; found = true;
        continue;
      }
      if (trusted) {
        uint32_t dc = (cand > rtcUnix) ? (cand - rtcUnix) : (rtcUnix - cand);
        uint32_t db = (best > rtcUnix) ? (best - rtcUnix) : (rtcUnix - best);
        if (dc < db) { best = cand; bestName = IRIDIUM_ERAS[i].name; }
      } else {
        if (cand > best) { best = cand; bestName = IRIDIUM_ERAS[i].name; }
      }
    }

    outUnix = best;
    outName = bestName;
    return found;
  }

  /* ---- small SD-backed state; every failure falls back to a safe default ---- */

  bool loadPending(uint32_t &pCand, uint32_t &pRtc) {
    if (!sdOk || !SD.exists(PENDING_FILE)) return false;
    File f = SD.open(PENDING_FILE, FILE_READ);
    if (!f) return false;
    String line = f.readStringUntil('\n');
    f.close();
    int c = line.indexOf(',');
    if (c <= 0) return false;
    pCand = (uint32_t)strtoul(line.substring(0, c).c_str(), NULL, 10);
    pRtc  = (uint32_t)strtoul(line.substring(c + 1).c_str(), NULL, 10);
    return (pCand > 0 && pRtc > 0);
  }

  void savePending(uint32_t pCand, uint32_t pRtc) {
    if (!sdOk) return;
    SD.remove(PENDING_FILE);
    File f = SD.open(PENDING_FILE, FILE_WRITE);
    if (!f) return;
    f.print(pCand); f.print(","); f.println(pRtc);
    f.close();
  }

  void clearPending() {
    if (sdOk) SD.remove(PENDING_FILE);
  }

  bool readLastBoot(uint32_t &prev) {
    if (!sdOk || !SD.exists(LASTBOOT_FILE)) return false;
    File f = SD.open(LASTBOOT_FILE, FILE_READ);
    if (!f) return false;
    String line = f.readStringUntil('\n');
    f.close();
    prev = (uint32_t)strtoul(line.c_str(), NULL, 10);
    return (prev > 0);
  }

  void writeLastBoot(uint32_t nowU) {
    if (!sdOk) return;
    SD.remove(LASTBOOT_FILE);
    File f = SD.open(LASTBOOT_FILE, FILE_WRITE);
    if (!f) return;
    f.println(nowU);
    f.close();
  }

  /* =========================================================================== */
  /* ========== AUX FUNCTIONS ================================================== */
  /* =========================================================================== */

  void powerDown() {
    digitalWrite(donePin, HIGH);
    delay(100);
    digitalWrite(donePin, LOW);
  }

  void initializeSensors() {
    if (!sht31.begin(0x44)) {
      blinky(2, 100, 100, 1000);
      logMessage("SHT31 Initialization Failed");
    }
    if (!bmp.begin_I2C()) {
      blinky(3, 100, 100, 1000);
      logMessage("BMP390 Initialization Failed");
    }
    bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_8X);
    bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);
  }

  /* One-shot summary printed at boot. This is what you read in the field
   * before walking away from the station. */
  void selfTest() {
    Serial.println(F("--- self test ---"));
    Serial.print(F("build   : ")); Serial.print(F(__DATE__)); Serial.print(F(" ")); Serial.println(F(__TIME__));
    Serial.print(F("SD      : ")); Serial.println(sdOk ? F("OK") : F("FAIL"));
    Serial.print(F("RTC     : "));
    if (!rtcOk) {
      Serial.println(F("FAIL"));
    } else {
      DateTime n = rtc.now();
      DateTime p = n - TimeSpan(8 * 3600);
      Serial.print(F("OK  trusted=")); Serial.print(rtcTrusted ? F("YES") : F("NO"));
      Serial.print(F("  UTC=")); Serial.print(n.timestamp());
      Serial.print(F("  PST=")); Serial.println(p.timestamp());
    }
    Serial.print(F("SHT31 T : ")); Serial.println(sht31.readTemperature());
    Serial.print(F("BMP390 P: ")); Serial.println(bmp.readPressure() / 100.0);
    Serial.print(F("battery : ")); Serial.println(sampleBatteryVoltage());
    Serial.println(F("-----------------"));
    Serial.flush();
    logMessage("Setup Complete (v1.3)");
  }

  void takeMeasurement(float &battVoltage, int &memory, float &minDistance, float &maxDistance,
                       float &medianDistance, float &airTemp, float &airHumidity,
                       float &baroTemp, float &baroPressure) {
    battVoltage = sampleBatteryVoltage();
    memory = freeMemory();
    sampleUltrasonic(minDistance, maxDistance, medianDistance);

    String tempHumidity = sampleSHT();
    airTemp = tempHumidity.substring(0, tempHumidity.indexOf(",")).toFloat();
    airHumidity = tempHumidity.substring(tempHumidity.indexOf(",") + 1).toFloat();

    String bmpData = sampleBMP();
    baroTemp = bmpData.substring(0, bmpData.indexOf(",")).toFloat();
    baroPressure = bmpData.substring(bmpData.indexOf(",") + 1).toFloat();
  }

  int tracking() {
    logMessage("Add row to /TRACKING.csv");
    writeToCSV("n", "1", "/TRACKING.csv");
    CSV_Parser cp("s", true, ',');
    cp.readSDfile("/TRACKING.csv");
    int nTracking = cp.getRowsCount() - 1;
    delay(50);
    logMessage("Number of rows in /TRACKING.csv: " + String(nTracking));
    // v1.3: the per-cycle status blink cost up to 3.2 s of delay plus LED
    // current on every wake-up. blinky() is now reserved for error codes.
    return nTracking;
  }

  /* Prepare Message for Transmission.
   * WIRE FORMAT UNCHANGED: DDHHMMB then, per reading, DDD(+/-)TTTHH joined by ':'.
   * All three fields are clamped so the format stays fixed-width no matter what
   * the sensors report -- a wild reading used to be able to emit a wider field
   * and make the whole message unparseable downstream. */
  String prepMsg() {
    CSV_Parser cp("sffff", true, ',');
    cp.readSDfile("/TRANSMIT.csv");
    int num_rows = cp.getRowsCount();

    char **out_datetimes = (char **)cp["datetime"];
    float *out_batt_v = (float *)cp["batt_v"];
    float *out_snow_depth_mm = (float *)cp["snow_depth_mm"];
    float *out_air_2m_temp_deg_c = (float *)cp["air_2m_temp_deg_c"];
    float *out_air_2m_temp_rh_prct = (float *)cp["air_2m_temp_rh_prct"];

    // v1.3 guard. If /TRANSMIT.csv is missing, empty or truncated -- a failed SD
    // write, a full card, corruption -- then num_rows is 0 and the column
    // pointers are NULL, and the original code went straight to
    // out_batt_v[num_rows - 1], i.e. NULL[-1]. On a SAMD21 that is a hard fault
    // with the TPL5110 still holding power on: the station hangs and flattens
    // the battery. Reachable whenever an SD write fails on a transmission hour.
    if (num_rows <= 0 || out_datetimes == NULL || out_batt_v == NULL ||
        out_snow_depth_mm == NULL || out_air_2m_temp_deg_c == NULL ||
        out_air_2m_temp_rh_prct == NULL) {
      logMessage("prepMsg: /TRANSMIT.csv empty or unreadable - nothing to send");
      return String("");
    }
    if (out_datetimes[0] == NULL || strlen(out_datetimes[0]) < 16) {
      logMessage("prepMsg: first timestamp malformed - nothing to send");
      return String("");
    }

    int charge_status;
    float batt_voltage = out_batt_v[num_rows - 1];
    if (batt_voltage >= 4.2)      charge_status = 9;
    else if (batt_voltage >= 4.1) charge_status = 8;
    else if (batt_voltage >= 4.0) charge_status = 7;
    else if (batt_voltage >= 3.9) charge_status = 6;
    else if (batt_voltage >= 3.8) charge_status = 5;
    else if (batt_voltage >= 3.7) charge_status = 4;
    else if (batt_voltage >= 3.6) charge_status = 3;
    else if (batt_voltage >= 3.5) charge_status = 2;
    else if (batt_voltage >= 3.4) charge_status = 1;
    else                          charge_status = 0;

    String datastring_msg =
      String(out_datetimes[0]).substring(8, 10) +   // Day
      String(out_datetimes[0]).substring(11, 13) +  // Hour
      String(out_datetimes[0]).substring(14, 16) +  // Minute
      String(charge_status);                        // Battery

    // v1.3: these were [4]/[4]/[3]. "%+04d" always writes 4 characters plus a
    // NUL, so the old temperature buffer overflowed by one byte on EVERY message.
    char formatted_snow_depth[8];
    char formatted_temp[8];
    char formatted_humidity[4];

    for (int i = 0; i < num_rows; i++) {
      long snow_depth_cm = lround(out_snow_depth_mm[i] / 10.0);
      long temp          = lround(out_air_2m_temp_deg_c[i] * 10.0);
      long humidity      = lround(out_air_2m_temp_rh_prct[i]);

      if (snow_depth_cm < 0)   snow_depth_cm = 0;
      if (snow_depth_cm > 999) snow_depth_cm = 999;
      if (temp < -999)         temp = -999;
      if (temp > 999)          temp = 999;
      if (humidity < 0)        humidity = 0;
      if (humidity >= 100)     humidity = 0;   // 100 % is sent as "00", as before

      sprintf(formatted_snow_depth, "%03d",  (int)snow_depth_cm);
      sprintf(formatted_temp,       "%+04d", (int)temp);
      sprintf(formatted_humidity,   "%02d",  (int)humidity);

      datastring_msg += String(formatted_snow_depth) +
                        String(formatted_temp) +
                        String(formatted_humidity);

      if (i < num_rows - 1) datastring_msg += ":";
    }
    return datastring_msg;
  }

  void blinky(int16_t n, int16_t high_ms, int16_t low_ms, int16_t btw_ms) {
    for (int i = 1; i <= n; i++) {
      digitalWrite(led, HIGH);
      delay(high_ms);
      digitalWrite(led, LOW);
      delay(low_ms);
    }
    delay(btw_ms);
  }

  float sampleBatteryVoltage() {
    pinMode(vbatPin, INPUT);
    return (analogRead(vbatPin) * 2 * 3.3) / 1024;
  }

  void writeToCSV(String header, String data, String filename) {
    if (!sdOk) return;
    if (!SD.exists(filename)) {
      File file = SD.open(filename, FILE_WRITE);
      if (!file) { Serial.println("SD Write Error: " + filename); return; }
      file.println(header);
      file.println(data);
      file.close();
    } else {
      File file = SD.open(filename, FILE_WRITE);
      if (!file) { Serial.println("SD Write Error: " + filename); return; }
      file.println(data);
      file.close();
    }
  }

  void logMessage(const String &message) {
    DateTime pst = rtcOk ? (rtc.now() - TimeSpan(8 * 3600)) : default_time;
    String logEntry = pst.timestamp() + ", " + message;
    Serial.println(logEntry);
    Serial.flush();
    writeToCSV("DATETIME, MESSAGE: ", logEntry, "/LOG.csv");
  }

  /* Power the modem up purely to recover the clock. No SBD message is sent, so
   * this consumes no Iridium credits. Used when the RTC cannot be trusted --
   * without it a dead coin cell or a stalled oscillator would be unrecoverable,
   * because the station would refuse to transmit and therefore never resync. */
  void syncOnly() {
    digitalWrite(IridSlpPin, HIGH);
    delay(2000);
    IridiumSerial.begin(19200);

    modem.setPowerProfile(IridiumSBD::USB_POWER_PROFILE);

    int status = modem.begin();
    if (status == ISBD_IS_ASLEEP) {
      logMessage(" - Modem asleep, wake up (sync only)");
      status = modem.begin();
    }

    if (status == ISBD_SUCCESS) {
      logMessage(" - Modem begin successful (sync only)");
      syncClock();
    } else {
      logMessage(" - Modem begin unsuccessful (sync only): " + String(status));
    }

    modem.sleep();
    digitalWrite(IridSlpPin, LOW);
    delay(100);
  }

  /* Send Message via Iridium Modem */
  int sendMsg(String msg, DateTime timestamp) {
    digitalWrite(IridSlpPin, HIGH);
    delay(2000);
    IridiumSerial.begin(19200);
    logMessage(" - attempt to send message: " + msg);

    modem.setPowerProfile(IridiumSBD::USB_POWER_PROFILE);

    int status = modem.begin();
    if (status == ISBD_IS_ASLEEP) {
      logMessage(" - Modem asleep, wake up");
      status = modem.begin();
    }
    if (status == ISBD_SUCCESS) {
      logMessage(" - Modem begin successful");
    } else {
      logMessage(" - Modem begin unsuccessful: " + String(status));
      digitalWrite(IridSlpPin, LOW);
      return status;
    }

    logMessage(" - Sending...");
    status = modem.sendSBDText(msg.c_str());
    logMessage(" - Send response: " + String(status));   // v1.3: was pointer arithmetic

    if (status != ISBD_SUCCESS) {
      // v1.3: all 21 failures in 2026 were a ~300 s ISBD_SENDRECEIVE_TIMEOUT (7)
      // followed by an instant ISBD_IS_ASLEEP (10). IridiumSBD tracks power in a
      // private 'asleep' flag that a failed send leaves false, so a second
      // begin() returns ISBD_ALREADY_AWAKE and then powers the modem OFF.
      // modem.sleep() drives the pin LOW *and* sets asleep=true, so the begin()
      // below really restarts the modem. In IridiumSBD 2.0 sleep() sends no AT
      // command (the AT*F block is compiled out), so it cannot block.
      logMessage(" - Retry: power-cycling modem");
      modem.sleep();
      delay(2000);
      digitalWrite(IridSlpPin, HIGH);
      delay(5000);                      // RockBLOCK charge store needs ~10 s from cold
      IridiumSerial.end();
      delay(50);
      IridiumSerial.begin(19200);

      int rstat = modem.begin();
      logMessage("   - Retry begin: " + String(rstat));
      if (rstat == ISBD_SUCCESS) {
        modem.adjustSendReceiveTimeout(180);   // bounded, so a bad cycle cannot run long
        logMessage("   - Sending...");
        status = modem.sendSBDText(msg.c_str());
        logMessage("   - Retry send response: " + String(status));
      } else {
        status = rstat;
      }
    }

    syncClock();

    modem.sleep();
    digitalWrite(IridSlpPin, LOW);
    delay(100);

    return status;
  }

  /* Sample the MaxBotix MB7374.
   *
   * v1.3: triggerPin is now held HIGH for the whole burst. The datasheet is
   * explicit that the snow filter "will initialize 40 readings (about 7 seconds)
   * after sensor power is applied, or after the RX pin is brought high and held
   * high" -- the old code pulled it LOW after every single reading, which reset
   * the filter each time and ran the sensor unfiltered. Filtered free-run output
   * updates at 1.33 Hz, hence the 800 ms spacing. */
  void sampleUltrasonic(float &minDistance, float &maxDistance, float &medianDistance) {
    float d[US_SAMPLES];
    uint8_t nValid = 0;

    digitalWrite(triggerPin, HIGH);
    delay(US_WARMUP_MS);

    uint8_t nRejected = 0;
    for (uint8_t i = 0; i < US_SAMPLES; i++) {
      unsigned long pw = pulseIn(pulsePin, HIGH, US_PULSE_TIMEOUT_US);
      // pw == 0 means the read timed out. Anything outside the sensor's
      // physical range is noise or a floating pin, not a distance -- the old
      // code passed such values straight through (a timeout spike once
      // produced a six-character payload field and broke the wire format).
      if (pw >= US_MIN_VALID_US && pw <= US_MAX_VALID_US) {
        d[nValid++] = (float)pw;          // 1 us per mm
      } else if (pw != 0) {
        nRejected++;
      }
      if (i < US_SAMPLES - 1) delay(US_SAMPLE_GAP_MS);
    }
    if (nRejected > 0) {
      logMessage("Ultrasonic: rejected " + String(nRejected) + " out-of-range reading(s)");
    }

    digitalWrite(triggerPin, LOW);

    if (nValid == 0) {
      // No target / no return. Report max range, as the sensor itself would.
      minDistance = maxDistance = medianDistance = US_MAX_RANGE_MM;
      logMessage("Ultrasonic: no valid returns");
      return;
    }

    // Insertion sort - deterministic, and small enough to reason about.
    for (uint8_t i = 1; i < nValid; i++) {
      float key = d[i];
      int8_t j = (int8_t)i - 1;
      while (j >= 0 && d[j] > key) { d[j + 1] = d[j]; j--; }
      d[j + 1] = key;
    }

    minDistance = d[0];
    maxDistance = d[nValid - 1];
    medianDistance = (nValid % 2 == 1) ? d[nValid / 2]
                                       : (d[nValid / 2 - 1] + d[nValid / 2]) / 2.0;
  }

  /* v1.3: a failed SHT31 returns NaN, and String(NaN).toFloat() silently became
   * 0.0 -- so a dead sensor reported 0.0 C and 0 % RH, which the Apps Script
   * then displays as 100 % humidity. Both look entirely plausible at a snow
   * station in winter, so the fault could go unnoticed for weeks. Report an
   * unmistakable value instead and say so in the log. -99.9 C clamps to the
   * payload's "-999" sentinel without changing the wire format. */
  String sampleSHT() {
    sht31.heater(0);
    float temperature = sht31.readTemperature();
    float humidity = sht31.readHumidity();

    if (isnan(temperature) || isnan(humidity)) {
      logMessage("SHT31 returned NaN - air temp/humidity INVALID this cycle");
      return String(-99.9) + "," + String(0.0);
    }
    return String(temperature) + "," + String(humidity);
  }

  String sampleBMP() {
    if (!bmp.begin_I2C()) {
      logMessage("BMP390 re-init failed");
      return "0,0";
    }
    bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_8X);
    bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);
    if (!bmp.performReading()) {
      logMessage("BMP390 first reading failed");
      return "0,0";
    }
    delay(100);
    if (!bmp.performReading()) {
      logMessage("BMP390 second reading failed");
      return "0,0";
    }
    float temperature = bmp.readTemperature();
    float pressure = bmp.readPressure() / 100.0;
    return String(temperature) + "," + String(pressure);
  }
