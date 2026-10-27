import assert from "node:assert/strict";
import test from "node:test";

const profile = (patch: Record<string, unknown> = {}) => ({
  id: "42", pk: "42", username: "public_fixture", is_private: false, ...patch,
  polaris_ordered_timeline_connection: { edges: [{ node: {
    id: "123", code: "FIXTURE", user: { id: "42", username: "public_fixture" },
  } }], page_info: { has_next_page: false } },
});
const relay = (user: unknown) => ({ require: [["ScheduledServerJS", "handle", null, [{
  __bbox: { require: [["RelayPrefetchedStreamCache", "next", [], ["fixture-cache", {
    __bbox: { result: { data: { xig_user_by_username: user } } },
  }]]] },
}]]] });
const location = new URL("https://www.instagram.com/public_fixture/?openbiliclaw_instagram_task=1");
let transportPayload: unknown = {};
const events = Object.assign(new EventTarget(), { location, fetch: async () => {
  const response = new Response(JSON.stringify(transportPayload));
  Object.defineProperty(response, "url", { value: "https://www.instagram.com/graphql/query/" });
  return response;
} });
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = location as unknown as Location;
globalThis.XMLHttpRequest = class { open() {} } as unknown as typeof XMLHttpRequest;
globalThis.document = { readyState: "complete", querySelectorAll: () => [{ textContent: JSON.stringify(relay(profile())) }] } as unknown as Document;
const emitted: Array<{ items: unknown[] }> = [];
events.addEventListener("openbiliclaw:instagram-response", event => emitted.push(JSON.parse((event as CustomEvent<string>).detail)));
const { parseInstagramObservedPayload } = await import("../src/main/instagram-response-tap.ts");

test("native anonymous ScheduledServerJS/Relay SSR proves the matching public creator without an extra request", () => {
  assert.equal(emitted[0]?.items.length, 1, "the actual MAIN SSR scan must bridge the public row");
  const [result] = parseInstagramObservedPayload(relay(profile()), "/public_fixture/");
  assert.equal(result.items.length, 1);
  assert.equal(result.affirmative_terminal, true);
});

for (const [label, patch] of [
  ["private", { is_private: true }], ["unknown", { is_private: undefined }],
  ["other username", { username: "someone_else" }], ["other id", { id: "99", pk: "99" }],
  ["invalid primary key", { id: "42", pk: "invalid" }],
] as const) test(`SSR creator proof rejects ${label}`, () => {
  const result = parseInstagramObservedPayload(relay(profile(patch)), "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
});

test("viewer and suggested-account objects cannot supply anonymous creator proof", () => {
  for (const unrelated of [{ viewer: profile() }, { suggested_users: [profile()] }]) {
    const payload = { ...unrelated, data: { xig_user_by_username: profile({ is_private: undefined }) } };
    assert.equal(parseInstagramObservedPayload(payload, "/public_fixture/").flatMap(row => row.items).length, 0);
  }
});

test("unrecognized server module cannot supply SSR creator proof", () => {
  const payload = relay(profile());
  payload.require[0][0] = "UnrelatedModule";
  assert.equal(parseInstagramObservedPayload(payload, "/public_fixture/").flatMap(row => row.items).length, 0);
});

test("truncated SSR privacy evidence cannot authorize rows before a later private profile", () => {
  const entries = Array.from({ length: 17 }, (_, i) => ["RelayPrefetchedStreamCache", "next", [], ["fixture", {
    __bbox: { result: { data: { xig_user_by_username: profile(i === 0 ? {}
      : i === 16 ? { is_private: true } : { username: `unrelated_${i}` }) } } },
  }]]);
  const payload = { require: [["ScheduledServerJS", "handle", null, [{ __bbox: { require: entries } }]]] };
  const result = parseInstagramObservedPayload(payload, "/public_fixture/", { id: "42", keys: { pk: "42", id: "42" }, username: "public_fixture" });
  assert.equal(result.flatMap(row => row.items).length, 0);
  assert.ok(result.every(row => !row.affirmative_terminal));
});

for (const [label, patch] of [
  ["invalid primary key", { id: "42", pk: "invalid" }],
  ["unknown privacy", { is_private: undefined }],
  ["private", { is_private: true }],
] as const) test(`authoritative ${label} revokes a previously warm public creator cache`, async () => {
  const receive = async (payload: unknown) => {
    transportPayload = payload;
    await window.fetch("/graphql/query/");
    await new Promise(resolve => setTimeout(resolve, 20));
  };
  await receive({ data: { user: profile() } });
  assert.ok(emitted.some(row => row.items.length > 0));
  emitted.length = 0;
  await receive({ data: { user: profile(patch) } });
  assert.equal(emitted.flatMap(row => row.items).length, 0);
  const partial = profile();
  emitted.length = 0;
  await receive({ data: { xig_user_by_username: {
    polaris_ordered_timeline_connection: partial.polaris_ordered_timeline_connection,
  } } });
  assert.equal(emitted.flatMap(row => row.items).length, 0, "later media cannot reuse revoked proof");
});
