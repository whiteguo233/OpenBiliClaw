import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(fileURLToPath(new URL("../../src/openbiliclaw/web/shared/director-client.js", import.meta.url)), "utf8");

function fixture({ request = async () => ({ ok: true }), storage = new Map() } = {}) {
  const handlers = new Map();
  const timers = [];
  const doc = { hidden: false, defaultView: { innerHeight: 800, innerWidth: 600 }, addEventListener: (event, fn) => handlers.set(event, fn), removeEventListener: (event) => handlers.delete(event) };
  let observer;
  class Observer {
    constructor(callback) { this.callback = callback; this.nodes = new Set(); observer = this; }
    observe(node) { this.nodes.add(node); }
    unobserve(node) { this.nodes.delete(node); }
    disconnect() { this.nodes.clear(); }
    show(node, ratio = 1) { this.callback([{ target: node, isIntersecting: ratio > 0, intersectionRatio: ratio }]); }
  }
  let sequence = 0;
  const context = vm.createContext({ setTimeout, clearTimeout, crypto: { randomUUID: () => `uuid-${++sequence}` }, Date, Math });
  vm.runInContext(source, context);
  const client = context.OpenBiliClawDirectorClient.createClient({ request, document: doc, Observer,
    storage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    setTimer: (fn, ms) => { const value = { fn, ms }; timers.push(value); return value; },
    clearTimer: (value) => { value.cancelled = true; },
  });
  return { client, observer, doc, handlers, timers, storage };
}

const item = { id: 17, item_key: "fixture:card-17", batch_ref: "batch_1", stream_ref: "default" };
function card({ connected = true, top = 20, width = 200 } = {}) {
  return { dataset: {}, isConnected: connected, top,
    getClientRects() { return width ? [this.getBoundingClientRect()] : []; },
    getBoundingClientRect() { return { top: this.top, bottom: this.top + 180, left: 0, right: width, width, height: width ? 180 : 0 }; },
  };
}
const turn = () => new Promise(resolve => setImmediate(resolve));
function directorPayload(items = [], extra = {}) {
  return { items, director: { mode: "enforce", strategy: "agent_only", stream_ref: "default", batch_ref: items.length ? "batch_1" : null, requires_generation: !items.length, ...extra } };
}

test("restore/preload/registration without intersection never acknowledges exposure", async () => {
  const calls = [];
  const f = fixture({ request: async (...args) => { calls.push(args); return { ok: true }; } });
  f.client.ingest(directorPayload([item]));
  const node = card();
  f.client.bindCard(node, item);
  assert.equal(node.dataset.batchRef, "batch_1");
  assert.equal(calls.length, 0);
  f.observer.show(node, 0);
  await turn();
  assert.equal(calls.length, 0);
});

test("only actually visible, attached foreground cards get per-card batch ACK", async () => {
  const calls = [];
  const f = fixture({ request: async (...args) => { calls.push(args); return { ok: true }; } });
  const node = card();
  f.client.bindCard(node, item);
  f.doc.hidden = true;
  f.observer.show(node);
  await turn();
  assert.equal(calls.length, 0);
  f.doc.hidden = false;
  node.top = 1200;
  f.handlers.get("visibilitychange")();
  await turn();
  assert.equal(calls.length, 0);
  node.top = 20;
  f.observer.show(node);
  await turn();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/recommendations/director/present");
  assert.deepEqual(JSON.parse(calls[0][1].body), { batch_ref: "batch_1", ack_id: "visible:batch_1:17", item_keys: ["fixture:card-17"] });
  f.observer.show(node);
  await turn();
  assert.equal(calls.length, 1);
});

test("detached, zero-size, legacy, or already presented cards do not create exposures", async () => {
  const calls = [];
  const f = fixture({ request: async (...args) => { calls.push(args); return { ok: true }; } });
  for (const node of [card({ connected: false }), card({ width: 0 })]) {
    f.client.bindCard(node, item);
    f.observer.show(node);
  }
  const shown = card();
  f.client.bindCard(shown, { ...item, id: 18, item_key: "fixture:18", presented: true });
  f.observer.show(shown);
  f.client.bindCard(card(), { id: 19, item_key: "legacy" });
  await turn();
  assert.equal(calls.length, 0);
});

test("fragment cards all remain observed until attached and visible", async () => {
  const calls = [];
  const f = fixture({ request: async (...args) => { calls.push(args); return { ok: true }; } });
  const first = card({ connected: false });
  const second = card({ connected: false });
  f.client.bindCard(first, item);
  f.client.bindCard(second, { ...item, id: 18, item_key: "fixture:18" });
  assert.equal(f.observer.nodes.size, 2);
  first.isConnected = second.isConnected = true;
  f.observer.show(first);
  f.observer.show(second);
  await turn();
  assert.equal(calls.length, 2);
});

test("ACK retries are bounded and reuse identity, including after node recreation", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => { calls.push(JSON.parse(options.body)); throw new Error("offline"); } });
  const node = card();
  f.client.bindCard(node, item);
  f.observer.show(node);
  await turn();
  assert.equal(f.timers.length, 1);
  f.timers[0].fn();
  await turn();
  assert.equal(f.timers.length, 2);
  f.timers[1].fn();
  await turn();
  assert.equal(calls.length, 3);
  const replacement = card();
  f.client.bindCard(replacement, item);
  f.observer.show(replacement);
  await turn();
  assert.equal(calls.length, 3);
  assert.equal(new Set(calls.map(call => call.ack_id)).size, 1);
});

test("hidden retry pauses without inventing exposure then resumes on visibility", async () => {
  let calls = 0;
  const f = fixture({ request: async () => { if (++calls === 1) throw new Error("offline"); return { ok: true }; } });
  const node = card(); f.client.bindCard(node, item); f.observer.show(node);
  await turn();
  f.doc.hidden = true; f.timers[0].fn();
  await turn(); assert.equal(calls, 1);
  f.doc.hidden = false; f.handlers.get("visibilitychange")();
  await turn(); assert.equal(calls, 2);
});

test("destroy stops visibility listeners and scheduled retries", async () => {
  const f = fixture({ request: async () => { throw new Error("offline"); } });
  const node = card(); f.client.bindCard(node, item); f.observer.show(node);
  await turn();
  f.client.destroy();
  assert.equal(f.observer.nodes.size, 0);
  assert.equal(f.handlers.size, 0);
  assert.equal(f.timers[0].cancelled, true);
});

test("initial explicit generation only happens for visible required director screen", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => { calls.push([path, JSON.parse(options.body)]); return directorPayload([item]); } });
  await f.client.ensureInitial({ items: [], director: { mode: "off", requires_generation: true } });
  await f.client.ensureInitial(directorPayload(), { allowGenerate: false });
  f.doc.hidden = true; await f.client.ensureInitial(directorPayload());
  assert.equal(calls.length, 0);
  f.doc.hidden = false;
  const result = await f.client.ensureInitial(directorPayload());
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/recommendations/director/generate");
  assert.equal(calls[0][1].intent, "NEXT");
  assert.equal(calls[0][1].stream_ref, "default");
  assert.equal(result.items.length, 1);
});

test("network retry keeps generation id and subsequent successful action gets new id", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => { const body = JSON.parse(options.body); calls.push(body); if (calls.length === 1) throw new Error("lost response"); return directorPayload([item]); } });
  f.client.ingest(directorPayload([item]));
  await assert.rejects(f.client.generate("/recommendations/append", { excluded_bvids: ["old"] }));
  await f.client.generate("/recommendations/append", { excluded_bvids: ["old"] });
  await f.client.generate("/recommendations/append", { excluded_bvids: ["old"] });
  assert.equal(calls[0].request_id, calls[1].request_id);
  assert.notEqual(calls[1].request_id, calls[2].request_id);
});

test("in-flight identical generation has one request and id", async () => {
  let resolve;
  let calls = 0;
  const f = fixture({ request: () => { calls += 1; return new Promise(done => { resolve = done; }); } });
  f.client.ingest(directorPayload([item]));
  const a = f.client.generate("/recommendations/append");
  const b = f.client.generate("/recommendations/append");
  resolve(directorPayload([item]));
  await Promise.all([a, b]);
  assert.equal(calls, 1);
});

test("pending id survives client reload", async () => {
  const storage = new Map();
  let firstId;
  const a = fixture({ storage, request: async (path, options) => { firstId = JSON.parse(options.body).request_id; throw new Error("lost"); } });
  a.client.ingest(directorPayload([item]));
  await assert.rejects(a.client.generate("/recommendations/append"));
  const b = fixture({ storage, request: async (path, options) => { assert.equal(JSON.parse(options.body).request_id, firstId); return directorPayload([item]); } });
  b.client.ingest(directorPayload([item]));
  await b.client.generate("/recommendations/append");
  assert.equal(storage.size, 0);
});

test("legacy mode preserves old mutation bodies exactly", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => { calls.push(JSON.parse(options.body)); return { items: [] }; } });
  await f.client.generate("/recommendations/append", { excluded_bvids: ["BV1"] });
  assert.deepEqual(calls, [{ excluded_bvids: ["BV1"] }]);
});

test("scope stays separate from returned scoped stream and feedback binds exact rec row", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => { calls.push(JSON.parse(options.body)); return directorPayload([{ ...item, stream_ref: "default:youtube" }]); } });
  f.client.ingest(directorPayload([item], { stream_ref: "default:youtube" }));
  await f.client.generate("/recommendations/append", { source_platform: "youtube" });
  assert.equal(calls[0].stream_ref, "default");
  assert.equal(calls[0].source_platform, "youtube");
  assert.equal(f.client.feedback({ recommendation_id: 17, feedback_type: "like" }).batch_ref, "batch_1");
  assert.equal(f.client.feedback({ recommendation_id: 18, feedback_type: "like" }).batch_ref, undefined);
});

test("only definitive missing-root STAY becomes NEXT, lost NEXT replays its own id", async () => {
  const calls = [];
  const f = fixture({ request: async (path, options) => {
    const body = JSON.parse(options.body); calls.push([path, body]);
    if (calls.length === 1) throw Object.assign(new Error("no root"), { status: 409, details: { detail: "stay_without_batch" } });
    if (calls.length === 2) throw new Error("NEXT response lost");
    return directorPayload([item]);
  } });
  f.client.ingest(directorPayload([item]));
  const path = "/recommendations/reshuffle";
  const body = { source_platform: "youtube" };
  await assert.rejects(f.client.generate(path, body));
  await f.client.generate(path, body);
  assert.deepEqual(calls.map(call => call[0]), [path, "/recommendations/director/generate", "/recommendations/director/generate"]);
  assert.notEqual(calls[0][1].request_id, calls[1][1].request_id);
  assert.equal(calls[1][1].request_id, calls[2][1].request_id);
  assert.equal(calls[1][1].intent, "NEXT");
});

test("other STAY conflicts never silently become NEXT", async () => {
  let calls = 0;
  const f = fixture({ request: async () => { calls += 1; throw Object.assign(new Error("conflict"), { status: 409, details: { detail: "generation_state_stale" } }); } });
  f.client.ingest(directorPayload([item]));
  await assert.rejects(f.client.generate("/recommendations/reshuffle"));
  assert.equal(calls, 1);
});

test("all entrypoints load shared protocol and card normalizers retain batch identity", () => {
  const root = new URL("../../", import.meta.url);
  for (const path of ["extension/popup/popup.html", "src/openbiliclaw/web/index.html", "src/openbiliclaw/web/desktop/index.html"]) {
    assert.match(readFileSync(new URL(path, root), "utf8"), /shared\/director-client\.js/);
  }
  for (const path of ["extension/popup/popup-helpers.js", "src/openbiliclaw/web/js/view-models.js", "src/openbiliclaw/web/desktop/assets/js/app.js"]) {
    const value = readFileSync(new URL(path, root), "utf8");
    assert.match(value, /batch_ref:/); assert.match(value, /stream_ref:/);
  }
});
