import assert from "node:assert/strict";
import test from "node:test";

let scrolls = 0;
const events = Object.assign(new EventTarget(), {
  location: new URL("https://www.instagram.com/popular/technology/?openbiliclaw_instagram_task=1"),
  scrollTo: () => { scrolls += 1; },
});
globalThis.window = events as unknown as Window & typeof globalThis;
globalThis.document = { title: "Instagram", body: { innerText: "", scrollHeight: 100 },
  documentElement: { scrollHeight: 100 } } as unknown as Document;
globalThis.chrome = { runtime: { sendMessage: async () => ({ ok: true }) } } as unknown as typeof chrome;
const { executeInstagramDiscover } = await import("../src/content/instagram/task-executor.ts");
const { INSTAGRAM_REPLAY_EVENT, INSTAGRAM_RESPONSE_EVENT } = await import("../src/content/instagram/response-buffer.ts");

test("topic upstream failure retains media and stops before scrolling", async () => {
  const replay = () => {
    for (const envelope of [
      { route: "topic", collection_id: "topic:xdt_fbsearch__top_serp_graphql",
        items: [{ id: "123", code: "FIXTURE", content_type: "post", url: "https://www.instagram.com/p/FIXTURE/" }],
        has_next_page: false, affirmative_terminal: true },
      { route: "topic", items: [], error: "rate_limited", shape_valid: false },
    ]) events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(envelope) }));
  };
  events.addEventListener(INSTAGRAM_REPLAY_EVENT, replay);
  try {
    const result = await executeInstagramDiscover({ id: "fixture", claim_token: "fixture",
      type: "discover", mode: "topic", query: "technology", max_items: 10, max_pages: 2,
      request_interval_ms: 1000 });
    assert.equal(result.status, "partial");
    assert.equal(result.error, "rate_limited");
    assert.equal(result.items.length, 1);
    assert.equal(result.scope_complete.discover, false);
    assert.equal(result.debug?.authenticated_topic_observed, true);
    assert.equal(scrolls, 0);
  } finally {
    events.removeEventListener(INSTAGRAM_REPLAY_EVENT, replay);
  }
});
