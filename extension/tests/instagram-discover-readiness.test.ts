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
const { INSTAGRAM_RESPONSE_EVENT } = await import("../src/content/instagram/response-buffer.ts");
const task = { id: "fixture", claim_token: "fixture", type: "discover" as const, mode: "topic" as const,
  query: "technology", max_items: 10, max_pages: 1, request_interval_ms: 1000 };
const emit = (value: unknown) => events.dispatchEvent(new CustomEvent(INSTAGRAM_RESPONSE_EVENT, { detail: JSON.stringify(value) }));

for (const outcome of ["late-content", "unobserved", "rate-limited", "late-login", "late-challenge"] as const) {
  test(`first response has a separate bounded readiness wait: ${outcome}`, async (t) => {
    scrolls = 0;
    t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 0 });
    try {
      if (outcome === "late-login" || outcome === "late-challenge") setTimeout(() => {
        events.location.pathname = outcome === "late-login" ? "/accounts/login/" : "/challenge/";
      }, 2500);
      else if (outcome !== "unobserved") setTimeout(() => emit(outcome === "late-content"
        ? { route: "topic", collection_id: "topic:fixture", shape_valid: true,
          items: [{ id: "123", code: "FIXTURE", content_type: "post", url: "https://www.instagram.com/p/FIXTURE/" }], has_next_page: true }
        : { route: "topic", shape_valid: false, items: [], error: "rate_limited" }), 2500);
      let result: Awaited<ReturnType<typeof executeInstagramDiscover>> | undefined;
      const running = executeInstagramDiscover(task).then(value => { result = value; });
      for (let elapsed = 0; elapsed < 25000 && !result; elapsed += 500) {
        t.mock.timers.tick(500);
        for (let i = 0; i < 15; i++) await Promise.resolve();
      }
      assert.ok(result, "readiness must terminate without unlimited waiting");
      await running;
      if (outcome === "late-content") {
        assert.equal(result.items.length, 1, "a slow first response must not consume the one-page cap");
        assert.equal(result.status, "partial");
      } else {
        assert.equal(result.error, outcome === "unobserved" ? "response_envelope_unobserved"
          : outcome === "late-login" ? "login_required"
          : outcome === "late-challenge" ? "challenge_required" : "rate_limited");
        assert.equal(result.status, "failed");
        if (outcome === "rate-limited") assert.ok(Date.now() <= 3000, "stop promptly on upstream failure");
      }
      assert.equal(scrolls, 0, "never request another page after the last permitted page");
    } finally { events.location.pathname = "/popular/technology/"; t.mock.timers.reset(); }
  });
}
