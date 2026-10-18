/* POW-O-METER intake: a Cloudflare Worker (free plan).
 *
 * Three jobs:
 *  1. POST /rockblock: RockBLOCK delivers each satellite message here (in
 *     addition to the email). The message is checked (RockBLOCK's signature
 *     and our station's IMEI), stripped to what is published anyway (message
 *     number, transmit time, position accuracy, payload; NOT the IMEI,
 *     serial or position), kept in the mailbox (KV) for 30 days, and a
 *     publish run is requested on GitHub.
 *  2. Every 10 minutes (cron in wrangler.toml): request a publish run, so
 *     webcams and nearby stations update even when no message comes.
 *  3. GET /messages: the mailbox, for the publish run (read key needed).
 *
 * The request to GitHub only says "run now"; the run then reads the whole
 * mailbox. GitHub keeps one waiting run and drops older waiting ones, so a
 * message sent inside a request could be lost; a mailbox cannot. (KV can
 * take up to a minute to show a new message everywhere; if the run it
 * started misses it, the next timer run picks it up.)
 *
 * Settings: wrangler.toml (not secret) and Worker secrets set in the
 * Cloudflare dashboard (OPERATIONS.md): STATION_IMEI, READ_KEY,
 * GH_DISPATCH_TOKEN.
 */

// RockBLOCK (Ground Control) signs each delivery with this key; from
// https://docs.groundcontrol.com/iot/rockblock/web-services/receiving-mo-message
const ROCKBLOCK_PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAlaWAVJfNWC4XfnRx96p9cztBcdQV6l8aKmzAlZdpEcQR6MSPzlgvihaUHNJgKm8t5ShR3jcDXIOI7er30cIN4/9aVFMe0LWZClUGgCSLc3rrMD4FzgOJ4ibD8scVyER/sirRzf5/dswJedEiMte1ElMQy2M6IWBACry9u12kIqG0HrhaQOzc6Tr8pHUWTKft3xwGpxCkV+K1N+9HCKFccbwb8okRP6FFAMm5sBbw4yAu39IVvcSL43Tucaa79FzOmfGs5mMvQfvO1ua7cOLKfAwkhxEjirC0/RYX7Wio5yL6jmykAHJqFG2HT0uyjjrQWMtoGgwv9cIcI7xbsDX6owIDAQAB
-----END PUBLIC KEY-----`;

const KEEP_DAYS = 30;                       // mailbox: the publish run reads it every few minutes
const PREFIX = "msg/";

// ---- small helpers ---------------------------------------------------------
const text = (body, status = 200) => new Response(body, { status, headers: { "content-type": "text/plain" } });

function b64urlBytes(s) {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function pemBytes(pem) {
  return Uint8Array.from(atob(pem.replace(/-----[^-]+-----/g, "").replace(/\s/g, "")), (c) => c.charCodeAt(0));
}

/** Same length and same characters, without stopping at the first difference. */
function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

/** The claims of an RS256 token signed with `pem`, or null. */
export async function verifyJwt(token, pem) {
  try {
    const [h, p, s] = String(token).split(".");
    if (!h || !p || !s) return null;
    if (JSON.parse(new TextDecoder().decode(b64urlBytes(h))).alg !== "RS256") return null;
    const key = await crypto.subtle.importKey("spki", pemBytes(pem),
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlBytes(s),
      new TextEncoder().encode(`${h}.${p}`));
    return ok ? JSON.parse(new TextDecoder().decode(b64urlBytes(p))) : null;
  } catch (e) {
    return null;
  }
}

/** RockBLOCK's "21-10-31 10:41:50" (UTC) -> "2021-10-31T10:41:50Z", or null. */
export function isoTime(s) {
  const m = /^(\d{2}|\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(String(s || "").trim());
  if (!m) return null;
  const year = m[1].length === 2 ? "20" + m[1] : m[1];
  return `${year}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`;
}

const NORMAL = {
  imei: (v) => String(v).trim(),
  momsn: (v) => Number(v),
  transmit_time: (v) => isoTime(v),
  iridium_cep: (v) => Number(v),
  data: (v) => String(v).replace(/\s/g, "").toLowerCase(),
};

/** The delivered fields checked against the signed ones. Each field is
 *  taken from the signed token when the token has it, and must then match
 *  the plain field; the payload must be covered by the signature.
 *  Returns {fields} or {error}. */
export function checkedFields(form, claims) {
  if (!claims || claims.data === undefined) {
    return { error: "the signature does not cover the payload; signed fields: " + Object.keys(claims || {}).join(",") };
  }
  const out = {};
  for (const [k, norm] of Object.entries(NORMAL)) {
    const signed = claims[k] === undefined ? undefined : norm(claims[k]);
    const plain = form[k] === undefined ? undefined : norm(form[k]);
    if (signed !== undefined && plain !== undefined && signed !== plain) return { error: `${k} differs from the signed value` };
    out[k] = signed !== undefined ? signed : plain;
  }
  return { fields: out };
}

/** What is kept: the same fields as the repository's raw message files. */
export function record(f) {
  return {
    transmit_utc: f.transmit_time,
    momsn: Number.isFinite(f.momsn) ? f.momsn : null,
    cep_km: Number.isFinite(f.iridium_cep) ? f.iridium_cep : null,
    payload_hex: f.data,
  };
}

async function readBody(request) {
  const type = request.headers.get("content-type") || "";
  if (type.includes("application/json")) return await request.json();
  return Object.fromEntries((await request.formData()).entries());
}

/** Ask GitHub for a publish run. Never throws. */
export async function requestRun(env, why) {
  if (!env.GH_DISPATCH_TOKEN) {
    console.log(`run not requested (${why}): GH_DISPATCH_TOKEN is not set`);
    return false;
  }
  try {
    const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "powometer-intake",
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
    });
    if (r.status !== 204) console.log(`run request (${why}) failed: HTTP ${r.status} ${await r.text()}`);
    return r.status === 204;
  } catch (e) {
    console.log(`run request (${why}) failed: ${e}`);
    return false;
  }
}

// ---- the three jobs -------------------------------------------------------------
async function receive(request, env, ctx) {
  let form;
  try {
    form = await readBody(request);
  } catch (e) {
    return text("unreadable body", 400);
  }
  const claims = await verifyJwt(form.JWT, env.ROCKBLOCK_PUBLIC_KEY || ROCKBLOCK_PUBLIC_KEY);
  if (!claims) {
    console.log("rejected: no valid RockBLOCK signature");
    return text("bad signature", 403);          // RockBLOCK retries for ~6 days: time to look
  }
  const { fields, error } = checkedFields(form, claims);
  if (error) {
    console.log("rejected: " + error);
    return text("rejected", 403);
  }
  if (!sameSecret(fields.imei, env.STATION_IMEI)) {
    console.log("rejected: not our station's IMEI");
    return text("unknown device", 403);
  }
  if (!fields.transmit_time) return text("no transmit time", 400);
  if (!fields.data) return text("ok (no payload)");   // an empty session: nothing to keep
  const rec = record(fields);
  const key = `${PREFIX}${rec.transmit_utc}/${rec.momsn ?? "x"}`;      // a retried delivery overwrites itself
  await env.MAILBOX.put(key, JSON.stringify(rec), { metadata: rec, expirationTtl: KEEP_DAYS * 86400 });
  ctx.waitUntil(requestRun(env, `message ${rec.momsn}`));
  return text("ok");
}

async function messages(request, env) {
  const auth = request.headers.get("authorization") || "";
  if (!sameSecret(auth.replace(/^Bearer /, ""), env.READ_KEY)) return text("read key needed", 401);
  const out = [];
  let cursor;
  do {
    const page = await env.MAILBOX.list({ prefix: PREFIX, cursor });
    for (const k of page.keys) out.push(k.metadata || JSON.parse(await env.MAILBOX.get(k.name)));
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  out.sort((a, b) => (a.transmit_utc < b.transmit_utc ? -1 : a.transmit_utc > b.transmit_utc ? 1 : 0));
  return new Response(JSON.stringify({ messages: out }), { headers: { "content-type": "application/json" } });
}

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === "/rockblock" && request.method === "POST") return receive(request, env, ctx);
    if (path === "/messages" && request.method === "GET") return messages(request, env);
    if (path === "/") return text("POW-O-METER intake (Mount Remo Backcountry Society)");
    return text("not found", 404);
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(requestRun(env, "timer"));
  },
};
