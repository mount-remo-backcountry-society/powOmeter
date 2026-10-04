// ---- Timezone + Utilities stub ------------------------------------------
function nthSunday(y, m, n) { const d = new Date(Date.UTC(y, m, 1)); const first = (7 - d.getUTCDay()) % 7 + 1; return first + (n - 1) * 7; }
function tzOffsetH(tz, ms) {
  if (tz === 'UTC') return 0;
  if (tz === 'Etc/GMT+8') return -8;
  if (tz === 'America/Los_Angeles') {
    const y = new Date(ms).getUTCFullYear();
    const start = Date.UTC(y, 2, nthSunday(y, 2, 2), 10);   // 02:00 PST = 10:00 UTC
    const end = Date.UTC(y, 10, nthSunday(y, 10, 1), 9);    // 02:00 PDT = 09:00 UTC
    return (ms >= start && ms < end) ? -7 : -8;
  }
  throw new Error('tz ' + tz);
}
const pad = (n, w) => String(n).padStart(w || 2, '0');
const Utilities = {
  formatDate(d, tz, fmt) {
    const w = new Date(d.getTime() + tzOffsetH(tz, d.getTime()) * 3600000);
    const map = { yyyy: w.getUTCFullYear(), MM: pad(w.getUTCMonth() + 1), dd: pad(w.getUTCDate()), HH: pad(w.getUTCHours()), mm: pad(w.getUTCMinutes()), ss: pad(w.getUTCSeconds()) };
    return fmt.replace(/yyyy|MM|dd|HH|mm|ss/g, k => map[k]);
  }
};
const SS_TZ = 'America/Los_Angeles';
function wallToDate(wall) { let t = wall + 8 * 3600000; t = wall - tzOffsetH(SS_TZ, t) * 3600000; return new Date(t); }

// ---- Sheets mock: strings that look like datetimes become Dates, =NA() stays a formula
function Cell(v) {
  if (typeof v === 'string' && v.charAt(0) === '=') return { f: v, v: v === '=NA()' ? '#N/A' : v };
  if (typeof v === 'string') { const m = parseWallMsLocal(v); if (m !== null && /^\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$/.test(v)) return { v: wallToDate(m), f: '' }; }
  return { v: v, f: '' };
}
function parseWallMsLocal(s) { const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0) : null; }
function disp(c) { return c.v instanceof Date ? Utilities.formatDate(c.v, SS_TZ, 'yyyy-MM-dd HH:mm:ss') : String(c.v === null || c.v === undefined ? '' : c.v); }
function Sheet(name) { this.name = name; this.rows = []; this.maxRows = 1000; }
Sheet.prototype.getLastRow = function () { return this.rows.length; };
Sheet.prototype.getMaxRows = function () { return Math.max(this.maxRows, this.rows.length); };
Sheet.prototype.getRange = function (r, c, nr, nc) { return new Range(this, r, c, nr || 1, nc || 1); };
Sheet.prototype.getDataRange = function () { const nc = Math.max(1, ...this.rows.map(x => x.length)); return new Range(this, 1, 1, Math.max(1, this.rows.length), nc); };
Sheet.prototype.appendRow = function (vals) { this.rows.push(vals.map(Cell)); };
Sheet.prototype.clear = function () { this.rows = []; };
Sheet.prototype.setFrozenRows = function () {};
Sheet.prototype.deleteRows = function (start, n) { this.rows.splice(start - 1, n); this.maxRows -= n; };
Sheet.prototype.copyTo = function (ss) { const s = ss.insertSheet('copy'); s.rows = this.rows.map(r => r.slice()); return { setName: n => { s.name = n; ss.sheets[n] = s; delete ss.sheets['copy']; } }; };
function Range(s, r, c, nr, nc) { this.s = s; this.r = r; this.c = c; this.nr = nr; this.nc = nc; }
Range.prototype.cells = function (fn) { const out = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) { const R = this.s.rows[this.r - 1 + i]; const cell = R && R[this.c - 1 + j] ? R[this.c - 1 + j] : { v: '', f: '' }; row.push(fn(cell)); } out.push(row); } return out; };
Range.prototype.getValues = function () { return this.cells(c => c.v); };
Range.prototype.getFormulas = function () { return this.cells(c => c.f || ''); };
Range.prototype.getDisplayValues = function () { return this.cells(disp); };
Range.prototype.setValues = function (vals) { if (vals.length !== this.nr || vals[0].length !== this.nc) throw new Error('setValues dims ' + vals.length + 'x' + vals[0].length + ' vs ' + this.nr + 'x' + this.nc); for (let i = 0; i < this.nr; i++) { const idx = this.r - 1 + i; while (this.s.rows.length <= idx) this.s.rows.push([]); for (let j = 0; j < this.nc; j++) this.s.rows[idx][this.c - 1 + j] = Cell(vals[i][j]); } return this; };
Range.prototype.clearContent = function () { for (let i = 0; i < this.nr; i++) { const R = this.s.rows[this.r - 1 + i]; if (R) for (let j = 0; j < this.nc; j++) R[this.c - 1 + j] = { v: '', f: '' }; } while (this.s.rows.length && this.s.rows[this.s.rows.length - 1].every(c => c.v === '')) this.s.rows.pop(); };
const SS = { sheets: {}, getSheetByName(n) { return this.sheets[n] || null; }, insertSheet(n) { const s = new Sheet(n); this.sheets[n] = s; return s; }, getSpreadsheetTimeZone() { return SS_TZ; } };
const SpreadsheetApp = { openById() { return SS; } };
const LockService = { getScriptLock() { return { tryLock() { return true; }, releaseLock() {} }; } };
const console = { log() {}, error(...a) { ERRS.push(a.join(' ')); } };
const ERRS = [];
// ---- Gmail mock
function Msg(body, date) { this.body = body; this.date = date; this.unread = true; }
Msg.prototype.getPlainBody = function () { return this.body; };
Msg.prototype.getDate = function () { return this.date; };
Msg.prototype.isUnread = function () { return this.unread; };
Msg.prototype.markRead = function () { this.unread = false; };
function Thread(msgs) { this.msgs = msgs; this.archived = false; }
Thread.prototype.getMessages = function () { return this.msgs; };
Thread.prototype.isUnread = function () { return this.msgs.some(m => m.unread); };
Thread.prototype.refresh = function () { return this; };
Thread.prototype.moveToArchive = function () { this.archived = true; };
let THREADS = [];
const GmailApp = { search(q, start, max) { return THREADS.filter(t => t.isUnread()).slice(start, start + max); } };
