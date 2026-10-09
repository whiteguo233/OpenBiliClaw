import assert from "node:assert/strict";
import test from "node:test";
import { installChromeMock } from "./helpers/chrome-mock.ts";

const key = "openbiliclaw_instagram_task_state_v1";

for (const changedMessage of ["identity", "bootstrap", "final"] as const) {
  test(`recovery rejects an account switch in ${changedMessage} without mixing personal rows`, async () => {
    const browser = installChromeMock();
    const task = { id: "account-switch", claim_token: "claim", type: "bootstrap_events" as const,
      scopes: ["instagram_saved" as const], max_items_per_scope: 3, max_pages_per_scope: 2 };
    const url = "https://www.instagram.com/your_activity/interactions/likes/?openbiliclaw_instagram_task=1";
    const item = (id: string) => ({ scope: "instagram_saved" as const, id, code: `FIXTURE${id}`,
      content_type: "post" as const, url: `https://www.instagram.com/p/FIXTURE${id}/` });
    browser.tabById.set(42, { id: 42, url, status: "loading" });
    browser.sessionStorage[key] = { version: 1, task, tabId: 42, lastProgress: null,
      idleDeadlineAt: Date.now() + 60_000, absoluteDeadlineAt: Date.now() + 120_000,
      dispatchAttempts: 0, reloadUsed: false, accountId: "111", acceptedItems: [item("1001")],
      scopeCounts: { instagram_saved: 1 }, scopeComplete: { instagram_saved: false } };
    try {
      const dispatcher = await import(`../src/background/instagram-task-dispatcher.ts?account-switch-${changedMessage}`);
      await dispatcher.ensureInstagramTaskRecovery();
      const sender = { tab: { id: 42, url }, url };
      if (changedMessage === "final") {
        await dispatcher.handleInstagramTaskResult({ task_id: task.id, claim_token: task.claim_token,
          status: "ok", account_id: "222", items: [item("2002")], scope_counts: { instagram_saved: 1 },
          scope_complete: { instagram_saved: true },
          debug: { response_observed: true, terminal_evidence: "fixture" } }, sender);
      } else {
        await assert.rejects(dispatcher.handleInstagramTaskProgress({ task_id: task.id,
          claim_token: task.claim_token, phase: changedMessage, account_id: "222",
          items: changedMessage === "bootstrap" ? [item("2002")] : [], accepted: true }, sender),
        /instagram_account_changed/);
      }
      const finals = browser.fetchCalls.filter(call => call.url.endsWith("/sources/instagram/task-result"));
      assert.equal(finals.length, 1);
      const result = finals[0].body as { status: string; error: string; items: unknown[] };
      assert.equal(result.status, "failed");
      assert.equal(result.error, "instagram_account_changed");
      assert.deepEqual(result.items, []);
      assert.equal(browser.sessionStorage[key], undefined);
    } finally { browser.restore(); }
  });
}

for (const pendingFinal of [false, true]) {
  test(`browser recovery preserves an unrelated reused tab ID (pending final: ${pendingFinal})`, async () => {
    const browser = installChromeMock();
    chrome.storage.session.get = async () => ({});
    chrome.storage.session.remove = async () => {};
    browser.tabById.set(97, { id: 97, url: "https://example.com/user-page", status: "complete" });
    const result = {
      task_id: "reused-tab-claim", claim_token: "original-claim", status: "failed",
      items: [], scope_counts: { discover: 0 }, scope_complete: { discover: false },
      error: "public_page_unavailable",
    };
    browser.sessionStorage[key] = {
      version: 1,
      task: {
        id: result.task_id, type: "discover", claim_token: result.claim_token,
        mode: "topic", query: "music", max_items: 3, max_pages: 2,
      },
      tabId: 97, lastProgress: null, dispatchAttempts: 0, reloadUsed: false,
      idleDeadlineAt: Date.now() + 60_000, absoluteDeadlineAt: Date.now() + 120_000,
      acceptedItems: [], scopeCounts: { discover: 0 }, scopeComplete: { discover: false },
      ...(pendingFinal ? { pendingResult: result, pendingResultBody: JSON.stringify(result) } : {}),
    };
    try {
      const dispatcher = await import(`../src/background/instagram-task-dispatcher.ts?reused-tab-${pendingFinal}`);
      await dispatcher.ensureInstagramTaskRecovery();
      const results = browser.fetchCalls.filter(call => call.url.endsWith("/sources/instagram/task-result"));
      assert.equal(results.length, 1);
      assert.equal((results[0].body as { task_id: string }).task_id, result.task_id);
      assert.equal((results[0].body as { error: string }).error,
        pendingFinal ? "public_page_unavailable" : "recovery_tab_not_owned");
      assert.deepEqual(browser.removedTabs, []);
      assert.ok(browser.tabById.has(97), "the ordinary user page must remain open");
      assert.equal(browser.sessionStorage[key], undefined);
      assert.equal(browser.createdTabs.length, 0);
    } finally {
      browser.restore();
    }
  });
}

test("browser restart terminalizes the original lost-tab claim instead of orphaning it", async () => {
  const browser = installChromeMock();
  // A full browser/extension restart erases session but retains local storage.
  chrome.storage.session.get = async () => ({});
  chrome.storage.session.remove = async () => {};
  browser.getImpl = async () => { throw new Error("No tab with this ID"); };
  browser.sessionStorage[key] = {
    version: 1,
    task: {
      id: "lost-tab-claim", type: "discover", claim_token: "original-claim",
      mode: "topic", query: "music", max_items: 3, max_pages: 2,
    },
    tabId: 99, lastProgress: null, dispatchAttempts: 0, reloadUsed: false,
    idleDeadlineAt: Date.now() + 60_000, absoluteDeadlineAt: Date.now() + 120_000,
    acceptedItems: [], scopeCounts: { discover: 0 }, scopeComplete: { discover: false },
  };
  try {
    const dispatcher = await import("../src/background/instagram-task-dispatcher.ts?lost-browser");
    await dispatcher.ensureInstagramTaskRecovery();
    const results = browser.fetchCalls.filter(call => call.url.endsWith("/sources/instagram/task-result"));
    assert.equal(results.length, 1);
    assert.equal((results[0].body as { error: string }).error, "recovery_tab_gone");
    assert.equal((results[0].body as { task_id: string }).task_id, "lost-tab-claim");
    assert.equal(browser.sessionStorage[key], undefined);
    assert.equal(browser.createdTabs.length, 0);
  } finally {
    browser.restore();
  }
});

test("an unacknowledged final survives empty session storage and replays identical bytes", async () => {
  const browser = installChromeMock();
  chrome.storage.session.get = async () => ({});
  chrome.storage.session.remove = async () => {};
  const result = {
    task_id: "pending-final", claim_token: "same-claim", status: "failed",
    items: [], scope_counts: { discover: 0 }, scope_complete: { discover: false },
    error: "public_page_unavailable",
  };
  const body = JSON.stringify(result, null, 2);
  browser.sessionStorage[key] = {
    version: 1,
    task: {
      id: result.task_id, type: "discover", claim_token: result.claim_token,
      mode: "topic", query: "unavailable", max_items: 3, max_pages: 2,
    },
    tabId: 98, lastProgress: null, dispatchAttempts: 0, reloadUsed: false,
    idleDeadlineAt: 1, absoluteDeadlineAt: 1,
    acceptedItems: [], scopeCounts: { discover: 0 }, scopeComplete: { discover: false },
    pendingResult: result, pendingResultBody: body,
  };
  const wire: string[] = [];
  let acknowledged = false;
  browser.fetchImpl = async (input, init) => {
    if (String(input).endsWith("/sources/instagram/task-result")) {
      wire.push(String(init?.body));
      return new Response("{}", { status: acknowledged ? 200 : 503 });
    }
    return new Response("{}", { status: 200 });
  };
  try {
    const first = await import("../src/background/instagram-task-dispatcher.ts?pending-final-first");
    await first.ensureInstagramTaskRecovery();
    assert.ok(browser.sessionStorage[key], "no ACK must retain the recovery record");
    acknowledged = true;
    const restarted = await import("../src/background/instagram-task-dispatcher.ts?pending-final-restart");
    await restarted.ensureInstagramTaskRecovery();
    assert.equal(wire.length, 4);
    assert.ok(wire.every(value => value === body));
    assert.equal(browser.sessionStorage[key], undefined);
    assert.equal(browser.createdTabs.length, 0);
  } finally {
    browser.restore();
  }
});
