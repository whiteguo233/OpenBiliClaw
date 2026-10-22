import assert from "node:assert/strict";
import test from "node:test";

// The boundary is the real MAIN fetch tap -> replay bridge -> public executor.
// Only browser transport/DOM are fixtures; no internal module is mocked.
let scrolls = 0;
const location = new URL("https://www.instagram.com/public_fixture/?openbiliclaw_instagram_task=1");
const events = Object.assign(new EventTarget(), {
  location,
  scrollTo: () => { scrolls += 1; },
  fetch: async () => {
    const response = new Response("{}", { status: 429 });
    Object.defineProperty(response, "url", { value: "https://www.instagram.com/graphql/query/" });
    return response;
  },
});
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.location = location as unknown as Location;
globalThis.document = {
  title: "Instagram", readyState: "complete",
  body: { innerText: "", scrollHeight: 100 }, documentElement: { scrollHeight: 100 },
  querySelectorAll: () => [{ textContent: JSON.stringify({ data: { user: {
    edge_owner_to_timeline_media: {
      edges: [{ node: { id: "123", shortcode: "FIXTURE", __typename: "GraphImage", user: { is_private: false } } }],
      page_info: { has_next_page: true, end_cursor: "fixture-next" },
    },
  } } }) }],
} as unknown as Document;
globalThis.XMLHttpRequest = class { open() {} } as unknown as typeof XMLHttpRequest;
globalThis.chrome = { runtime: { sendMessage: async () => ({ ok: true }) } } as unknown as typeof chrome;
await import("../src/main/instagram-response-tap.ts");
const { executeInstagramDiscover } = await import("../src/content/instagram/task-executor.ts");

test("creator transport rate limit retains the first page and stops further scrolling", async () => {
  await window.fetch("https://www.instagram.com/graphql/query/");
  const result = await executeInstagramDiscover({ id: "fixture", claim_token: "fixture",
    type: "discover", mode: "creator", username: "public_fixture", max_items: 10,
    max_pages: 2, request_interval_ms: 1000 });
  assert.equal(result.status, "partial");
  assert.equal(result.error, "rate_limited");
  assert.equal(result.items.length, 1);
  assert.equal(result.scope_complete.discover, false);
  assert.equal(scrolls, 0);
});
