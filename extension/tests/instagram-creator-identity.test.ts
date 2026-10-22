import assert from "node:assert/strict";
import test from "node:test";

// Redacted shape from the anonymous Firefox profile: pk and GraphQL id are
// distinct numeric namespaces, but each matches its corresponding media field.
const publicProfile = { pk: "42", id: "99", username: "public_fixture", is_private: false };
const timeline = { polaris_ordered_timeline_connection: { edges: [{ node: {
  pk: "123", id: "456", code: "FIXTURE", user: { pk: "42", id: "99", username: "public_fixture" },
} }], page_info: { has_next_page: false } } };
const relay = (user: unknown) => ({ require: [["ScheduledServerJS", "handle", null, [{
  __bbox: { require: [["RelayPrefetchedStreamCache", "next", [], ["fixture-cache", {
    __bbox: { result: { data: { xig_user_by_username: user } } },
  }]]] },
}, null, null]]] });
const location = new URL("https://www.instagram.com/public_fixture/?openbiliclaw_instagram_task=1");
const events = Object.assign(new EventTarget(), { location, fetch: async () => new Response("{}") });
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = location as unknown as Location;
globalThis.XMLHttpRequest = class { open() {} } as unknown as typeof XMLHttpRequest;
globalThis.document = { readyState: "complete", querySelectorAll: () => [publicProfile, timeline]
  .map(user => ({ textContent: JSON.stringify(relay(user)) })) } as unknown as Document;
const emitted: Array<{ items: Array<{ id: string; author_id: string }>; error?: string }> = [];
events.addEventListener("openbiliclaw:instagram-response", event => emitted.push(JSON.parse((event as CustomEvent<string>).detail)));
const { parseInstagramObservedPayload } = await import("../src/main/instagram-response-tap.ts");

test("actual MAIN scan combines split SSR public proof and media when pk differs from GraphQL id", () => {
  assert.ok(emitted.some(row => row.items.some(item => item.id === "123" && item.author_id === "42")));
  assert.ok(emitted.every(row => !row.error));
});

test("a matching alternate id cannot override a mismatched media primary key", () => {
  const mismatched = { ...timeline, polaris_ordered_timeline_connection: { ...timeline.polaris_ordered_timeline_connection,
    edges: [{ node: { ...timeline.polaris_ordered_timeline_connection.edges[0].node,
      user: { pk: "43", id: "99", username: "public_fixture" } } }],
  } };
  const result = parseInstagramObservedPayload(relay({ ...publicProfile, ...mismatched }), "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
  assert.ok(result.every(row => !row.affirmative_terminal));
});

for (const pk of ["invalid", "", null, 0, false, "42_123", 42.5, Number.MAX_SAFE_INTEGER + 1]) test(`invalid primary-key ${String(pk)} is not rescued by a valid alternate id`, () => {
  const result = parseInstagramObservedPayload(relay({ ...publicProfile, pk, ...timeline }), "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
  assert.equal(result[0]?.error, "creator_not_public");
});

const withAuthor = (author: Record<string, unknown>) => ({ ...timeline,
  polaris_ordered_timeline_connection: { ...timeline.polaris_ordered_timeline_connection,
    edges: [{ node: { ...timeline.polaris_ordered_timeline_connection.edges[0].node,
      user: { username: "public_fixture", ...author } } }],
  },
});
for (const pk of ["invalid", "", null, 0, false, "42_123", 42.5, Number.MAX_SAFE_INTEGER + 1]) test(`invalid media-author primary-key ${String(pk)} cannot borrow the public proof`, () => {
  const result = parseInstagramObservedPayload(relay({ ...publicProfile, ...withAuthor({ pk, id: "42" }) }), "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
  assert.ok(result.every(row => !row.affirmative_terminal));
});

test("same digits in different id namespaces do not prove the same author", () => {
  const result = parseInstagramObservedPayload(relay({ ...publicProfile, ...withAuthor({ id: "42" }) }), "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
});

test("a shared GraphQL id can use the public profile's known canonical pk", () => {
  const [result] = parseInstagramObservedPayload(relay({ ...publicProfile, ...withAuthor({ id: "99" }) }), "/public_fixture/");
  assert.equal(result.items.length, 1);
  assert.equal(result.items[0].author_id, "42");
});

test("public proof cannot hide another matching profile with unknown privacy", () => {
  const payload = { data: { user: publicProfile,
    xig_user_by_username: { pk: "43", username: "public_fixture", ...timeline } } };
  const result = parseInstagramObservedPayload(payload, "/public_fixture/");
  assert.equal(result.flatMap(row => row.items).length, 0);
  assert.equal(result[0]?.error, "creator_not_public");
});
