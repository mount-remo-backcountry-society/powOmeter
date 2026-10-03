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
  #include <QuickStats.h>       // Stats
  #include <MemoryFree.h>

  /* Pin Definitions */
  const byte chipSelect = 4;   // Chip select pin for SD card
  const byte led = 8;          // Built in led pin
  const byte vbatPin = 9;      // Batt pin
  const byte triggerPin = 10;  // Range start / stop pin for MaxBotix MB7369 ultrasonic ranger
  const byte pulsePin = 12;    // Pulse width pin for reading pw from MaxBotix MB7369 ultrasonic ranger
  const byte IridSlpPin = 13;  // Sleep control pin for Iridium modem
  const byte donePin = 5;      // Done pin - This is for the TP5110. Can be any pin. It's important that the donePin is written LOW and THEN HIGH. This shift
                              // from low to HIGH is how the Nano Power Timer knows to turn off the
                              // microcontroller.
  /* Transmission Times */
  // const uint8_t transmissionHours[] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23 };  // Hours in 24-h clock of when data should be transmitted via modem
  const uint8_t transmissionHours[] = { 1, 6, 8, 10, 15, 20, 22 };  // Hours in 24-h clock of when data should be transmitted via modem
  const int numTransmissionTimes = sizeof(transmissionHours) / sizeof(transmissionHours[0]);  // Number of transmissions per day
  
  bool rtc_working = false;
  DateTime default_time(2000, 1, 1, 0, 0, 0); // January 1, 2000 00:00:00

  /*Define Iridium seriel communication as Serial1 */
  #define IridiumSerial Serial1
  IridiumSBD modem(IridiumSerial, IridSlpPin);  // Declare the IridiumSBD object

  /* Global Variables */
  RTC_PCF8523 rtc;  // Setup a PCF8523 Real Time Clock instance (may have to change this to more precise DS3231)
  Adafruit_SHT31 sht31 = Adafruit_SHT31();  // Temperature/Humidity sensor instance
  Adafruit_BMP3XX bmp;           // BMP390 sensor instance
  QuickStats stats;  // Instance of QuickStats

  String myHeader = "datetime,batt_v,memory,snow_depth_min_mm,snow_depth_max_mm,snow_depth_median_mm,air_2m_temp_deg_c,air_2m_temp_rh_prct,baro_temp_deg_c,baro_pressure_hPa";
  String transmitHeader = "datetime,batt_v,snow_depth_mm,air_2m_temp_deg_c,air_2m_temp_rh_prct";

  /* Function Prototypes */
  void initializeSensors();
  String takeMeasurement();
  String prepMsg();
  void writeToCSV(String header, String data, String filename);
  void logMessage(const String &message);
  int sendMsg(String msg, DateTime timestamp);
  float dataBatteryVoltage();
  void dataUltrasonic(float &minDistance, float &maxDistance, float &medianDistance);
  String dataSHT();
  String dataBMP();
  void powerDown();
  int freeMemory();

  /* =========================================================================== */
  /* ========== SETUP LOOP ===================================================== */
  /* =========================================================================== */

  void setup(void) {
    // Connect serial monitor 
    delay(100);
    Serial.begin(115200);
    delay(2000); // Give time for Serial Monitor to connect
    Serial.println("=== Serial communication started ===");

    // Configure pins and set initial states
    pinMode(led, OUTPUT); digitalWrite(led, LOW); delay(50);
    pinMode(triggerPin, OUTPUT); digitalWrite(triggerPin, LOW); delay(50);
    pinMode(pulsePin, INPUT);
    pinMode(donePin, OUTPUT); digitalWrite(donePin, LOW); delay(50);
    pinMode(IridSlpPin, OUTPUT); digitalWrite(IridSlpPin, LOW); delay(50);

    // Put modem in sleep mode -- IS THIS NECESSARY?
    modem.sleep();

    // Initialize SD card
    if (!SD.begin(chipSelect)) {
        //logMessage("SD Initialization Failed");
        Serial.println("SD Initialization Failed");
        Serial.flush();
        blinky(5, 100, 100, 2000);
        powerDown(); // Signal TPL5110 to power off the system
    }
    
    // Initialize sensors and peripherals
    initializeSensors();

    logMessage("Setup Complete");
  }

  /* =========================================================================== */
  /* ========== MAIN LOOP ====================================================== */
  /* =========================================================================== */

  void loop(void) {
    // Read time and convert UTC time to PST (UTC-8)
    DateTime now;
    DateTime timestamp;
    if (rtc_working) {
        now = rtc.now();
        timestamp = now - TimeSpan(8 * 3600);
    } else {
        timestamp = default_time;
    }

    // Define variables to hold measurement data
    float battVoltage, minDistance, maxDistance, medianDistance, airTemp, airHumidity, baroTemp, baroPressure;
    int memory;

    // TAKE MEASUREMENT
    takeMeasurement(battVoltage, memory, minDistance, maxDistance, medianDistance, airTemp, airHumidity, baroTemp, baroPressure);

    // Write all parameters to DATA.csv
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

    // INCREMENT TRACKING.csv to know how many data points have been taken
    int nTracking = tracking();  

    // How many data UNTIL YOU WRITE TO TRANSMIT (for TPL at 15m, then this should be >=4)
    if (nTracking >= 4) {
      logMessage("Number of rows in /TRACKING.csv is >= 4");
      SD.remove("/TRACKING.csv");
      logMessage("Remove tracking");

      // Prepare subset of parameters for TRANSMIT.csv
      // int snowDepthCm = round(medianDistance / 10); // Convert mm to cm
      String transmitData = timestamp.timestamp() + "," +
                            String(battVoltage, 2) + "," +
                            String(minDistance) + "," +
                            String(airTemp, 1) + "," +
                            String(airHumidity, 0);

      // WRITE TO TRANSMIT.csv
      logMessage("Write to /TRANSMIT.csv: " + transmitData);
      writeToCSV(transmitHeader, transmitData, "/TRANSMIT.csv");
      
      // Check how many data points are stored in /TRANSMIT.csv
      CSV_Parser cp("sffff", true, ',');  // Set paramters for parsing the log file
      cp.readSDfile("/TRANSMIT.csv");
      int num_rows_transmit = cp.getRowsCount();  //Get # of rows minus header
      logMessage("Number of rows in /TRANSMIT.csv: " + String(num_rows_transmit));

      // Check if it's time to transmit data
      for (int i = 0; i < numTransmissionTimes; i++) {
        logMessage("Current hour = " + String(timestamp.hour()) + ", Transmission hour = " + String(transmissionHours[i]));
        
        // Transmit data if a) hour of current time = one of the transmission hours, or b) there's 5 hourly data points, or more
        if (timestamp.hour() == transmissionHours[i] || num_rows_transmit >= 5) {  

          // PARSE MSG FROM TRANSMIT.csv
          String msg = prepMsg();
          logMessage(msg);
          
          // Send message
          int irid_err = sendMsg(msg, timestamp);

          // IF SUCCESS, delete TRANSMIT.csv (if not, will try again at next hourly interval)
          if (irid_err == 0) {
            logMessage("Message sent! Removing /TRANSMIT.csv");
            SD.remove("/TRANSMIT.csv");
          } else {
            logMessage("Transmission failed. Error = " + String(irid_err));
          }
          break; // Exit loop once transmission is handled
        }
      }

      // If TRANSMIT.csv > 10 rows, then delete it! Probably too big to send
      if (num_rows_transmit >= 10) {
        logMessage("n_transmit >= 10, too much to transmit, delete /TRANSMIT.csv");
        SD.remove("/TRANSMIT.csv");
      }
    }

    // TRIGGER DONE PIN ON TPL
    logMessage("Done loop ...");
    powerDown(); // Signal TPL5110 to power off the system
    delay(5000);
    logMessage("Power down failed");
  }

  /* =========================================================================== */
  /* ========== AUX FUNCTIONS ================================================== */
  /* =========================================================================== */

  /* Signal TPL5110 to Power Down */
  void powerDown() {
    //logMessage("... powering down.");
    digitalWrite(donePin, HIGH);
    delay(100);
    digitalWrite(donePin, LOW);
  }

  /* Initialize Sensors and Peripherals */
  void initializeSensors() {
      // Initialize SHT31 temperature/humidity sensor
      if (!sht31.begin(0x44)) {
          blinky(2, 100, 100, 2000);
          logMessage("SHT31 Initialization Failed");
          // while (1); // Halt if sensor fails
      }

      // Initialize BMP390 barometric pressure sensor
      if (!bmp.begin_I2C()) {
          blinky(3, 100, 100, 2000);
          logMessage("BMP390 Initialization Failed");
          // while (1); // Halt if sensor fails
      }
      bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_8X);
      bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);

      // Initialize RTC
      rtc_working = rtc.begin();
      if (!rtc_working) {
        logMessage("RTC Initialization Failed - Using Default Time");
        blinky(4, 100, 100, 2000);
      }
  }

  /* Take Measurement */
  void takeMeasurement(float &battVoltage, int &memory, float &minDistance, float &maxDistance, float &medianDistance,
                      float &airTemp, float &airHumidity, float &baroTemp, float &baroPressure) {
      // Sample battery voltage
      battVoltage = sampleBatteryVoltage();

      // Sample memory usage
      memory = freeMemory();

      // Sample ultrasonic distances
      sampleUltrasonic(minDistance, maxDistance, medianDistance);

      // Sample temperature and humidity
      String tempHumidity = sampleSHT();
      airTemp = tempHumidity.substring(0, tempHumidity.indexOf(",")).toFloat();
      airHumidity = tempHumidity.substring(tempHumidity.indexOf(",") + 1).toFloat();

      // Sample barometric pressure and temperature
      String bmpData = sampleBMP();
      baroTemp = bmpData.substring(0, bmpData.indexOf(",")).toFloat();
      baroPressure = bmpData.substring(bmpData.indexOf(",") + 1).toFloat();
  }

  /* Add one line to /TRACKING.csv and return # of lines */
  int tracking(){
      logMessage("Add row to /TRACKING.csv");
      writeToCSV("n", "1", "/TRACKING.csv");
      CSV_Parser cp("s", true, ',');  // Set paramters for parsing the tracking file ("s" = "String")
      cp.readSDfile("/TRACKING.csv");
      int nTracking = cp.getRowsCount() - 1;  //Get # of rows minus header
      delay(50);
      logMessage("Number of rows in /TRACKING.csv: " + String(nTracking));
      blinky(nTracking, 100, 200, 2000);
      return nTracking;
  }

  /* Prepare Message for Transmission */
  String prepMsg() {
    CSV_Parser cp("sffff", true, ',');  // Set paramters for parsing the log file
    cp.readSDfile("/TRANSMIT.csv");
    int num_rows = cp.getRowsCount();  //Get # of rows

    char **out_datetimes = (char **)cp["datetime"];
    // float *out_mem = (float *)cp["memory"];
    float *out_batt_v = (float *)cp["batt_v"];
    float *out_snow_depth_mm = (float *)cp["snow_depth_mm"];
    float *out_air_2m_temp_deg_c = (float *)cp["air_2m_temp_deg_c"];
    float *out_air_2m_temp_rh_prct = (float *)cp["air_2m_temp_rh_prct"];

    // Determine charge status based on the battery voltage
    int charge_status;
    float batt_voltage = out_batt_v[num_rows - 1];
    if (batt_voltage >= 4.2) {
      charge_status = 9;
    } else if (batt_voltage >= 4.1) {
      charge_status = 8;
    } else if (batt_voltage >= 4.0) {
      charge_status = 7;
    } else if (batt_voltage >= 3.9) {
      charge_status = 6;
    } else if (batt_voltage >= 3.8) {
      charge_status = 5;
    } else if (batt_voltage >= 3.7) {
      charge_status = 4;
    } else if (batt_voltage >= 3.6) {
      charge_status = 3;
    } else if (batt_voltage >= 3.5) {
      charge_status = 2;
    } else if (batt_voltage >= 3.4) {
      charge_status = 1;
    } else {
      charge_status = 0;
    }

    String datastring_msg =
      String(out_datetimes[0]).substring(8, 10) +   // Day
      String(out_datetimes[0]).substring(11, 13) +  // Hour
      String(out_datetimes[0]).substring(14, 16) +  // Minute
      String(charge_status);                        // Battery (rounded to first decimal)

    char formatted_snow_depth[4];  // Buffer to hold formatted snow depth
    char formatted_temp[4];        // Buffer to hold formatted temperature
    char formatted_humidity[3];    // Buffer to hold formatted humidity

    for (int i = 0; i < num_rows; i++) {                     // For each observation in TRANSMIT.csv
      int snow_depth_cm = round(out_snow_depth_mm[i] / 10);  // Convert mm to cm and round
      int temp = round(out_air_2m_temp_deg_c[i] * 10);       // Round temperature to first decimal
      int humidity = round(out_air_2m_temp_rh_prct[i]);      // Round humidity without decimal

      sprintf(formatted_snow_depth, "%03d", snow_depth_cm);  // Format snow depth with leading zeros (3 digits)
      sprintf(formatted_temp, "%+04d", temp);                // Format temperature with leading zeros (3 digits)
      // sprintf(formatted_humidity, "%02d", humidity);         // Format humidity with leading zeros (2 digits)
      sprintf(formatted_humidity, "%02d", humidity >= 100 ? 0 : humidity);  // Format humidity with leading zeros (2 digits), 100 becomes 00

      datastring_msg +=
        String(formatted_snow_depth) +  // Snow depth in cm with leading zeros (3 digits)
        String(formatted_temp) +        // Temperature with leading zeros (3 digits)
        String(formatted_humidity);     // Humidity with leading zeros (2 digits)

      if (i < num_rows - 1) {
        datastring_msg += ":";  // Separator for different rows
      }
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

  /* Sample Battery Voltage */
  float sampleBatteryVoltage() {
      pinMode(vbatPin, INPUT);
      return (analogRead(vbatPin) * 2 * 3.3) / 1024;
  }

  void writeToCSV(String header, String data, String filename) {
    // IF FILE DOES NOT EXIST, WRITE HEADER AND DATA, ELSE, WRITE DATA
    if (!SD.exists(filename)){  //Write header if first time writing to the logfile
      File file = SD.open(filename, FILE_WRITE);  //Open file
      if (!file) {
          logMessage("SD Write Error: " + filename);
          return;
      }
      if (file) { // If file opened successfully
        file.println(header);
        file.println(data);
      }
      file.close();  //Close the file
    } else {
      File file = SD.open(filename, FILE_WRITE);
      if (!file) {
          logMessage("SD Write Error: " + filename);
          return;
      }
      if (file) {
        file.println(data);
        file.close();
      }
    }
  }

  /* Log Message to Serial and Log File */
  void logMessage(const String &message) {
    DateTime now;
    DateTime pst;
    if (rtc_working) {
        now = rtc.now();
        pst = now - TimeSpan(8 * 3600);  // Convert to PST
    } else {
        pst = default_time;
    }
    String timestamp = pst.timestamp();  // Convert DateTime to String after timezone adjustment
    String logEntry = timestamp + ", " + message;
    Serial.println(logEntry);
    Serial.flush();
    writeToCSV("DATETIME, MESSAGE: ", logEntry, "/LOG.csv");
}

  /* Send Message via Iridium Modem */
  int sendMsg(String msg, DateTime timestamp) {
    digitalWrite(IridSlpPin, HIGH);  // Power on the modem
    delay(2000);
    IridiumSerial.begin(19200);  // Start the serial port connected to the satellite modem
    logMessage(" - attempt to send message: " + msg);

    modem.setPowerProfile(IridiumSBD::USB_POWER_PROFILE);  // This is a low power application

    int status = modem.begin();
    if (status == ISBD_IS_ASLEEP) {
      logMessage(" - Modem asleep, wake up");
      status = modem.begin();
    }
    if (status == ISBD_SUCCESS) {
      logMessage(" - Modem begin successful"); 
    } else {
      logMessage(" - Modem begin unsuccessful"); 
      return status; 
    }

    // Send message
    logMessage(" - Sending...");
    status = modem.sendSBDText(msg.c_str());
    logMessage(" - Send response: " + status);

    if (status != ISBD_SUCCESS) {
      logMessage(" - Retry...");    
      status = modem.begin();
      if (status == ISBD_IS_ASLEEP) {
      logMessage("   - modem asleep, wake up");
      status = modem.begin();
      }
      modem.adjustSendReceiveTimeout(300);
      logMessage("   - Sending...");
      status = modem.sendSBDText(msg.c_str());
      logMessage("   - Send response: " + String(status));
    }

    // Sync clock
    struct tm t;
    int status_time = modem.getSystemTime(t);
    logMessage("  - Sync clocks... " + String(status_time));
    if (status_time == ISBD_SUCCESS) {
      String pre_time = rtc.now().timestamp();
      logMessage(" - Arduino Old Time: " + pre_time);
      logMessage("TIME FROM MODEM: " + String(t.tm_year) + "-" + String(t.tm_mon) + "-" + String(t.tm_mday) + " " + String(t.tm_hour) + ":" + String(t.tm_min));
      // rtc.adjust(DateTime(t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,  // This is the original offset
      //                    t.tm_hour, t.tm_min, t.tm_sec));
        rtc.adjust(DateTime(t.tm_year + 1911, t.tm_mon - 2, t.tm_mday + 4, // For some reason the offset has changed.
                          t.tm_hour + 4, t.tm_min - 9, t.tm_sec));

        // Check if time was set by reading it back
        DateTime new_time = rtc.now();
        if (new_time.year() >= 2025) {  // Sanity check on the year
            rtc_working = true;
            String post_time = new_time.timestamp();
            logMessage(" - Arduino New Time: " + post_time);
        } else {
            logMessage(" - Failed to set valid RTC time");
            rtc_working = false;
        }
    } else {
      logMessage(" - Clock was not synced");
    }

    modem.sleep();
    digitalWrite(IridSlpPin, LOW);  //Drive iridium power pin LOW
    delay(100);

    return status;
  }

  /* Sample Ultrasonic Sensor */
  void sampleUltrasonic(float &minDistance, float &maxDistance, float &medianDistance) {
      const int sampleCount = 10;
      float distances[sampleCount];

      // Collect multiple samples
      for (int i = 0; i < sampleCount; i++) {
          digitalWrite(triggerPin, HIGH);
          delay(30);
          distances[i] = pulseIn(pulsePin, HIGH);
          digitalWrite(triggerPin, LOW);
          delay(50); // Small delay between samples
      }
    minDistance = stats.minimum(distances, 10);  //Copute minimum distance
    maxDistance = stats.maximum(distances, 10);  //Copute maximum distance
    medianDistance = stats.median(distances, 10);  //Copute median distance
  }

  /* Sample SHT31 Sensor */
  String sampleSHT() {
    sht31.heater(0);
    float temperature = sht31.readTemperature();
    float humidity = sht31.readHumidity();
  return String(temperature) + "," + String(humidity);
  }

  /* Sample BMP390 Sensor */
  // String sampleBMP() {
  //     float temperature = bmp.readTemperature();
  //     float pressure = bmp.readPressure() / 100.0; // Convert to hPa
  //     return String(temperature) + "," + String(pressure);
  // }
  String sampleBMP() {
      // Re-initialize BMP390 after power cycle
      if (!bmp.begin_I2C()) {
          logMessage("BMP390 re-init failed");
          return "0,0";
      }
      // Configure sensor settings
      bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_8X);
      bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);
      // Perform one reading to clear out initial values
      if (!bmp.performReading()) {
          logMessage("BMP390 first reading failed");
          return "0,0";
      }
      delay(100);  // Give sensor time to stabilize
      // Take actual reading
      if (!bmp.performReading()) {
          logMessage("BMP390 second reading failed");
          return "0,0";
      }
      float temperature = bmp.readTemperature();
      float pressure = bmp.readPressure() / 100.0; // Convert to hPa
      return String(temperature) + "," + String(pressure);
  }
