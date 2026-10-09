import assert from "node:assert/strict";
import test from "node:test";

const profile = { id: "42", username: "public_fixture", is_private: false };
const location = new URL("https://www.instagram.com/public_fixture/?openbiliclaw_instagram_task=1");
const events = Object.assign(new EventTarget(), {
  location,
  fetch: async () => {
    const response = new Response(JSON.stringify({ data: { user: profile } }));
    Object.defineProperty(response, "url", { value: "https://www.instagram.com/graphql/query/" });
    return response;
  },
});
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = location as unknown as Location;
globalThis.XMLHttpRequest = class { open() {} } as unknown as typeof XMLHttpRequest;
globalThis.document = { readyState: "complete", querySelectorAll: () => [{
  textContent: JSON.stringify({ data: { xig_user_by_username: {
    polaris_ordered_timeline_connection: { edges: [{ node: {
      id: "123", code: "FIXTURE", user: { id: "42", username: "public_fixture" },
    } }], page_info: { has_next_page: false } },
  } } }),
}] } as unknown as Document;
const emitted: Array<{ route: string; items: unknown[]; error?: string }> = [];
events.addEventListener("openbiliclaw:instagram-response", event => {
  emitted.push(JSON.parse((event as CustomEvent<string>).detail));
});
await import("../src/main/instagram-response-tap.ts");

test("late native profile evidence unlocks matching SSR media, never the viewer or another creator", async () => {
  assert.deepEqual(emitted, [], "unknown visibility must not cross the MAIN bridge");
  profile.username = "another_creator";
  await window.fetch("/graphql/query/");
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(emitted, []);
  profile.username = "public_fixture";
  await window.fetch("/graphql/query/");
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].items.length, 1);
  profile.is_private = true;
  await window.fetch("/graphql/query/");
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(emitted.at(-1)?.error, "creator_not_public");
  assert.deepEqual(emitted.at(-1)?.items, []);
});
