/* POW-O-METER station page.
 *
 * Reads the published files in v1/ (and webcams/) and draws the page. No
 * cookies, no tracking. The only outside requests are the webcams' newest
 * images, loaded from Shames Mountain's own address once the webcams are
 * switched on.
 *
 * Safety rules (design R6):
 *  - every value shows its own age, worked out here in the browser, so a
 *    stopped pipeline still shows as old data; a warning line appears when
 *    the data are late or stale, or processing has stopped;
 *  - only values from the CURRENT site appear under "Now" (latest.json also
 *    holds SD-card-only values from older visits and the old site);
 *  - times are shown as BC time, a FIXED UTC-7 (BC dropped clock changes;
 *    phones with old time-zone data would show America/Vancouver 1 h off
 *    from 2026-11-01). Files stay UTC.
 *
 * Pure functions are in POW (tested by selftest.html); rendering follows.
 */
(function () {
  "use strict";

  // ---- settings ----------------------------------------------------------
  const BC_OFFSET_H = -7;                  // BC time = UTC-7 all year
  const BC_LABEL = "BC time (UTC−7)";
  const FRESH_H = 6;                       // radio sends 7x a day; longest normal gap ~5 h
  const STALE_H = 12;                      // = the no-message alarm (config/monitoring.yaml)
  const BUILD_STALE_H = 3;                 // publish runs every 5 min (GitHub skips some)
  const CHART_DAYS = [1, 3, 7, 30];        // the range buttons; recent.csv holds 30 days
  const DEFAULT_DAYS = 3;
  const MIN_DEPTH_CM = 15;                // shallower shows as "Under 15 cm": bare-ground readings
                                           // scatter -14 to +7 cm, and thin snow squashes the heather
  const TABLE_HOURS = 24;
  const GAP_H = 6;                         // break a chart line across a longer gap
  const HOVER_H = 2;                       // hover: a station's reading within this of the pointer
  const HOVER_KEEP_S = 3;                  // after the finger lifts, the values stay this long
  const CHANGE_HOURS = [24, 48, 72];       // depth change under the snow depth (avalanche standard)
  const CHANGE_TOL_H = 3;                  // its start: the reading nearest that time, within this
                                           // (the radio sends about every 3.4 h)
  const REFRESH_MIN = 10;
  const REOPEN_MIN = 2;                    // back on screen after longer than this: refresh
  const CAM_REFRESH_MIN = 5;               // newest webcam image: fetched afresh at most this often
  const CAM_OFF_H = 24;                    // no new webcam image for longer: "the camera may be off"
                                           // (longer than a night, when a camera may rest)
  const CHART_QUALITIES = ["good", "estimate"];   // suspect values are left out of charts
  const STATION = "powometer";
  const OWN_NAME = "Shames POW-O-METER";
  const REPO = "https://github.com/mount-remo-backcountry-society/powOmeter";
  const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4"];

  const H = 3600e3;
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const pad = (n) => String(n).padStart(2, "0");

  // ---- pure functions (tested in selftest.html) --------------------------
  const POW = {
    BC_OFFSET_H, FRESH_H, STALE_H, MIN_DEPTH_CM,

    /** CSV text -> array of objects (the published files never quote fields). */
    parseCSV(text) {
      const lines = text.split("\n").filter((l) => l.length);
      if (!lines.length) return [];
      const head = lines[0].split(",");
      return lines.slice(1).map((l) => {
        const v = l.split(",");
        const o = {};
        head.forEach((h, i) => { o[h] = v[i] === undefined ? "" : v[i]; });
        return o;
      });
    },

    ms(iso) { return Date.parse(iso); },

    /** BC-time parts of a UTC instant (ms). */
    bc(ms) {
      const d = new Date(ms + BC_OFFSET_H * H);
      return { dow: DAYS[d.getUTCDay()], day: d.getUTCDate(), mon: MONTHS[d.getUTCMonth()],
               year: d.getUTCFullYear(), hh: pad(d.getUTCHours()), mm: pad(d.getUTCMinutes()) };
    },
    /** "Fri 2 Oct" */
    fmtDay(ms) { const b = POW.bc(ms); return `${b.dow} ${b.day} ${b.mon}`; },
    /** "Fri 2 Oct, 14:05" */
    fmtDateTime(ms) { const b = POW.bc(ms); return `${b.dow} ${b.day} ${b.mon}, ${b.hh}:${b.mm}`; },
    /** "14:05" */
    fmtClock(ms) { const b = POW.bc(ms); return `${b.hh}:${b.mm}`; },
    /** BC times (ms) from a to b, every stepH hours from BC midnight. */
    ticks(a, b, stepH = 24) {
      const off = BC_OFFSET_H * H, step = stepH * H;
      let t = Math.ceil((a + off) / step) * step - off;
      const out = [];
      for (; t <= b; t += step) out.push(t);
      return out;
    },
    /** BC midnights (ms) from a to b. */
    midnights(a, b) { return POW.ticks(a, b, 24); },
    /** A time-axis label: "Fri 2 Oct" at midnight, else "06:00". */
    tickText(ms) {
      const b = POW.bc(ms);
      return b.hh === "00" && b.mm === "00" ? POW.fmtDay(ms) : POW.fmtClock(ms);
    },

    ageHours(iso, nowMs) { return (nowMs - POW.ms(iso)) / H; },
    /** "just now", "40 min ago", "3 h ago", "2 days ago" */
    ageText(hours) {
      if (hours < 0.05) return "just now";
      if (hours < 1) return `${Math.round(hours * 60)} min ago`;
      if (hours < 48) return `${Math.round(hours)} h ago`;
      return `${Math.round(hours / 24)} days ago`;
    },
    /** "fresh" | "late" | "stale" */
    freshness(hours) { return hours <= FRESH_H ? "fresh" : hours <= STALE_H ? "late" : "stale"; },

    /** Snow depth for the chart: below zero shows as 0 (zero is the top of
     *  the heather). Files keep the real value. */
    displayDepth(cm) { return Math.max(0, Math.round(cm)); },
    /** Snow depth for the casual view: "Under 15 cm" below MIN_DEPTH_CM, where
     *  the sensor cannot tell snow from scatter over the heather. "More ->
     *  Detailed readings" shows the measured value. */
    depthText(cm) {
      const v = Math.round(cm);
      return v < MIN_DEPTH_CM ? `Under ${MIN_DEPTH_CM} cm` : `${v} cm`;
    },
    fmtTemp(c) { return (c < 0 ? "−" : "") + Math.abs(c).toFixed(1) + " °C"; },
    /** The webcam's newest-image address with a number that changes every
     *  CAM_REFRESH_MIN, so browsers fetch it afresh (Shames' server lets them
     *  keep a copy for an hour), while visitors in the same few minutes still
     *  share one copy. */
    freshUrl(url, nowMs) {
      return url + (url.includes("?") ? "&" : "?") + "t=" + Math.floor(nowMs / (CAM_REFRESH_MIN * 60e3));
    },
    /** A line for a webcam whose newest image is older than CAM_OFF_H, else null. */
    camNote(latestIso, nowMs) {
      if (!latestIso || POW.ageHours(latestIso, nowMs) <= CAM_OFF_H) return null;
      return `No new image since ${POW.fmtDateTime(POW.ms(latestIso))} BC: the camera may be off.`;
    },
    /** "Updated just now", "Updated 3 min ago": when this phone last fetched the files */
    updatedText(fetchedMs, nowMs) { return "Updated " + POW.ageText((nowMs - fetchedMs) / H); },
    /** 250 -> "WSW" */
    compass(deg) { return COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]; },
    /** "Kasiks High (1,435 m)"; parentheses in a site name become a comma */
    stationLabel(name, elev) {
      const n = name.replace(/\s*\((.*)\)\s*$/, ", $1");
      return elev ? `${n} (${Number(elev).toLocaleString("en-CA")} m)` : n;
    },

    /** The current site of a station: the one with no end date. */
    currentSite(sites, station = STATION) {
      const s = (sites.sites || []).find((x) => x.station === station && !x.until_utc);
      return s ? s.id : null;
    },

    /** A value from latest.json for the "Now" tiles, or null. Only values
     *  from the current site; older than STALE_H they are marked stale. */
    nowValue(latest, variable, site, nowMs) {
      const v = latest && latest.values && latest.values[variable];
      if (!v || v.value === null || v.site_id !== site) return null;
      const age = POW.ageHours(v.time_utc, nowMs);
      return { value: v.value, ms: POW.ms(v.time_utc), age, state: POW.freshness(age),
               quality: v.quality, approval: v.approval };
    },

    /** Readings of the current site in [fromMs, toMs], grouped by time,
     *  newest first: [{ms, snow_depth, air_temperature, relative_humidity, rows}] */
    readings(rows, site, fromMs, toMs) {
      const by = new Map();
      for (const r of rows) {
        if (r.site_id !== site || r.station_id !== STATION) continue;
        const t = POW.ms(r.time_utc);
        if (t < fromMs || t > toMs) continue;
        if (!by.has(t)) by.set(t, { ms: t, rows: [] });
        const e = by.get(t);
        e.rows.push(r);
        if (r.value !== "" && (r.variable !== "snow_depth" || r.statistic === "median")) e[r.variable] = Number(r.value);
      }
      return [...by.values()].filter((e) => e.snow_depth !== undefined || e.air_temperature !== undefined)
        .sort((a, b) => b.ms - a.ms);
    },

    /** Chart series per station: {station: [{x, y}]}. A null point breaks the
     *  line across gaps longer than GAP_H and where the site changes (snow
     *  depth is never joined across sites). POW-O-METER's snow depth uses
     *  the median of the burst. */
    series(rows, variable, fromMs, opts = {}) {
      const out = {}, lastSite = {};
      const sorted = rows.filter((r) => r.variable === variable && r.value !== "" &&
          CHART_QUALITIES.includes(r.quality) && POW.ms(r.time_utc) >= fromMs &&
          !(r.station_id === STATION && variable === "snow_depth" && r.statistic !== "median"))
        .sort((a, b) => POW.ms(a.time_utc) - POW.ms(b.time_utc));
      for (const r of sorted) {
        const k = r.station_id, x = POW.ms(r.time_utc);
        let y = Number(r.value);
        if (opts.transform) y = opts.transform(y);
        const s = (out[k] = out[k] || []);
        if (s.length && (x - s[s.length - 1].x > GAP_H * H || lastSite[k] !== r.site_id)) s.push({ x: x - 1, y: null });
        lastSite[k] = r.site_id;
        s.push({ x, y });
      }
      return out;
    },

    /** Change in POW-O-METER's snow depth (cm) over `hours` up to its newest
     *  reading at the site, or null: when no reading lies within CHANGE_TOL_H
     *  of the start, or either end is under MIN_DEPTH_CM (not a depth yet).
     *  A change in depth, not new snow: new snow settles, and wind moves it. */
    depthChange(rows, site, hours) {
      const pts = rows.filter((r) => r.station_id === STATION && r.site_id === site && r.variable === "snow_depth" &&
          r.statistic === "median" && r.value !== "" && CHART_QUALITIES.includes(r.quality))
        .map((r) => ({ ms: POW.ms(r.time_utc), cm: Number(r.value) }));
      if (!pts.length) return null;
      const end = pts.reduce((a, p) => (p.ms > a.ms ? p : a));
      const t = end.ms - hours * H;
      const start = pts.reduce((a, p) => (Math.abs(p.ms - t) < Math.abs(a.ms - t) ? p : a));
      if (Math.abs(start.ms - t) > CHANGE_TOL_H * H) return null;
      if (Math.round(start.cm) < MIN_DEPTH_CM || Math.round(end.cm) < MIN_DEPTH_CM) return null;
      return Math.round(end.cm) - Math.round(start.cm);
    },
    /** "+18 cm", "−5 cm", "0 cm"; "–" for no value */
    changeText(cm) {
      if (cm === null || cm === undefined) return "–";
      return (cm > 0 ? "+" : cm < 0 ? "−" : "") + Math.abs(cm) + " cm";
    },

    /** For hovering: in each series, the index of the point nearest to x
     *  (within HOVER_H), or -1. */
    nearestEach(seriesList, x) {
      return seriesList.map((data) => {
        let best = -1, bd = Infinity;
        for (let i = 0; i < data.length; i++) {
          if (data[i].y === null) continue;
          const d = Math.abs(data[i].x - x);
          if (d < bd) { bd = d; best = i; }
        }
        return bd <= HOVER_H * H ? best : -1;
      });
    },

    /** Plain-words label for a reading's labels. */
    labelText(r) {
      const parts = [];
      parts.push(r.approval === "approved" ? "Approved" : "Provisional");
      if (r.quality && r.quality !== "good") parts.push(r.quality);
      if (r.qualifiers) parts.push(r.qualifiers.split(";").join(", "));
      return parts.join(" · ");
    },

    /** Battery level text from status.json. */
    batteryText(st) {
      if (st.battery_bucket === null || st.battery_bucket === undefined) return "unknown";
      const up = st.battery_bucket_upper_v;
      if (st.battery_bucket === 0) return "below 3.4 V (level 0)";
      if (up === null || up === undefined) return `4.2 V or more (level ${st.battery_bucket})`;
      return `${(up - 0.1).toFixed(1)}–${up.toFixed(1)} V (level ${st.battery_bucket})`;
    },
  };
  window.POW = POW;

  // ---- rendering -----------------------------------------------------------
  if (!document.getElementById("app")) return;          // selftest.html loads only the functions

  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
    }
    for (const k of kids) if (k !== null && k !== undefined) e.append(k);
    return e;
  };
  const link = (href, text) => el("a", { href, text, target: "_blank", rel: "noopener" });
  const getJSON = (u) => fetch(u, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(u); return r.json(); });
  const getText = (u) => fetch(u, { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(u); return r.text(); });
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const store = {          // remembers the ticked stations and the time range, in this browser only
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  const state = { latest: null, status: null, sites: null, recent: [], site: null,
                  near: { stations: {} }, nearRows: [], webcams: null, stations: [], hidden: new Set(),
                  charts: {}, days: DEFAULT_DAYS, fetchedMs: null, camLive: [] };

  // -- stations: POW-O-METER plus the published nearby stations ------------
  function stationList() {
    const sites = state.sites.sites || [], stations = state.sites.stations || [];
    const siteOf = (sid) => sites.find((s) => s.station === sid && !s.until_utc) || {};
    const ids = [STATION, ...Object.keys(state.near.stations || {}).sort()];
    return ids.map((sid, i) => {
      const site = siteOf(sid), st = stations.find((s) => s.id === sid) || {};
      const name = sid === STATION ? OWN_NAME : site.name || st.name || sid;
      return { id: sid, label: POW.stationLabel(name, site.elevation_m), elev: site.elevation_m,
               color: css(SERIES[i % SERIES.length]), operator: st.operator, licence: st.licence };
    }).sort((a, b) => (b.elev || 0) - (a.elev || 0));
  }

  // -- warning line (only when something is wrong) --
  function renderBanner(now) {
    const b = $("banner");
    const st = state.status.stations[STATION];
    const lastObs = st.last_observation_utc;
    const built = POW.ageHours(state.status.built_utc, now);
    const age = lastObs ? POW.ageHours(lastObs, now) : Infinity;
    const f = POW.freshness(age);
    b.replaceChildren();
    b.hidden = false;
    if (built > BUILD_STALE_H) {
      b.className = "banner stale";
      b.append(el("strong", { text: "The data processing has stopped. " }),
        `These files were built ${POW.ageText(built)}, so everything below may be older than it looks.`);
    } else if (f === "stale") {
      b.className = "banner stale";
      b.append(el("strong", { text: "No recent data: don’t rely on these values. " }),
        lastObs ? `The latest reading is from ${POW.fmtDateTime(POW.ms(lastObs))} (${POW.ageText(age)}).` : "");
    } else if (f === "late") {
      b.className = "banner late";
      b.append(el("strong", { text: "Later than usual. " }),
        `The latest reading is ${POW.ageText(age)} old; the station sends 7 times a day, so this may only be a delay.`);
    } else {
      b.hidden = true;
    }
  }

  // -- Now tiles --
  function tile(id, label, v, fmt) {
    const t = $(id);
    t.replaceChildren();
    t.className = "tile" + (v ? " " + v.state : "");
    t.append(el("div", { class: "tile-label", text: label }));
    if (!v) {
      t.append(el("div", { class: "tile-value", text: "–" }), el("div", { class: "tile-age", text: "no current reading" }));
      return;
    }
    t.append(el("div", { class: "tile-value", text: fmt(v.value) }),
      el("div", { class: "tile-age", text: `${POW.ageText(v.age)} · ${POW.fmtDateTime(v.ms)}` +
        (v.state === "stale" ? " · not current" : v.state === "late" ? " · older than usual" : "") }));
  }
  // under the snow depth: its change over 24, 48 and 72 h (not with old data, nor before there is snow)
  function depthChanges(v) {
    if (!v || v.state === "stale") return;
    const ch = CHANGE_HOURS.map((h) => [h, POW.depthChange(state.recent, state.site, h)]);
    if (ch.every(([, cm]) => cm === null)) return;
    $("now-depth").append(el("div", { class: "tile-change" },
      el("div", { class: "tile-label", text: "Depth change" }),
      el("div", { class: "changes" }, ...ch.map(([h, cm]) => el("div", {},
        el("div", { class: "tile-age", text: `${h} h` }), el("div", { class: "change-value", text: POW.changeText(cm) }))))));
  }
  function renderTiles(now) {
    const depth = POW.nowValue(state.latest, "snow_depth", state.site, now);
    tile("now-depth", "Snow depth", depth, POW.depthText);
    depthChanges(depth);
    tile("now-temp", "Air temperature", POW.nowValue(state.latest, "air_temperature", state.site, now), POW.fmtTemp);
    tile("now-rh", "Humidity", POW.nowValue(state.latest, "relative_humidity", state.site, now), (v) => `${Math.round(v)}%`);
  }

  // -- station tick-boxes (one row above the charts, for all charts) --
  function renderPicker() {
    const box = $("station-picker");
    box.replaceChildren();
    box.hidden = state.stations.length < 2;
    for (const s of state.stations) {
      const cb = el("input", { type: "checkbox", value: s.id });
      cb.checked = !state.hidden.has(s.id);
      cb.addEventListener("change", () => {
        if (cb.checked) state.hidden.delete(s.id); else state.hidden.add(s.id);
        store.set("powometer.hidden", [...state.hidden]);
        renderCharts(Date.now());
      });
      const key = el("span", { class: "key line" }); key.style.background = s.color;
      box.append(el("label", { class: "pick" }, cb, key, s.label));
    }
  }

  // -- charts --
  // fmt: the value in the title line while a chart is touched (the unit is in the title)
  const CHARTS = [
    // "under 15 cm" is about POW-O-METER's sensor over the heather; other stations show their value
    { id: "chart-depth", variable: "snow_depth", fmt: (v) => `${v} cm`,
      ownFmt: (v) => (v < MIN_DEPTH_CM ? `under ${MIN_DEPTH_CM} cm` : `${v} cm`), axis: (v) => `${v}`,
      transform: POW.displayDepth, y: { min: 0, suggestedMax: 50 }, shallow: true },
    { id: "chart-temp", variable: "air_temperature", fmt: (v) => (v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "°",
      axis: (v) => `${v}°`, freezing: true },
    { id: "chart-wind", variable: "wind_speed", fmt: (v) => `${Math.round(v)} km/h`, axis: (v) => `${v}`,
      y: { min: 0, suggestedMax: 20 }, direction: true },
    { id: "chart-rh", variable: "relative_humidity", fmt: (v) => `${Math.round(v)}%`, axis: (v) => `${v}%`,
      y: { min: 0, max: 100 } },
  ];

  // Touching or pointing at a chart: every chart's title line shows, for each
  // station, the reading nearest that time, with a hairline at the time and
  // the time itself on the time axis. Nothing covers the plot (no callouts).
  function showAt(x) {
    for (const def of CHARTS) {
      const chart = state.charts[def.id], head = $(def.id + "-head");
      if (!chart) continue;
      chart.$hoverX = x;
      const idx = x === null ? [] : POW.nearestEach(chart.data.datasets.map((d, i) => chart.isDatasetVisible(i) ? d.data : []), x);
      chart.setActiveElements(idx.map((index, datasetIndex) => ({ datasetIndex, index })).filter((a) => a.index >= 0));
      chart.update("none");
      head.classList.toggle("reading", x !== null);
      if (x === null) { head.textContent = head.dataset.title; continue; }
      const items = idx.map((index, i) => (index < 0 ? null : chart.$readout(chart.data.datasets[i], chart.data.datasets[i].data[index])))
        .filter(Boolean);
      head.replaceChildren(...(items.length ? items : [el("span", { class: "muted", text: "no reading at this time" })]));
    }
  }
  // Pointer events cover mouse and touch alike. A sideways drag reads the
  // values; up and down scrolls the page (touch-action in style.css), which
  // cancels the reading. Lifting the finger keeps the values HOVER_KEEP_S.
  function listenCharts() {
    let next = null, frame = 0, keep = 0;
    const show = (x) => {
      clearTimeout(keep);
      next = x;
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; showAt(next); });
    };
    for (const def of CHARTS) {
      const canvas = $(def.id);
      const at = (e) => {
        const chart = state.charts[def.id];
        if (!chart) return null;
        const { left, right } = chart.chartArea;
        return e.offsetX < left || e.offsetX > right ? null : chart.scales.x.getValueForPixel(e.offsetX);
      };
      canvas.addEventListener("pointerdown", (e) => show(at(e)));
      canvas.addEventListener("pointermove", (e) => show(at(e)));
      canvas.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") show(null); });
      canvas.addEventListener("pointercancel", () => show(null));
      canvas.addEventListener("pointerup", (e) => {
        if (e.pointerType !== "mouse") keep = setTimeout(() => show(null), HOVER_KEEP_S * 1000);
      });
    }
  }
  // a vertical hairline at the hovered time, with the time over the time axis
  const crosshair = {
    id: "crosshair",
    afterDraw(chart) {
      if (chart.$hoverX === null || chart.$hoverX === undefined) return;
      const x = chart.scales.x.getPixelForValue(chart.$hoverX), { top, bottom, left, right } = chart.chartArea;
      if (x < left || x > right) return;
      const c = chart.ctx;
      c.save(); c.strokeStyle = css("--axis"); c.lineWidth = 1;
      c.beginPath(); c.moveTo(x, top); c.lineTo(x, bottom); c.stroke();
      const text = POW.fmtDateTime(chart.$hoverX);
      c.font = "600 11px system-ui, sans-serif";
      const w = c.measureText(text).width + 10, py = bottom + 2, h = Math.max(16, chart.scales.x.bottom - py);
      const px = Math.min(Math.max(x - w / 2, left), right - w);
      c.fillStyle = css("--surface-1");               // the axis shows only this time meanwhile,
      c.fillRect(left - 2, py, chart.width, chart.height - py);   // keeping the "0" of the other axis
      c.fillRect(0, bottom + 8, left, chart.height);
      c.fillStyle = css("--surface-2");
      c.beginPath();
      if (c.roundRect) c.roundRect(px, py, w, h, 4); else c.rect(px, py, w, h);
      c.fill(); c.stroke();
      c.fillStyle = css("--text-primary"); c.textBaseline = "middle";
      c.fillText(text, px + 5, py + h / 2 + 0.5);
      c.restore();
    },
  };
  // a hairline at 0 degC
  const freezing = {
    id: "freezing",
    beforeDatasetsDraw(chart, _args, opts) {
      if (!opts.on) return;
      const y = chart.scales.y, { left, right } = chart.chartArea;
      if (y.min > 0 || y.max < 0) return;
      const py = y.getPixelForValue(0), c = chart.ctx;
      c.save(); c.strokeStyle = css("--axis"); c.lineWidth = 1;
      c.beginPath(); c.moveTo(left, py); c.lineTo(right, py); c.stroke(); c.restore();
    },
  };

  // snow chart: a shaded band below MIN_DEPTH_CM, where POW-O-METER's readings are not snow depth yet
  const shallow = {
    id: "shallow",
    beforeDatasetsDraw(chart, _args, opts) {
      if (!opts.on) return;
      const y = chart.scales.y, { left, right } = chart.chartArea;
      const top = y.getPixelForValue(MIN_DEPTH_CM), bottom = y.getPixelForValue(Math.max(0, y.min)), c = chart.ctx;
      c.save();
      c.fillStyle = css("--grid");
      c.fillRect(left, top, right - left, bottom - top);
      c.fillStyle = css("--text-muted"); c.font = "11px system-ui, sans-serif";
      const inside = bottom - top >= 20;            // a thin band (tall scale) gets its label above it
      c.textBaseline = inside ? "top" : "bottom";
      c.fillText(`POW-O-METER can’t measure under ${MIN_DEPTH_CM} cm`, left + 6, inside ? top + 4 : top - 3);
      c.restore();
    },
  };

  // time axis: as many labels (and grid lines) as fit, about 70 px each, the
  // newest kept; 7 days shows every other day, 30 days every 7th
  function labelled(chart, i, n) {
    const step = Math.ceil(70 / (chart.width / Math.max(1, n)));
    return (n - 1 - i) % step === 0;
  }

  function chartOptions(def, min, max, stepH) {
    const muted = css("--text-muted"), grid = css("--grid");
    return {
      animation: false, parsing: false, normalized: true, maintainAspectRatio: false,
      events: [],                                 // the page handles the pointer itself (listenCharts)
      scales: {
        x: { type: "linear", min, max,
             afterBuildTicks: (ax) => { ax.ticks = POW.ticks(min, max, stepH).map((value) => ({ value })); },
             ticks: { color: muted, maxRotation: 0, autoSkip: false,
                      callback(v, i, ticks) { return labelled(this.chart, i, ticks.length) ? POW.tickText(v) : ""; } },
             grid: { color: (ctx) => (ctx.tick && !labelled(ctx.chart, ctx.index, ctx.chart.scales.x.ticks.length) ? "transparent" : grid) },
             border: { color: css("--axis") } },
        y: { ticks: { color: muted, callback: def.axis, maxTicksLimit: 5 }, grid: { color: grid },
             border: { display: false }, ...(def.y || {}) },
      },
      plugins: {
        legend: { display: false },
        freezing: { on: !!def.freezing },
        shallow: { on: !!def.shallow },
        tooltip: { enabled: false },
      },
      elements: { point: { radius: 0, hoverRadius: 4 },
                  line: { borderWidth: 2, tension: 0, borderJoinStyle: "round", borderCapStyle: "round" } },
      spanGaps: false,
    };
  }

  // -- time range buttons: one choice for all charts, remembered in this browser --
  function renderRange() {
    for (const b of document.querySelectorAll("#range button")) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.days) === state.days));
    }
    $("charts-heading").textContent = state.days === 1 ? "Last 24 hours" : `Last ${state.days} days`;
  }

  function renderCharts(now) {
    const from = now - state.days * 24 * H, stepH = state.days === 1 ? 6 : 24;
    const rows = state.recent.concat(state.nearRows);
    const dirs = new Map();                     // wind direction at a station and time, for the title line
    for (const r of state.nearRows) {
      if (r.variable === "wind_from_direction" && r.value !== "") dirs.set(`${r.station_id}|${POW.ms(r.time_utc)}`, Number(r.value));
    }
    for (const def of CHARTS) {
      const series = POW.series(rows, def.variable, from, { transform: def.transform });
      const shown = state.stations.filter((s) => series[s.id] && series[s.id].some((p) => p.y !== null));
      const wrap = $(def.id).parentElement, note = $(def.id + "-none"), head = $(def.id + "-head");
      if (state.charts[def.id]) { state.charts[def.id].destroy(); delete state.charts[def.id]; }
      head.textContent = head.dataset.title;
      head.classList.remove("reading");
      head.hidden = wrap.hidden = !shown.length;
      if (note) note.hidden = !!shown.length;
      if (!shown.length) continue;
      const datasets = shown.map((s) => ({ label: s.label, station: s.id, data: series[s.id],
        borderColor: s.color, backgroundColor: s.color, hidden: state.hidden.has(s.id) }));
      const chart = state.charts[def.id] = new Chart($(def.id), { type: "line", data: { datasets },
        options: chartOptions(def, from, now, stepH), plugins: [crosshair, freezing, shallow] });
      // one station's value in the title line: colour key, value (and wind direction)
      chart.$readout = (ds, p) => {
        const fmt = ds.station === STATION && def.ownFmt ? def.ownFmt : def.fmt;
        let t = fmt(p.y);
        if (def.direction) { const d = dirs.get(`${ds.station}|${p.x}`); if (d !== undefined) t += " " + POW.compass(d); }
        const key = el("span", { class: "key line" }); key.style.background = ds.borderColor;
        return el("span", { class: "item", title: ds.label }, key, t);
      };
    }
  }

  function renderTable(now) {
    const body = $("table-24h");
    body.replaceChildren();
    const rows = POW.readings(state.recent, state.site, now - TABLE_HOURS * H, now);
    if (!rows.length) {
      body.append(el("tr", {}, el("td", { colspan: "4", class: "muted", text: "No readings in the last 24 hours." })));
      return;
    }
    const val = (v, f) => (v === undefined ? "–" : f(v));
    for (const r of rows) {
      body.append(el("tr", {},
        el("td", { class: "nowrap", text: POW.fmtDateTime(r.ms) }),
        el("td", { class: "num", text: val(r.snow_depth, POW.depthText) }),
        el("td", { class: "num", text: val(r.air_temperature, POW.fmtTemp) }),
        el("td", { class: "num", text: val(r.relative_humidity, (v) => `${Math.round(v)}%`) })));
    }
  }

  // -- webcams: newest image, and a slider back through the last few days --
  function renderWebcams() {
    const box = $("webcam-body");
    const idx = state.webcams;
    $("webcams").hidden = false;
    if (!idx || !idx.enabled) {
      box.replaceChildren(el("p", { class: "muted", text: "Webcam images are not available here yet." }));
      return;
    }
    box.replaceChildren();
    state.camLive = [];
    for (const cam of idx.cameras) box.append(camera(cam, idx.keep_hours));
    box.append(el("p", { class: "small muted", text: idx.credit }));
  }

  function camera(cam, keepHours) {
    // the frames of the time lapse, then the newest image from Shames itself
    const frames = cam.frames.map((f) => ({ src: "webcams/" + f.file, ms: POW.ms(f.time_utc) }))
      .concat([{ src: POW.freshUrl(cam.live_url, Date.now()), ms: null }]);
    const img = el("img", { class: "cam", alt: `${cam.label} webcam`, loading: "lazy", src: frames[frames.length - 1].src });
    const cap = el("figcaption", {});
    const slider = el("input", { type: "range", min: "0", max: String(frames.length - 1),
                                 value: String(frames.length - 1), "aria-label": `${cam.label}: go back in time` });
    const play = el("button", { type: "button", text: "▶ Last " + Math.round(keepHours / 24) + " days" });
    let i = frames.length - 1, timer = null;
    const show = () => {
      img.src = frames[i].src;
      cap.textContent = frames[i].ms === null ? `${cam.label}: newest image (its time is printed on it)`
        : `${cam.label}: ${POW.fmtDateTime(frames[i].ms)} BC · ${i + 1} of ${frames.length - 1}`;
      slider.value = String(i);
    };
    const stop = () => { clearInterval(timer); timer = null; play.textContent = "▶ Last " + Math.round(keepHours / 24) + " days"; };
    slider.addEventListener("input", () => { if (timer) stop(); i = Number(slider.value); show(); });
    play.addEventListener("click", () => {
      if (timer) { stop(); return; }
      frames.forEach((f) => { const p = new Image(); p.src = f.src; });     // preload
      i = 0; show();
      play.textContent = "❚❚ Pause";
      timer = setInterval(() => { if (++i >= frames.length) { i = frames.length - 1; show(); stop(); } else show(); }, 400);
    });
    show();
    // on refresh: a fresh newest image, swapped in only if it is the one showing
    state.camLive.push((nowMs) => {
      const live = frames[frames.length - 1], src = POW.freshUrl(cam.live_url, nowMs);
      if (live.src === src) return;
      live.src = src;
      if (i === frames.length - 1) img.src = src;
    });
    const controls = frames.length > 1 ? el("div", { class: "controls" }, play, slider)
      : el("p", { class: "small muted", text: "No time lapse yet: no daylight images in the last few days." });
    const note = POW.camNote(cam.latest_utc, Date.now());
    return el("div", { class: "camera" }, el("h3", { text: cam.label }),
      note ? el("p", { class: "small muted cam-note", text: note }) : null, el("figure", {}, img, cap), controls);
  }

  // ---- "More" sections, filled when opened ----------------------------------
  const sections = {
    details(now) {
      const body = $("table-details");
      body.replaceChildren();
      for (const r of POW.readings(state.recent, state.site, now - TABLE_HOURS * H, now)) {
        const val = (v, f) => (v === undefined ? "–" : f(v));
        body.append(el("tr", {},
          el("td", { class: "nowrap", text: POW.fmtDateTime(r.ms) }),
          el("td", { class: "num", text: val(r.snow_depth, (v) => `${v} cm`) }),
          el("td", { class: "num", text: val(r.air_temperature, POW.fmtTemp) }),
          el("td", { class: "num", text: val(r.relative_humidity, (v) => `${v}%`) })));
        // labels on their own line, only when there is more to say than "Provisional"
        const extra = r.rows.map(POW.labelText).filter((t) => t !== "Provisional");
        if (extra.length) {
          body.append(el("tr", { class: "label-row" }, el("td", { colspan: "4", class: "small muted",
            text: [...new Set(extra)].join("; ") })));
        }
      }
      const sd = $("sd-values");
      sd.replaceChildren();
      const names = { air_pressure: "Air pressure", battery_voltage: "Battery", enclosure_temperature: "Inside the logger box" };
      const siteName = Object.fromEntries((state.sites.sites || []).map((s) => [s.id, s.name]));
      for (const [k, name] of Object.entries(names)) {
        const v = state.latest.values[k];
        if (!v) continue;
        sd.append(el("li", { text: `${name}: ${v.value} ${v.unit === "degC" ? "°C" : v.unit} — ` +
          `${POW.fmtDay(POW.ms(v.time_utc))} (${POW.ageText(POW.ageHours(v.time_utc, now))}), ${siteName[v.site_id] || v.site_id}` }));
      }
    },

    nearby(now) {
      const ids = Object.keys(state.near.stations || {});
      $("nearby-body").hidden = !ids.length;
      $("nearby-empty").hidden = !!ids.length;
      if (!ids.length) return;
      const tb = $("nearby-table");
      tb.replaceChildren();
      for (const s of state.stations) {
        const v = s.id === STATION ? state.latest.values.air_temperature : (state.near.stations[s.id].values || {}).air_temperature;
        const key = el("span", { class: "key line" }); key.style.background = s.color;
        tb.append(el("tr", {},
          el("td", {}, key, s.label),
          el("td", { class: "num" }, v ? POW.fmtTemp(v.value) + (v.quality && v.quality !== "good" ? ` (${v.quality})` : "") : "–",
             el("div", { class: "small muted", text: v ? POW.ageText(POW.ageHours(v.time_utc, now)) : "" }))));
      }
    },

    health(now) {
      const st = state.status.stations[STATION];
      const dl = $("health-list");
      dl.replaceChildren();
      const add = (k, v) => dl.append(el("dt", { text: k }), el("dd", {}, v));
      add("Last message from the station", st.last_message_utc ?
        `${POW.fmtDateTime(POW.ms(st.last_message_utc))} (${POW.ageText(POW.ageHours(st.last_message_utc, now))})` : "none");
      add("Files built", `${POW.fmtDateTime(POW.ms(state.status.built_utc))} (${POW.ageText(POW.ageHours(state.status.built_utc, now))})`);
      add("Battery (by radio)", POW.batteryText(st));
      add("Station clock", st.clock_offset_min === null ? "unknown" : `${st.clock_offset_min >= 0 ? "+" : ""}${st.clock_offset_min} min off`);
      const sd = (state.status.info.sd_downloads || []).slice(-1)[0];
      add("Last SD card download", sd ? sd.download : "none");
      const alarms = state.status.alarms || [];
      add("Alarms", alarms.length ? el("ul", {}, ...alarms.map((a) => el("li", { text: a.message }))) : "none");
      dl.append(el("dt", { text: "" }), el("dd", {}, link(`${REPO}/issues`, "Open alarm Issues on GitHub")));
    },
  };

  function runSection(name) {
    const box = document.querySelector(`details[data-section="${name}"] .section-body`);
    const old = box && box.querySelector(".section-error");
    if (old) old.remove();
    Promise.resolve().then(() => sections[name](Date.now())).catch(() => {
      if (box) box.prepend(el("p", { class: "muted section-error", text: "Could not load this section." }));
    });
  }

  // ---- main ------------------------------------------------------------------
  function renderAll() {
    const now = Date.now();
    state.stations = stationList();
    renderBanner(now);
    renderUpdated(now);
    renderTiles(now);
    renderPicker();
    renderCharts(now);
    renderTable(now);
    renderCredits();
    for (const d of document.querySelectorAll("details[data-section]")) if (d.open) runSection(d.dataset.section);
  }

  function renderCredits() {
    // one line per owner's terms (the licence text carries the owner's credit)
    const ul = $("nearby-credit");
    const groups = new Map();
    for (const s of state.stations.filter((x) => x.id !== STATION)) {
      const k = s.licence || s.operator || "";
      groups.set(k, (groups.get(k) || []).concat(s.label));
    }
    ul.replaceChildren(...[...groups].map(([terms, labels]) => el("li", { text: `${labels.join(", ")}: ${terms}` })));
    ul.hidden = !ul.children.length;
  }

  async function load() {
    const opt = (p) => p.catch(() => null);           // optional files: the page works without them
    const [latest, status, sites, recent, near, nearRows, webcams] = await Promise.all([
      getJSON("v1/latest.json"), getJSON("v1/status.json"), getJSON("v1/sites.json"), getText("v1/recent.csv"),
      opt(getJSON("v1/nearby_latest.json")), opt(getText("v1/nearby_recent.csv")), opt(getJSON("webcams/index.json"))]);
    const firstLoad = !state.status;
    Object.assign(state, { latest, status, sites, recent: POW.parseCSV(recent), site: POW.currentSite(sites),
                           near: near || { stations: {} }, nearRows: nearRows ? POW.parseCSV(nearRows) : [] });
    state.fetchedMs = Date.now();
    if (firstLoad || JSON.stringify(webcams) !== JSON.stringify(state.webcams)) {
      state.webcams = webcams;
      renderWebcams();
    } else {
      for (const f of state.camLive) f(state.fetchedMs);
    }
  }

  // -- "Updated 3 min ago · Refresh" under the title --
  function renderUpdated(now) {
    if (state.fetchedMs === null) return;
    $("updated").hidden = false;
    $("updated-text").textContent = POW.updatedText(state.fetchedMs, now);
  }

  function start() {
    for (const e of document.querySelectorAll(".bc-label")) e.textContent = BC_LABEL;
    state.hidden = new Set(store.get("powometer.hidden") || []);
    const days = store.get("powometer.days");
    if (CHART_DAYS.includes(days)) state.days = days;
    for (const b of document.querySelectorAll("#range button")) {
      b.addEventListener("click", () => {
        state.days = Number(b.dataset.days);
        store.set("powometer.days", state.days);
        renderRange();
        if (state.status) renderCharts(Date.now());
      });
    }
    renderRange();
    for (const def of CHARTS) { const head = $(def.id + "-head"); head.dataset.title = head.textContent; }
    listenCharts();
    for (const d of document.querySelectorAll("details[data-section]")) {
      d.addEventListener("toggle", () => { if (d.open && state.status) runSection(d.dataset.section); });
    }
    // a link like .../#health opens that section
    const openHash = () => {
      const t = document.getElementById(location.hash.slice(1));
      if (t && t.tagName === "DETAILS") t.open = true;
      if (t) t.scrollIntoView();
    };
    window.addEventListener("hashchange", openHash);
    const button = $("refresh");
    let busy = null;                       // the refresh under way, so taps don't pile up
    const refresh = () => busy || (busy = (async () => {
      button.disabled = true;
      button.textContent = "Updating…";
      try {
        await load();
        renderAll();
      } catch (e) {
        const b = $("banner");
        b.hidden = false;
        b.className = "banner stale";
        b.textContent = "Could not load the data. Check your connection; the values shown (if any) may be old.";
      } finally {
        button.disabled = false;
        button.textContent = "↻ Refresh";
        renderUpdated(Date.now());
        busy = null;
      }
    })());
    const tick = () => { if (state.status) { const n = Date.now(); renderBanner(n); renderTiles(n); renderUpdated(n); } };
    // A phone app from the home screen has no reload button, and the phone
    // pauses it in the background: refresh when it is back on screen.
    const back = () => {
      if (document.visibilityState !== "visible") return;
      if (state.fetchedMs === null || Date.now() - state.fetchedMs > REOPEN_MIN * 60e3) refresh(); else tick();
    };
    button.addEventListener("click", refresh);
    document.addEventListener("visibilitychange", back);
    window.addEventListener("pageshow", (e) => { if (e.persisted) back(); });
    refresh().then(openHash);
    setInterval(() => { if (document.visibilityState === "visible") refresh(); }, REFRESH_MIN * 60e3);
    setInterval(tick, 60e3);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.status) renderAll(); });
  }
  start();
})();
