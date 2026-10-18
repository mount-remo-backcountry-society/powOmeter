// Tests for the intake Worker: node --test (Node 20 or later; no packages).
// RockBLOCK's real key cannot sign test messages, so each test makes its own
// key pair and hands the public half to the Worker as ROCKBLOCK_PUBLIC_KEY.
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { checkedFields, isoTime, verifyJwt } from "../src/index.js";

const IMEI = "9".repeat(15);                 // made up; never the station's
const READ_KEY = "test-read-key";

const b64url = (bytes) => Buffer.from(bytes).toString("base64url");

async function keys() {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["sign", "verify"]);
  const spki = Buffer.from(await crypto.subtle.exportKey("spki", pair.publicKey)).toString("base64");
  return { privateKey: pair.privateKey, pem: `-----BEGIN PUBLIC KEY-----\n${spki}\n-----END PUBLIC KEY-----` };
}

async function sign(claims, privateKey, alg = "RS256") {
  const h = b64url(JSON.stringify({ alg, typ: "JWT" })), p = b64url(JSON.stringify(claims));
  const s = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64url(s)}`;
}

class KV {                                   // the parts of Workers KV the Worker uses
  constructor() { this.items = new Map(); }
  async put(key, value, opts = {}) { this.items.set(key, { value, metadata: opts.metadata, ttl: opts.expirationTtl }); }
  async get(key) { return this.items.get(key)?.value ?? null; }
  async list({ prefix }) {
    const keys = [...this.items.keys()].filter((k) => k.startsWith(prefix)).sort()
      .map((name) => ({ name, metadata: this.items.get(name).metadata }));
    return { keys, list_complete: true };
  }
}

function setup(pem, extra = {}) {
  const env = { MAILBOX: new KV(), STATION_IMEI: IMEI, READ_KEY, GH_DISPATCH_TOKEN: "gh-token",
                GITHUB_REPO: "org/repo", GITHUB_WORKFLOW: "publish.yml", GITHUB_REF: "main",
                ROCKBLOCK_PUBLIC_KEY: pem, ...extra };
  const waits = [];
  const ctx = { waitUntil: (p) => waits.push(p) };
  return { env, ctx, waits };
}

// a real message as RockBLOCK delivers it (MOMSN 3271, 2026-10-17); IMEI made up
const FIELDS = { imei: IMEI, serial: "12345", momsn: "3271", transmit_time: "26-10-17 18:39:26",
                 iridium_latitude: "54.1", iridium_longitude: "-128.1", iridium_cep: "8",
                 data: Buffer.from("1709293377+00595:381+01493").toString("hex") };

async function post(env, ctx, fields, type = "form") {
  const body = type === "json" ? JSON.stringify(fields) : new URLSearchParams(fields);
  const headers = { "content-type": type === "json" ? "application/json" : "application/x-www-form-urlencoded" };
  return worker.fetch(new Request("https://intake.example/rockblock", { method: "POST", body, headers }), env, ctx);
}

async function mailbox(env, key = READ_KEY) {
  const headers = key ? { authorization: `Bearer ${key}` } : {};
  return worker.fetch(new Request("https://intake.example/messages", { headers }), env, {});
}

function mockGitHub() {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return new Response(null, { status: 204 }); };
  return calls;
}

test("RockBLOCK times become ISO UTC", () => {
  assert.equal(isoTime("26-10-17 18:39:26"), "2026-10-17T18:39:26Z");
  assert.equal(isoTime("2026-10-17 18:39:26"), "2026-10-17T18:39:26Z");
  assert.equal(isoTime("17/Oct/2026"), null);
});

test("a signed message is kept without IMEI, serial or position, and a run is requested", async () => {
  const { privateKey, pem } = await keys();
  const { env, ctx, waits } = setup(pem);
  const calls = mockGitHub();
  const r = await post(env, ctx, { ...FIELDS, JWT: await sign(FIELDS, privateKey) });
  assert.equal(r.status, 200);
  await Promise.all(waits);
  const stored = [...env.MAILBOX.items.values()];
  assert.equal(stored.length, 1);
  assert.deepEqual(stored[0].metadata, { transmit_utc: "2026-10-17T18:39:26Z", momsn: 3271, cep_km: 8,
                                         payload_hex: FIELDS.data });
  assert.ok(!JSON.stringify(stored[0]).includes(IMEI) && !stored[0].value.includes("128.1"));
  assert.equal(stored[0].ttl, 30 * 86400);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.github.com/repos/org/repo/actions/workflows/publish.yml/dispatches");
  assert.equal(calls[0].init.headers.authorization, "Bearer gh-token");
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main" });
});

test("JSON deliveries work too, and a retried delivery is stored once", async () => {
  const { privateKey, pem } = await keys();
  const { env, ctx } = setup(pem);
  mockGitHub();
  const msg = { ...FIELDS, JWT: await sign(FIELDS, privateKey) };
  assert.equal((await post(env, ctx, msg, "json")).status, 200);
  assert.equal((await post(env, ctx, msg)).status, 200);
  assert.equal(env.MAILBOX.items.size, 1);
});

test("forged, altered, unsigned-payload and foreign messages are refused", async () => {
  const { privateKey, pem } = await keys();
  const other = await keys();
  const { env, ctx } = setup(pem);
  mockGitHub();
  const refused = async (fields) => (await post(env, ctx, fields)).status;
  assert.equal(await refused({ ...FIELDS }), 403);                                            // no signature
  assert.equal(await refused({ ...FIELDS, JWT: await sign(FIELDS, other.privateKey) }), 403); // someone else's key
  assert.equal(await refused({ ...FIELDS, JWT: (await sign(FIELDS, privateKey)).slice(0, -4) + "AAAA" }), 403);
  assert.equal(await refused({ ...FIELDS, data: "41", JWT: await sign(FIELDS, privateKey) }), 403);  // payload altered
  const { data, ...noData } = FIELDS;
  assert.equal(await refused({ ...FIELDS, JWT: await sign(noData, privateKey) }), 403);       // payload not signed
  const foreign = { ...FIELDS, imei: "1".repeat(15) };
  assert.equal(await refused({ ...foreign, JWT: await sign(foreign, privateKey) }), 403);     // another device
  assert.equal(env.MAILBOX.items.size, 0);
});

test("other signing methods are refused", async () => {
  const { privateKey, pem } = await keys();
  assert.equal(await verifyJwt(await sign(FIELDS, privateKey, "none"), pem), null);
  assert.equal(await verifyJwt("not.a.token", pem), null);
  assert.deepEqual(await verifyJwt(await sign({ a: 1 }, privateKey), pem), { a: 1 });
});

test("signed values win; plain fields fill in what the token lacks", () => {
  const { fields } = checkedFields({ imei: IMEI, momsn: "7", iridium_cep: "3" }, { data: "41 42", transmit_time: "26-01-02 03:04:05" });
  assert.deepEqual(fields, { imei: IMEI, momsn: 7, transmit_time: "2026-01-02T03:04:05Z", iridium_cep: 3, data: "4142" });
  assert.match(checkedFields({}, { imei: IMEI }).error, /signed fields: imei/);
});

test("an empty session is acknowledged and not kept", async () => {
  const { privateKey, pem } = await keys();
  const { env, ctx } = setup(pem);
  mockGitHub();
  const empty = { ...FIELDS, data: "" };
  assert.equal((await post(env, ctx, { ...empty, JWT: await sign(empty, privateKey) })).status, 200);
  assert.equal(env.MAILBOX.items.size, 0);
});

test("the mailbox needs the read key and lists messages oldest first", async () => {
  const { privateKey, pem } = await keys();
  const { env, ctx } = setup(pem);
  mockGitHub();
  const later = { ...FIELDS, momsn: "3272", transmit_time: "26-10-17 20:51:00" };
  await post(env, ctx, { ...later, JWT: await sign(later, privateKey) });
  await post(env, ctx, { ...FIELDS, JWT: await sign(FIELDS, privateKey) });
  assert.equal((await mailbox(env, null)).status, 401);
  assert.equal((await mailbox(env, "wrong")).status, 401);
  const r = await mailbox(env);
  assert.equal(r.status, 200);
  assert.deepEqual((await r.json()).messages.map((m) => m.momsn), [3271, 3272]);
});

test("the timer requests a run; without a GitHub key it only logs", async () => {
  const { env, waits } = setup("unused");
  const calls = mockGitHub();
  await worker.scheduled({}, env, { waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  assert.equal(calls.length, 1);
  const quiet = setup("unused", { GH_DISPATCH_TOKEN: "" });
  await worker.scheduled({}, quiet.env, { waitUntil: (p) => quiet.waits.push(p) });
  await Promise.all(quiet.waits);
  assert.equal(calls.length, 1);
});

test("other addresses: a plain page or not found", async () => {
  const { env } = setup("unused");
  assert.equal((await worker.fetch(new Request("https://intake.example/"), env, {})).status, 200);
  assert.equal((await worker.fetch(new Request("https://intake.example/x"), env, {})).status, 404);
});
