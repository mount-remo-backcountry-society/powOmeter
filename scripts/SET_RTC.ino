/* ===========================================================================
 * SET_RTC  --  POW-O-METER field utility
 * ---------------------------------------------------------------------------
 * Sets the PCF8523 from this laptop's clock, then prints the result forever so
 * you can check it against your phone before flashing the main sketch.
 *
 * RUN THIS FIRST, BEFORE POW_O_METER_v1_3.ino.
 *
 * -------------------------------------------------------------------------
 * ONE THING TO SET BEFORE YOU COMPILE:  UTC_OFFSET_HOURS, just below.
 *
 * It is simply: how many hours to ADD to this laptop's clock to get UTC.
 * Do NOT reason about "PST" or "PDT" -- just use your laptop's actual offset.
 *
 *   laptop on UTC-7  ->  7      (British Columbia is UTC-7 year round)
 *   laptop on UTC-8  ->  8
 *
 * Then VERIFY, do not trust: the sketch prints the UTC it set. Check that
 * against an independent UTC source (time.is/UTC, or a phone set to UTC)
 * before you flash the main sketch. If the UTC line is right, the station is
 * right, whatever the offset was.
 *
 * The station's own log column is fixed UTC-8 by design and does not shift.
 * With BC on UTC-7 that column reads one hour behind local civil time, all
 * year. That is intentional: it keeps 17 months of existing record continuous.
 * -------------------------------------------------------------------------
 *
 * IMPORTANT: __DATE__ and __TIME__ are captured when the sketch is COMPILED,
 * not when it is uploaded. Press Upload directly (not Verify, then Upload) so
 * the gap stays small. UPLOAD_LAG_SECONDS compensates for the rest. A few
 * seconds either way is irrelevant for a snow station.
 * =========================================================================== */

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include "RTClib.h"

const int8_t  UTC_OFFSET_HOURS   = 7;   // <-- hours to ADD to laptop time to get UTC.
                                        //     BC is UTC-7 year round. VERIFY THE UTC LINE.
const uint8_t UPLOAD_LAG_SECONDS = 12;  // rough compile-to-run delay on a Feather M0

const byte chipSelect = 4;
const byte led = 8;

RTC_PCF8523 rtc;
bool rtc_working = false;

void setup() {
    Serial.begin(115200);
    unsigned long start = millis();
    while (!Serial && (millis() - start) < 5000) { }

    Serial.println();
    Serial.println(F("=== POW-O-METER  SET_RTC ==="));

    pinMode(led, OUTPUT);
    digitalWrite(led, LOW);

    if (!rtc.begin()) {
        Serial.println(F("RTC NOT FOUND - check the I2C wiring and the coin cell."));
        for (int i = 0; i < 20; i++) {
            digitalWrite(led, HIGH); delay(100);
            digitalWrite(led, LOW);  delay(100);
        }
        return;   // never hangs in a while(1)
    }
    rtc_working = true;

    // Clear the PCF8523 STOP bit. It survives a battery event, and while it is
    // set the oscillator is halted and now() returns the same value forever.
    rtc.start();

    DateTime before = rtc.now();

    // Compile time is laptop-local; add the offset to get UTC.
    DateTime compiled(F(__DATE__), F(__TIME__));
    DateTime utc = compiled + TimeSpan((int32_t)UTC_OFFSET_HOURS * 3600L
                                       + (int32_t)UPLOAD_LAG_SECONDS);
    rtc.adjust(utc);

    Serial.print(F("assumed laptop is UTC-")); Serial.print(UTC_OFFSET_HOURS);
    Serial.println(F("  (hours added to laptop clock)"));
    Serial.print(F("compile time       : ")); Serial.println(compiled.timestamp());
    Serial.print(F("RTC before         : ")); Serial.println(before.timestamp());
    Serial.print(F("RTC set to (UTC)   : ")); Serial.println(utc.timestamp());
    Serial.print(F("lostPower flag     : ")); Serial.println(rtc.lostPower() ? F("YES") : F("no"));

    // Best effort: clear the state files the main sketch keeps, so v1.3 starts
    // from a clean slate rather than inheriting a stale pending correction.
    if (SD.begin(chipSelect)) {
        if (SD.exists("/CLKSYNC.CSV"))  { SD.remove("/CLKSYNC.CSV");  Serial.println(F("removed /CLKSYNC.CSV")); }
        if (SD.exists("/LASTBOOT.CSV")) { SD.remove("/LASTBOOT.CSV"); Serial.println(F("removed /LASTBOOT.CSV")); }
        Serial.println(F("SD                 : OK"));
    } else {
        Serial.println(F("SD                 : not readable (fine for this sketch)"));
    }

    Serial.println();
    Serial.println(F(">>> CHECK THE 'RTC (UTC)' LINE BELOW against time.is/UTC <<<"));
    Serial.println(F("If UTC is right, the station is right. Then flash v1.3."));
    Serial.println(F("The 'station' column is fixed UTC-8 by design, so with BC"));
    Serial.println(F("on UTC-7 it reads 1 h behind local civil time, all year."));
    Serial.println();
}

void loop() {
    if (!rtc_working) {
        Serial.println(F("RTC not working - nothing to display."));
        delay(5000);
        return;
    }
    DateTime now = rtc.now();
    DateTime pst = now - TimeSpan(8 * 3600);
    Serial.print(F("RTC (UTC): ")); Serial.print(now.timestamp());
    Serial.print(F("   |   station clock (UTC-8): ")); Serial.println(pst.timestamp());
    delay(5000);
}
