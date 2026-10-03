/* ===========================================================================
 * POW-O-METER — Google Apps Script ingest, v2 (2026-09-22)
 *
 * Replaces "Google Apps script.txt". The Data sheet keeps the same 9 columns,
 * so the Looker dashboard needs no change.
 *
 * What changed, and why (evidence in IMPROVEMENTS.md, Part 3):
 *   1. Every unread message is processed, not threads[0].getMessages()[0].
 *      Gmail groups RockBLOCK notifications into threads, and the old code
 *      always took the FIRST message of the first thread:
 *        - on an unparseable message (MOMSN 2941, "No Data") it returned
 *          without archiving, so the same message came back every 5 minutes
 *          from 2026-05-14 onwards;
 *        - on a good message it re-read an already-ingested one whenever the
 *          thread still held another unread message — the source of 8,473
 *          copies of one transmission in the Data sheet.
 *   2. Duplicates are detected by payload. A payload can only be ingested once,
 *      whatever the timezone or re-read, so the pipeline is idempotent.
 *   3. Times are written in fixed UTC-8 (true PST, matching the firmware),
 *      not America/Los_Angeles. See SHEET_TZ.
 *   4. Snow depth uses the Field row in force at the measurement time, not
 *      the last Field row, so re-processing cannot rewrite history.
 *   5. Dead SHT31 (-99.9 °C) is written as #N/A for temperature AND humidity,
 *      instead of -99.9 °C / 100 %.
 *   6. Errors sheet gets real errors only (was ~187k rows of progress notes).
 *   7. Resampling is O(n), and runs once per batch of new data, not per email.
 *   8. New "Messages" sheet: one row per RockBLOCK email, including "No Data"
 *      sessions, with a station-clock check. This is the remote health view
 *      for the v1.3 firmware.
 *   9. A script lock stops two 5-minute triggers from overlapping.
 *  10. (2026-09-29) Readings are timed from the station clock (DDHHMM header)
 *      when it agrees with the transmit time to within CLOCK_TRUST_MIN, and
 *      from the transmit time otherwise, so a failed station clock can never
 *      put readings days out. The Messages "Result" says which was used.
 *
 * One-off maintenance (run by hand from the editor or the Snow Station menu):
 *   dedupeDataSheet()  — backs up Data, then removes exact duplicate rows
 *   trimErrorsSheet()  — keeps the last 1,000 rows of Errors
 *   testParser()       — parses sample emails, writes nothing
 * =========================================================================== */

// Configuration
// Identifiers are kept out of the public source. Set them once in the Apps
// Script editor: Project Settings → Script Properties → STATION_IMEI and
// SPREADSHEET_ID. (The copy deployed before 2026-10 still has them inline;
// it is switched to this version outside the winter season.)
const SCRIPT_PROPS = PropertiesService.getScriptProperties();
const STATION_IMEI = SCRIPT_PROPS.getProperty('STATION_IMEI');
const SPREADSHEET_ID = SCRIPT_PROPS.getProperty('SPREADSHEET_ID');
const GMAIL_SEARCH_QUERY = 'from:' + STATION_IMEI + '@rockblock.rock7.com subject:"Message" is:unread';
const SHEET_NAME = 'Data';
const ERROR_SHEET_NAME = 'Errors';
const MESSAGES_SHEET_NAME = 'Messages';
const FIELD_SHEET_NAME = 'Field';
const RESAMPLED_SHEET_NAME = 'Resampled';

// Timezone for the "(PST)" columns. The firmware logs fixed UTC-8 all year, so
// the sheet does too. The old script used 'America/Los_Angeles', which moves
// to UTC-7 in summer. Tz-database sign convention: 'Etc/GMT+8' IS UTC-8.
const SHEET_TZ = 'Etc/GMT+8';
const DEVICE_UTC_OFFSET_H = -8;   // the firmware's DDHHMM header is UTC-8

// false = time readings from the station clock (DDHHMM header), falling back
// to the transmit time whenever that clock is off by more than CLOCK_TRUST_MIN.
// true  = always use the transmit time (the pre-2026-09-29 behaviour).
const USE_TRANSMIT_TIME = false;
const CLOCK_TRUST_MIN = 30;       // a healthy v1.3 clock reads within about ±3 min
// Hours between queued readings: 4 TPL5110 wake-ups of 998 s (median of 8,577
// gaps in the 2026-09-26 SD download). Was 1.12094444443937, which placed the
// last reading of a 5-reading bundle up to 2.6 min after its own transmission.
const HOUR_INTERVAL = 4 * 998 / 3600;

const MAX_THREADS_PER_RUN = 50;
const MAX_MESSAGES_PER_RUN = 100; // the rest are picked up by the next trigger
// "No echo": v1.3 sends 499, older firmware also sent 498 and 500. The old
// script's |d - 4.99| < 0.01 test caught all three, so this does too.
const NO_TARGET_MIN_CM = 498;
const NO_TARGET_MAX_CM = 500;
const SHT_DEAD_TEMP_X10 = -999;   // firmware sends -99.9 °C for a dead SHT31

// Battery voltage ranges
const BATTERY_RANGES = {
  0: '< 3.4V',
  1: '3.4-3.5V',
  2: '3.5-3.6V',
  3: '3.6-3.7V',
  4: '3.7-3.8V',
  5: '3.8-3.9V',
  6: '3.9-4.0V',
  7: '4.0-4.1V',
  8: '4.1-4.2V',
  9: '≥ 4.2V'
};

const DATA_HEADERS = [
  'Transmit Time (PST)',
  'Measurement Time (PST)',
  'Battery Level',
  'Battery Range',
  'Distance (m)',
  'Air Temperature (°C)',
  'Humidity (%)',
  'Original Message',
  'Snow Depth (m)'
];

const MESSAGES_HEADERS = [
  'Processed (UTC)',
  'MOMSN',
  'Transmit Time (UTC)',
  'Session Status',
  'CEP (km)',
  'Payload',
  'Readings',
  'Battery Level',
  'Station Clock Offset (min)',
  'Result'
];

/* ===========================================================================
 * Main entry point — run by the 5-minute trigger
 * =========================================================================== */

function checkRockBlockEmails() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;   // previous run still going

  try {
    const pending = collectUnreadMessages();
    if (pending.length === 0) return;

    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    const dataSheet = getOrCreateSheet(ss, SHEET_NAME, DATA_HEADERS);
    const messagesSheet = getOrCreateSheet(ss, MESSAGES_SHEET_NAME, MESSAGES_HEADERS);
    const seenPayloads = readIngestedPayloads(dataSheet);
    const fieldRefs = readFieldReferences(ss);

    const dataRows = [];
    const logRows = [];
    const processedAt = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HH:mm:ss');

    pending.forEach(item => {
      const email = parseEmail(item.message.getPlainBody());
      const log = [processedAt, email.momsn, email.transmitUtcString, email.sessionStatus,
                   email.cep, email.payload, '', '', '', ''];

      if (email.imei !== STATION_IMEI) {
        log[9] = 'skipped: IMEI ' + email.imei;
      } else if (!email.transmitTime) {
        log[9] = 'unparseable: no transmit time';
      } else if (!email.payload) {
        log[9] = 'no data (session status ' + email.sessionStatus + ')';
      } else {
        const parsed = parsePayload(email.payload);
        if (!parsed) {
          log[9] = 'unparseable payload';
        } else {
          log[6] = parsed.readings.length;
          log[7] = parsed.batteryLevel;
          log[8] = clockOffsetMinutes(parsed, email.transmitTime);

          if (seenPayloads.has(email.payload)) {
            log[9] = 'duplicate (already in Data)';
          } else {
            const built = buildDataRows(parsed, email.payload, email.transmitTime, fieldRefs);
            built.rows.forEach(r => dataRows.push(r));
            seenPayloads.add(email.payload);
            const bad = parsed.readings.filter(r => !r).length;
            log[9] = 'ok: ' + built.rows.length + ' rows' +
                     (bad ? ', ' + bad + ' bad readings skipped' : '') + ', ' + built.source;
          }
        }
      }
      logRows.push(log);
    });

    // Write first, mark read after. If a write fails the emails stay unread and
    // are retried next run; the payload check stops them being added twice.
    appendRows(dataSheet, dataRows);
    appendRows(messagesSheet, logRows);

    const threads = [];
    pending.forEach(item => {
      item.message.markRead();
      if (threads.indexOf(item.thread) < 0) threads.push(item.thread);
    });
    threads.forEach(thread => {
      if (!thread.refresh().isUnread()) thread.moveToArchive();
    });

    if (dataRows.length > 0) {
      try {
        resampleData();
      } catch (error) {
        logProcessingError(error, {action: 'resampleData'});
      }
    }
  } catch (error) {
    logProcessingError(error, {action: 'checkRockBlockEmails'});
  } finally {
    lock.releaseLock();
  }
}

/** Every unread RockBLOCK message, oldest first, capped per run. */
function collectUnreadMessages() {
  const threads = GmailApp.search(GMAIL_SEARCH_QUERY, 0, MAX_THREADS_PER_RUN);
  const items = [];
  threads.forEach(thread => {
    thread.getMessages().forEach(message => {
      if (message.isUnread()) items.push({thread: thread, message: message});
    });
  });
  items.sort((a, b) => a.message.getDate() - b.message.getDate());
  return items.slice(0, MAX_MESSAGES_PER_RUN);
}

/* ===========================================================================
 * Parsing — pure functions, no Google services except Utilities in formatting
 * =========================================================================== */

/**
 * RockBLOCK notification body:
 *   IMEI: ... / MOMSN: ... / Transmit Time: 2026-05-14T14:13:55Z UTC /
 *   Iridium CEP: 3.0 / Iridium Session Status: 0 / Data: <hex> / <ascii>
 * or "No Data" in place of the Data lines.
 */
function parseEmail(body) {
  const field = re => {
    const m = body.match(re);
    return m ? m[1].trim() : '';
  };
  const transmitUtcString = field(/Transmit Time:\s*(\S+?)Z/);
  const transmitTime = transmitUtcString ? new Date(transmitUtcString + 'Z') : null;

  // Prefer the hex Data field: it is exactly what the station sent. Fall back
  // to the decoded ASCII line the old script used.
  let payload = '';
  const hex = field(/Data:\s*([0-9a-fA-F]+)/);
  if (hex && hex.length % 2 === 0) {
    for (let i = 0; i < hex.length; i += 2) {
      payload += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
    }
    payload = payload.trim();
  }
  if (!payload && !/No Data/.test(body)) {
    const lines = body.split(/\r?\n/).map(l => l.trim()).filter(l => l);
    const last = lines.length ? lines[lines.length - 1] : '';
    if (/^\d{7}[-+\d:]+$/.test(last)) payload = last;
  }

  return {
    imei: field(/IMEI:\s*(\d+)/),
    momsn: field(/MOMSN:\s*(\d+)/),
    transmitUtcString: transmitUtcString ? transmitUtcString.replace('T', ' ') : '',
    transmitTime: (transmitTime && !isNaN(transmitTime)) ? transmitTime : null,
    sessionStatus: field(/Session Status:\s*(\d+)/),
    cep: field(/CEP:\s*([\d.]+)/),
    payload: payload
  };
}

/**
 * Payload: DDHHMMB then readings "DDD±TTTHH" separated by ':'
 *   DD HH MM = day/hour/minute of the FIRST reading, device clock (UTC-8)
 *   B        = battery bucket 0-9
 *   DDD      = distance in cm (498-500 = no echo)
 *   ±TTT     = air temperature x10 (-999 = dead SHT31)
 *   HH       = relative humidity (00 = 100 %)
 * A malformed reading is kept as null so the others keep their time slot.
 */
function parsePayload(payload) {
  const head = payload.match(/^(\d{2})(\d{2})(\d{2})(\d)(.+)$/);
  if (!head) return null;

  const readings = head[5].split(':').map(s => {
    const m = s.trim().match(/^(\d{3})([+-])(\d{3})(\d{2})$/);
    if (!m) return null;
    const cm = parseInt(m[1], 10);
    const tempX10 = (m[2] === '-' ? -1 : 1) * parseInt(m[3], 10);
    const rh = parseInt(m[4], 10);
    const shtDead = tempX10 === SHT_DEAD_TEMP_X10;
    return {
      distance: (cm >= NO_TARGET_MIN_CM && cm <= NO_TARGET_MAX_CM) ? null : cm / 100,
      temperature: shtDead ? null : tempX10 / 10,
      humidity: shtDead ? null : (rh === 0 ? 100 : rh)
    };
  });
  if (readings.every(r => r === null)) return null;

  return {
    day: parseInt(head[1], 10),
    hour: parseInt(head[2], 10),
    minute: parseInt(head[3], 10),
    batteryLevel: parseInt(head[4], 10),
    readings: readings
  };
}

/** Header DDHHMM as a UTC instant, resolved to the month nearest `nearMs`. */
function headerTimeMs(parsed, nearMs) {
  if (parsed.day < 1 || parsed.day > 31 || parsed.hour > 23 || parsed.minute > 59) return null;
  const offsetMs = DEVICE_UTC_OFFSET_H * 3600 * 1000;
  const near = new Date(nearMs + offsetMs);  // device wall clock, held in UTC fields
  let best = null;
  [-1, 0, 1].forEach(dm => {
    const wall = Date.UTC(near.getUTCFullYear(), near.getUTCMonth() + dm,
                          parsed.day, parsed.hour, parsed.minute);
    const t = wall - offsetMs;
    if (best === null || Math.abs(t - nearMs) < Math.abs(best - nearMs)) best = t;
  });
  return best;
}

/**
 * Station clock check: header time of the first reading minus the time the
 * transmit-time method assigns it. A healthy v1.3 clock reads within about
 * ±30 min (the reading interval is not exact). Hours or days = clock fault.
 */
function clockOffsetMinutes(parsed, transmitTime) {
  const expected = transmitTime.getTime() -
    (parsed.readings.length - 1) * HOUR_INTERVAL * 3600 * 1000;
  const header = headerTimeMs(parsed, expected);
  return header === null ? '' : Math.round((header - expected) / 60000);
}

/**
 * Measurement instants, one per reading slot, and which clock they came from.
 * The station clock is used only when it agrees with the transmit time to
 * within CLOCK_TRUST_MIN; otherwise the transmit time is, as before v2.
 */
function measurementTimes(parsed, transmitTime) {
  const n = parsed.readings.length;
  const stepMs = HOUR_INTERVAL * 3600 * 1000;
  const fromTransmit = () =>
    parsed.readings.map((_, i) => transmitTime.getTime() - (n - 1 - i) * stepMs);

  if (USE_TRANSMIT_TIME) return {times: fromTransmit(), source: 'transmit time'};

  const offset = clockOffsetMinutes(parsed, transmitTime);
  if (offset === '' || Math.abs(offset) > CLOCK_TRUST_MIN) {
    return {times: fromTransmit(),
            source: 'transmit time (station clock ' +
                    (offset === '' ? 'unreadable' : 'off by ' + offset + ' min') + ')'};
  }
  const first = headerTimeMs(parsed, transmitTime.getTime() - (n - 1) * stepMs);
  return {times: parsed.readings.map((_, i) => first + i * stepMs), source: 'station time'};
}

function buildDataRows(parsed, payload, transmitTime, fieldRefs) {
  const timed = measurementTimes(parsed, transmitTime);
  const times = timed.times;
  const transmitStr = formatSheetTime(transmitTime.getTime());
  const rows = [];

  parsed.readings.forEach((r, i) => {
    if (!r || times[i] === null || isNaN(times[i])) return;
    const measStr = formatSheetTime(times[i]);
    const ref = fieldReferenceAt(fieldRefs, parseWallMs(measStr));
    const snowDepth = (r.distance !== null && ref) ? ref.sensorHeight - r.distance : null;

    rows.push([
      transmitStr,
      measStr,
      parsed.batteryLevel,
      BATTERY_RANGES[parsed.batteryLevel],
      naIfNull(r.distance),
      naIfNull(r.temperature),
      naIfNull(r.humidity),
      payload,
      naIfNull(snowDepth)
    ]);
  });
  return {rows: rows, source: timed.source};
}

/* ===========================================================================
 * Time helpers
 *
 * The sheet holds wall-clock strings in SHEET_TZ. Sheets turns them into
 * datetimes, and getValues() hands them back as Dates in the SPREADSHEET's
 * timezone, which may differ. To compare anything, every time is converted to
 * "wall ms": the wall-clock reading parsed as if it were UTC.
 * =========================================================================== */

function formatSheetTime(ms) {
  return Utilities.formatDate(new Date(ms), SHEET_TZ, 'yyyy-MM-dd HH:mm:ss');
}

function parseWallMs(s) {
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0);
}

function cellWallMs(value, spreadsheetTz) {
  if (value instanceof Date) {
    return isNaN(value) ? null :
      parseWallMs(Utilities.formatDate(value, spreadsheetTz, 'yyyy-MM-dd HH:mm:ss'));
  }
  return typeof value === 'string' ? parseWallMs(value) : null;
}

function formatWallMs(wallMs) {
  return Utilities.formatDate(new Date(wallMs), 'UTC', 'yyyy-MM-dd HH:mm:ss');
}

function naIfNull(v) {
  return (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) ? '=NA()' : v;
}

/* ===========================================================================
 * Sheet helpers
 * =========================================================================== */

function getOrCreateSheet(ss, sheetName, headers) {
  let sheet = ss.getSheetByName(sheetName);
  if (!sheet) {
    sheet = ss.insertSheet(sheetName);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function appendRows(sheet, rows) {
  if (rows.length === 0) return;
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
}

/** Set of every payload already in the Data sheet (column H). */
function readIngestedPayloads(sheet) {
  const seen = new Set();
  const n = sheet.getLastRow() - 1;
  if (n < 1) return seen;
  sheet.getRange(2, 8, n, 1).getValues().forEach(r => {
    if (r[0] !== '') seen.add(String(r[0]).trim());
  });
  return seen;
}

/** Field sheet rows as {wallMs, sensorHeight}, oldest first. */
function readFieldReferences(ss) {
  const sheet = ss.getSheetByName(FIELD_SHEET_NAME);
  if (!sheet) {
    logProcessingError(new Error('Field sheet not found'));
    return [];
  }
  const tz = ss.getSpreadsheetTimeZone();
  return sheet.getDataRange().getValues().slice(1)
    .map(r => ({wallMs: cellWallMs(r[0], tz), sensorHeight: r[5]}))
    .filter(f => f.wallMs !== null && typeof f.sensorHeight === 'number')
    .sort((a, b) => a.wallMs - b.wallMs);
}

/** The Field row in force at `wallMs`: the latest one at or before it. */
function fieldReferenceAt(refs, wallMs) {
  let ref = null;
  for (let i = 0; i < refs.length && refs[i].wallMs <= wallMs; i++) ref = refs[i];
  return ref;
}

function logProcessingError(error, context) {
  try {
    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    const sheet = getOrCreateSheet(ss, ERROR_SHEET_NAME, ['Timestamp', 'Error', 'Context']);
    const detail = JSON.stringify(Object.assign({}, context || {}, {stack: error.stack}), null, 2);
    sheet.appendRow([new Date(), error.toString(), detail.substr(0, 5000)]);
    console.error(error.toString(), detail);
  } catch (e) {
    console.error('Error in logProcessingError:', e);
  }
}

/* ===========================================================================
 * Resampling to an hourly grid (linear, gaps > 2 h left as #N/A)
 * =========================================================================== */

function resampleData() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sourceSheet = ss.getSheetByName(SHEET_NAME);
  const tz = ss.getSpreadsheetTimeZone();
  const data = sourceSheet.getDataRange().getValues();
  const headers = data[0];

  const columnsToInterpolate = [4, 5, 6, 8]; // Distance, Temperature, Humidity, Snow Depth
  const MAX_GAP = 2 * 60 * 60 * 1000;
  const HOUR = 60 * 60 * 1000;

  const points = data.slice(1)
    .map(r => ({t: cellWallMs(r[1], tz), row: r}))
    .filter(p => p.t !== null)
    .sort((a, b) => a.t - b.t);

  let resampledSheet = ss.getSheetByName(RESAMPLED_SHEET_NAME);
  if (!resampledSheet) {
    resampledSheet = ss.insertSheet(RESAMPLED_SHEET_NAME);
  } else {
    resampledSheet.clear();
  }
  resampledSheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (points.length === 0) return;

  const num = v => (typeof v === 'number' && !isNaN(v)) ? v : null;
  const naRow = t => headers.map((_, i) => i === 1 ? formatWallMs(t) : '=NA()');

  const out = [];
  let j = 0;  // index of the first point at or after t
  const start = Math.floor(points[0].t / HOUR) * HOUR;
  const end = points[points.length - 1].t;

  for (let t = start; t <= end; t += HOUR) {
    while (j < points.length && points[j].t < t) j++;
    const after = points[j];
    const before = j > 0 ? points[j - 1] : null;

    if (!before || !after || after.t - before.t > MAX_GAP) {
      out.push(naRow(t));
      continue;
    }
    const span = after.t - before.t;
    out.push(headers.map((_, col) => {
      if (col === 1) return formatWallMs(t);
      if (columnsToInterpolate.indexOf(col) < 0) return '=NA()';
      const y1 = num(before.row[col]);
      const y2 = num(after.row[col]);
      if (y1 === null || y2 === null) return '=NA()';
      return span === 0 ? y2 : y1 + (t - before.t) * (y2 - y1) / span;
    }));
  }

  resampledSheet.getRange(2, 1, out.length, headers.length).setValues(out);
}

/* ===========================================================================
 * One-off maintenance — run by hand
 * =========================================================================== */

/**
 * Copies Data to "Data backup yyyy-MM-dd", then rewrites Data with exact
 * duplicate rows removed (same transmit time, measurement time and payload).
 * Row order is kept. Formulas such as =NA() are preserved.
 */
function dedupeDataSheet() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(SHEET_NAME);
  const range = sheet.getDataRange();
  const values = range.getValues();
  const formulas = range.getFormulas();
  const display = range.getDisplayValues();

  const backupName = 'Data backup ' + Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HHmm');
  sheet.copyTo(ss).setName(backupName);

  const seen = new Set();
  const keep = [];
  for (let i = 1; i < values.length; i++) {
    const key = display[i][0] + '|' + display[i][1] + '|' + display[i][7];
    if (seen.has(key)) continue;
    seen.add(key);
    keep.push(values[i].map((v, c) => formulas[i][c] ? formulas[i][c] : v));
  }

  const removed = values.length - 1 - keep.length;
  sheet.getRange(2, 1, values.length - 1, values[0].length).clearContent();
  if (keep.length > 0) sheet.getRange(2, 1, keep.length, keep[0].length).setValues(keep);
  if (sheet.getMaxRows() > keep.length + 1) {
    sheet.deleteRows(keep.length + 2, sheet.getMaxRows() - keep.length - 1);
  }
  resampleData();

  const msg = 'Removed ' + removed + ' duplicate rows, kept ' + keep.length +
              '. Backup: "' + backupName + '".';
  console.log(msg);
  return msg;
}

/** Keeps the last `keep` rows of the Errors sheet. */
function trimErrorsSheet(keep) {
  keep = keep || 1000;
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(ERROR_SHEET_NAME);
  if (!sheet) return;
  const excess = sheet.getLastRow() - 1 - keep;
  if (excess > 0) sheet.deleteRows(2, excess);
  console.log('Deleted ' + Math.max(excess, 0) + ' rows from ' + ERROR_SHEET_NAME);
}

/** Parses real notification bodies and logs the result. Writes nothing. */
function testParser() {
  const samples = [
    '\r\nIMEI: ' + STATION_IMEI + '\r\nMOMSN: 2877\r\nTransmit Time: 2026-05-14T14:13:55Z UTC\r\nIridium Latitude: 54.5167\r\nIridium Longitude: -128.9590\r\nIridium CEP: 3.0\r\nIridium Session Status: 0\r\nData: 313230323533333236382b30303739363a3237302b30303239363a3237312b30303139353a3237312b3030333935\r\n\r\n1202533268+00796:270+00296:271+00195:271+00395\r\n\r\n',
    '\r\nIMEI: ' + STATION_IMEI + '\r\nMOMSN: 2941\r\nTransmit Time: 2026-05-23T23:17:41Z UTC\r\nIridium Latitude: 54.5028\r\nIridium Longitude: -128.3029\r\nIridium CEP: 680.0\r\nIridium Session Status: 13\r\nNo Data\r\n'
  ];
  samples.forEach(body => {
    const email = parseEmail(body);
    const parsed = email.payload ? parsePayload(email.payload) : null;
    console.log(JSON.stringify({
      email: email,
      parsed: parsed,
      clockOffsetMin: parsed ? clockOffsetMinutes(parsed, email.transmitTime) : null,
      built: parsed ? buildDataRows(parsed, email.payload, email.transmitTime, []) : null
    }, null, 2));
  });
}

/* ===========================================================================
 * Triggers and menu
 * =========================================================================== */

function createTrigger() {
  const triggers = ScriptApp.getProjectTriggers();
  triggers.forEach(trigger => ScriptApp.deleteTrigger(trigger));

  ScriptApp.newTrigger('checkRockBlockEmails')
    .timeBased()
    .everyMinutes(5)
    .create();
}

// Add menu item to spreadsheet
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('Snow Station')
    .addItem('Resample Data', 'resampleData')
    .addItem('Remove duplicate Data rows (backs up first)', 'dedupeDataSheet')
    .addItem('Trim Errors sheet to last 1,000 rows', 'trimErrorsSheet')
    .addToUi();
}
