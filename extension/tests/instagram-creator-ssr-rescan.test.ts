import assert from "node:assert/strict";
import test from "node:test";

const relay = (user: unknown) => ({ require: [["ScheduledServerJS", "handle", null, [{
  __bbox: { require: [["RelayPrefetchedStreamCache", "next", [], ["fixture-cache", {
    __bbox: { result: { data: { xig_user_by_username: user } } },
  }]]] },
}]]] });
const timeline = { polaris_ordered_timeline_connection: { edges: [{ node: {
  pk: "123", code: "FIXTURE", user: { id: "99", username: "public_fixture" },
} }], page_info: { has_next_page: false } } };
const profile = { pk: "42", id: "99", username: "public_fixture", is_private: false };
const { pk: _pk, ...weakerProfile } = profile;
const location = new URL("https://www.instagram.com/public_fixture/?openbiliclaw_instagram_task=1");
const events = Object.assign(new EventTarget(), { location, fetch: async () => new Response("{}") });
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = location as unknown as Location;
globalThis.XMLHttpRequest = class { open() {} } as unknown as typeof XMLHttpRequest;
let scans = 0;
globalThis.document = { readyState: "complete", querySelectorAll: () => {
  if (++scans > 8) throw new Error("recursive scan guard in regression harness");
  return [timeline, profile, weakerProfile].map(user => ({ textContent: JSON.stringify(relay(user)) }));
} } as unknown as Document;
const emitted: Array<{ items: Array<{ id: string; author_id: string }> }> = [];
events.addEventListener("openbiliclaw:instagram-response", event => emitted.push(JSON.parse((event as CustomEvent<string>).detail)));
await import("../src/main/instagram-response-tap.ts");

test("same-identity strong/weak SSR proofs merge without recursive scans and unlock earlier media", () => {
  assert.ok(scans <= 2, `only an initial scan and one bounded follow-up are allowed, got ${scans}`);
  assert.ok(emitted.some(row => row.items.some(item => item.id === "123" && item.author_id === "42")));
  assert.ok(emitted.flatMap(row => row.items).every(item => item.author_id === "42"));
});
