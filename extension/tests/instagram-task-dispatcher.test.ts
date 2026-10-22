import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  instagramTaskSenderMatches,
  instagramTaskUrl,
  isValidInstagramTask,
  postInstagramTaskResult,
  sanitizeInstagramTaskResult,
  type InstagramResultTransport,
} from "../src/background/instagram-task-dispatcher.ts";
import {
  isInstagramTaskTabLocation,
  withInstagramTaskMarker,
} from "../src/content/instagram/task-mode.ts";
import type { InstagramTaskResult } from "../src/content/instagram/task-executor.ts";

const discover = {
  id: "task-1",
  type: "discover" as const,
  claim_token: "claim-1",
  mode: "topic" as const,
  query: "machine learning",
  source_keyword_id: 7,
  max_items: 30,
  max_pages: 3,
  request_interval_ms: 3_000,
};

test("validates frozen task shape, caps and throttle", () => {
  assert.equal(isValidInstagramTask(discover), true);
  assert.equal(isValidInstagramTask({ ...discover, source_keyword_id: 0 }), false);
  assert.equal(isValidInstagramTask({ ...discover, max_items: 501 }), false);
  assert.equal(isValidInstagramTask({ ...discover, request_interval_ms: 999 }), false);
  assert.equal(isValidInstagramTask({ ...discover, request_interval_ms: 30_001 }), false);
  assert.equal(isValidInstagramTask({ ...discover, claim_token: "" }), false);
  assert.equal(isValidInstagramTask({
    id: "bootstrap",
    type: "bootstrap_events",
    claim_token: "claim",
    scopes: ["instagram_liked", "instagram_saved", "instagram_following"],
    max_items_per_scope: 300,
    max_pages_per_scope: 20,
    request_interval_ms: 3_000,
  }), true);
});

test("task marker is a stable query parameter with hash compatibility", () => {
  const marked = withInstagramTaskMarker("https://www.instagram.com/popular/ai/?existing=1#keep");
  const parsed = new URL(marked);
  assert.equal(parsed.searchParams.get("existing"), "1");
  assert.equal(parsed.searchParams.get("openbiliclaw_instagram_task"), "1");
  assert.equal(new URLSearchParams(parsed.hash.slice(1)).get("openbiliclaw_instagram_task"), "1");
  assert.equal(new URLSearchParams(parsed.hash.slice(1)).has("keep"), true);
  assert.equal(isInstagramTaskTabLocation({ search: parsed.search, hash: parsed.hash }), true);
  assert.equal(isInstagramTaskTabLocation({ hash: "#openbiliclaw_instagram_task=1" }), true);
  assert.equal(isInstagramTaskTabLocation({ search: "?foo=1" }), false);
  assert.equal(new URL(instagramTaskUrl(discover)).searchParams.get("openbiliclaw_instagram_task"), "1");
});

test("sanitizer enforces observed evidence, task caps, scope ownership and secret-free debug", () => {
  const rows = Array.from({ length: 40 }, (_, index) => ({
    id: String(index + 1),
    code: `C${index + 1}`,
    content_type: "post" as const,
    url: `https://www.instagram.com/p/C${index + 1}/`,
  }));
  const safe = sanitizeInstagramTaskResult({
    task_id: discover.id,
    claim_token: discover.claim_token,
    status: "partial",
    items: rows,
    scope_counts: { discover: 40, injected: 99 },
    scope_complete: { discover: false, injected: true },
    error: "bounded_public_snapshot",
    debug: {
      response_observed: true,
      terminal_evidence: "bounded_nonterminal_page",
      cookie: "secret",
      raw_body: "secret",
    },
  }, { ...discover, max_items: 30 });
  assert.ok(safe);
  assert.equal(safe?.items.length, 30);
  assert.deepEqual(safe?.scope_counts, { discover: 40 });
  assert.deepEqual(safe?.scope_complete, { discover: false });
  assert.equal("cookie" in (safe?.debug || {}), false);
  assert.equal("raw_body" in (safe?.debug || {}), false);

  const rejected = sanitizeInstagramTaskResult({
    task_id: discover.id,
    claim_token: discover.claim_token,
    status: "empty",
    items: [],
    scope_counts: { discover: 0 },
    scope_complete: { discover: true },
    debug: { terminal_evidence: "empty" },
  }, discover);
  assert.equal(rejected?.status, "failed");
  assert.equal(rejected?.error, "terminal_evidence_missing");
  assert.deepEqual(rejected?.scope_complete, { discover: false });
});

test("failed bootstrap preserves login/challenge classification without a fabricated account id", () => {
  const task = {
    id: "bootstrap",
    type: "bootstrap_events" as const,
    claim_token: "claim",
    scopes: ["instagram_liked" as const],
    max_items_per_scope: 300,
    max_pages_per_scope: 20,
  };
  for (const error of ["login_required", "challenge_required"]) {
    const result = sanitizeInstagramTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "failed",
      items: [],
      scope_counts: {},
      scope_complete: { instagram_liked: false },
      error,
      debug: { identity_resolved: false, response_observed: false },
    }, task);
    assert.equal(result?.status, "failed");
    assert.equal(result?.error, error);
    assert.equal(result?.account_id, undefined);
    assert.deepEqual(result?.scope_complete, { instagram_liked: false });
  }
});

test("task results are bound to exact tab and Instagram task URL", () => {
  const sender = {
    tab: { id: 12, url: instagramTaskUrl(discover) },
    url: instagramTaskUrl(discover),
  } as chrome.runtime.MessageSender;
  assert.equal(instagramTaskSenderMatches(12, sender), true);
  assert.equal(instagramTaskSenderMatches(13, sender), false);
  assert.equal(instagramTaskSenderMatches(12, {
    tab: { id: 12, url: "https://example.com/?openbiliclaw_instagram_task=1" },
  } as chrome.runtime.MessageSender), false);
});

test("result retry reuses byte-identical payload until 2xx ACK", async () => {
  const result: InstagramTaskResult = {
    task_id: "task-1",
    claim_token: "claim-1",
    status: "empty",
    items: [],
    scope_counts: { discover: 0 },
    scope_complete: { discover: true },
    debug: { response_observed: true, terminal_evidence: "page_info_terminal" },
  };
  const bodies: string[] = [];
  let attempts = 0;
  const transport: InstagramResultTransport = {
    resolveUrl: async (path) => `http://localhost${path}`,
    fetch: async (_url, init) => {
      attempts += 1;
      bodies.push(String(init.body));
      return { ok: attempts === 3, status: attempts === 3 ? 204 : 503 };
    },
    sleep: async () => {},
  };
  await postInstagramTaskResult(result, transport, 3);
  assert.equal(attempts, 3);
  assert.equal(new Set(bodies).size, 1);
  assert.equal(bodies[0], JSON.stringify(result));
});

test("dispatcher source locks recovery, mutex-before-claim, readiness retry and one reload", async () => {
  const source = await readFile(new URL("../src/background/instagram-task-dispatcher.ts", import.meta.url), "utf8");
  assert.match(source, /chrome\.storage\?\.session/);
  assert.match(source, /pendingResult/);
  assert.match(source, /pendingResultBody/);
  assert.ok(source.indexOf("tryAcquireDispatcherMutex(OWNER)") < source.indexOf("const task = await fetchNextTask()"));
  assert.match(source, /SEND_MESSAGE_MAX_ATTEMPTS/);
  assert.match(source, /reloadUsed/);
  assert.match(source, /await chrome\.tabs\.reload/);
  assert.match(source, /ensureInstagramTaskRecovery/);
  assert.match(source, /acceptedItems/);
  assert.match(source, /durable_partial_progress_before_timeout/);
  assert.match(source, /progress\.claim_token !== state\.task\.claim_token/);
  const finalizeStart = source.indexOf("async function finalize(");
  const retryStart = source.indexOf("async function retryPendingResult(");
  const finalizeSource = source.slice(finalizeStart, retryStart);
  assert.ok(finalizeSource.indexOf("await persistState()") < finalizeSource.indexOf("await postInstagramTaskResult("));
  assert.doesNotMatch(finalizeSource, /persistState\(\)\.catch/);
  assert.match(finalizeSource, /catch \(error\)[\s\S]*scheduleResultRetry\(\);[\s\S]*throw error/);
  assert.match(finalizeSource, /error instanceof InstagramTerminalResultRejection[\s\S]*cleanupAfterAck/);
  const retrySource = source.slice(retryStart, source.indexOf("function timeoutResult("));
  assert.ok(retrySource.indexOf("await persistState()") < retrySource.indexOf("await postInstagramTaskResult("));
  const progressHandler = source.slice(
    source.indexOf("export function handleInstagramTaskProgress("),
    source.indexOf("export function handleInstagramTaskResult("),
  );
  const resultHandler = source.slice(
    source.indexOf("export function handleInstagramTaskResult("),
    source.indexOf("async function resumeRecoveredTask("),
  );
  assert.match(progressHandler, /ensureInstagramTaskRecovery\(\)/);
  assert.match(resultHandler, /ensureInstagramTaskRecovery\(\)/);
});

test("service worker awaits Instagram recovery before progress/result ACK", async () => {
  const source = await readFile(new URL("../src/background/service-worker.ts", import.meta.url), "utf8");
  for (const action of ["INSTAGRAM_TASK_PROGRESS", "INSTAGRAM_TASK_RESULT"]) {
    const start = source.indexOf(`message.action === "${action}"`);
    assert.ok(start >= 0);
    const block = source.slice(start, source.indexOf("return true;", start));
    assert.match(block, /ensureInstagramTaskRecovery\(\)/);
    assert.ok(block.indexOf("ensureInstagramTaskRecovery()") < block.indexOf("handleInstagramTask"));
  }
});

test("task content entry never starts passive collector in task tabs", async () => {
  const source = await readFile(new URL("../src/content/instagram.ts", import.meta.url), "utf8");
  assert.doesNotMatch(source, /startCollector|BEHAVIOR_EVENT/);
  assert.match(source, /installInstagramTaskMessageListener/);
});
