import assert from "node:assert/strict";
import test from "node:test";
import { installChromeMock } from "./helpers/chrome-mock.ts";
import { executeInstagramBootstrap, type InstagramTaskResult } from "../src/content/instagram/task-executor.ts";

test("legal slow pacing reaches durable page progress before dispatcher idle timeout", async (t) => {
  const browser = installChromeMock();
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 0 });
  const key = "openbiliclaw_instagram_task_state_v1";
  const task = { id: "paced-page", type: "bootstrap_events" as const, claim_token: "fixture",
    scopes: ["instagram_saved" as const], max_items_per_scope: 3, max_pages_per_scope: 1,
    request_interval_ms: 30_000 };
  const url = "https://www.instagram.com/your_activity/interactions/likes/?openbiliclaw_instagram_task=1";
  const sender = { tab: { id: 42, url }, url };
  browser.tabById.set(42, { id: 42, url, status: "loading" });
  browser.sessionStorage[key] = { version: 1, task, tabId: 42, lastProgress: null,
    idleDeadlineAt: 90_000, absoluteDeadlineAt: 720_000, dispatchAttempts: 0,
    reloadUsed: false, acceptedItems: [], scopeCounts: { instagram_saved: 0 },
    scopeComplete: { instagram_saved: false } };
  const flush = async (count = 30) => { for (let i = 0; i < count; i++) await Promise.resolve(); };
  try {
    const dispatcher = await import("../src/background/instagram-task-dispatcher.ts?paced-page-test");
    await dispatcher.ensureInstagramTaskRecovery();
    chrome.runtime.sendMessage = async ({ data }) => {
      try { await dispatcher.handleInstagramTaskProgress(data, sender); return { ok: true }; }
      catch { return { ok: false }; }
    };
    let settled: InstagramTaskResult | undefined;
    let error: unknown;
    void executeInstagramBootstrap(task, async (input, init) => {
      assert.equal(init?.signal?.aborted, false);
      t.mock.timers.tick(1);
      await flush();
      const path = new URL(String(input)).pathname;
      if (path === "/") return new Response('<script data-sjs>{"__bbox":{"define":[["PolarisViewer",[],{"id":"42","data":{"id":"42","username":"fixture"}},1]]}}</script>', { headers: { "content-type": "text/html" } });
      return new Response(JSON.stringify(path.endsWith("web_form_data/")
        ? { status: "ok", form_data: { username: "fixture" } }
        : { status: "ok", items: [], more_available: false }), { headers: { "content-type": "application/json" } });
    }, async ms => { t.mock.timers.tick(ms); await flush(); })
      .then(result => { settled = result; }, failure => { error = failure; });
    await flush(800);
    const results = () => browser.fetchCalls.filter(call => call.url.endsWith("/sources/instagram/task-result"));
    assert.deepEqual(results(), [], "must not timeout a normally paced page before durable progress");
    assert.equal(error, undefined);
    assert.equal(settled?.status, "empty");
    assert.equal(settled?.scope_complete.instagram_saved, true);
    await dispatcher.handleInstagramTaskResult(settled!, sender);
    assert.equal(results().length, 1);
    assert.equal(results()[0].body.status, "empty");
  } finally { t.mock.timers.reset(); browser.restore(); }
});
