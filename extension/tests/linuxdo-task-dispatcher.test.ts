import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  computeLinuxdoTaskTimeoutMs,
  ensureLinuxdoTaskRecovery,
  executeLinuxdoTask,
  handleLinuxdoTaskResult,
  isValidLinuxdoTask,
  pollLinuxdoTaskNow,
  resetLinuxdoTaskRuntimeForTest,
  shouldOpenLinuxdoTaskActive,
  type LinuxdoTask,
} from "../src/background/linuxdo-task-dispatcher.ts";
import { releaseDispatcherMutex } from "../src/background/dispatcher-mutex.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

const CLAIM_TOKEN = "linuxdo-claim-token";

test("Linux.do dispatcher validates every read-only task shape", () => {
  assert.equal(isValidLinuxdoTask({ id: "s", claim_token: CLAIM_TOKEN, type: "search", keywords: ["AI"] }), true);
  assert.equal(isValidLinuxdoTask({ id: "h", claim_token: CLAIM_TOKEN, type: "hot" }), true);
  assert.equal(isValidLinuxdoTask({ id: "f", claim_token: CLAIM_TOKEN, type: "feed" }), true);
  assert.equal(isValidLinuxdoTask({ id: "c", claim_token: CLAIM_TOKEN, type: "creator", creator_urls: ["https://linux.do/u/a"] }), true);
  assert.equal(isValidLinuxdoTask({ id: "r", claim_token: CLAIM_TOKEN, type: "related", related_urls: ["https://linux.do/t/x/1"] }), true);
  assert.equal(isValidLinuxdoTask({ id: "b", claim_token: CLAIM_TOKEN, type: "bootstrap_events", scopes: ["linuxdo_likes"] }), true);
  assert.equal(isValidLinuxdoTask({ id: "s", type: "search", keywords: [] }), false);
  assert.equal(isValidLinuxdoTask({ id: "b-empty", type: "bootstrap_events", scopes: [] }), false);
  assert.equal(isValidLinuxdoTask({ id: "b", type: "bootstrap_events", scopes: ["linuxdo_search"] }), false);
  assert.equal(isValidLinuxdoTask({ id: "mutate", type: "favorite" }), false);
  assert.equal(isValidLinuxdoTask({ id: "slow", type: "feed", request_interval_seconds: 31 }), false);
  assert.equal(isValidLinuxdoTask({ id: "huge", type: "feed", max_items: 301 }), false);
  assert.equal(
    isValidLinuxdoTask({ id: "wide", type: "search", keywords: ["1", "2", "3", "4", "5", "6"] }),
    false,
  );
  assert.equal(isValidLinuxdoTask({ id: "pages", type: "feed", max_pages: 6 }), false);
  assert.equal(isValidLinuxdoTask({ id: "slow-fetch", type: "feed", fetch_timeout_ms: 30_001 }), false);
  assert.equal(isValidLinuxdoTask({
    id: "cursor-missing-lane",
    claim_token: CLAIM_TOKEN,
    type: "search",
    keywords: ["AI"],
    cursor_contract: "page-offset-v1",
    start_cursors: { wrong: { page: 0, offset: 0 } },
  }), false);
  assert.equal(isValidLinuxdoTask({
    id: "cursor-valid",
    claim_token: CLAIM_TOKEN,
    type: "feed",
    cursor_contract: "page-offset-v1",
    start_cursors: { default: { page: 2, offset: 3 } },
  }), true);
  assert.equal(isValidLinuxdoTask({
    id: "cursor-related",
    claim_token: CLAIM_TOKEN,
    type: "related",
    related_urls: ["https://linux.do/t/1"],
    cursor_contract: "page-offset-v1",
    start_cursors: { default: { page: 0, offset: 0 } },
  }), false);
});

test("Linux.do dispatcher timeout scales with bounded inputs and pages", () => {
  assert.ok(
    computeLinuxdoTaskTimeoutMs({
      id: "wide",
      claim_token: CLAIM_TOKEN,
      type: "search",
      keywords: ["a", "b", "c"],
      max_pages: 2,
    }) > computeLinuxdoTaskTimeoutMs({ id: "feed", claim_token: CLAIM_TOKEN, type: "feed" }),
  );
  const bootstrapTimeout = computeLinuxdoTaskTimeoutMs({
    id: "bootstrap",
    claim_token: CLAIM_TOKEN,
    type: "bootstrap_events",
    scopes: ["linuxdo_bookmarks", "linuxdo_likes", "linuxdo_read_history"],
    max_items_per_scope: 300,
    request_interval_seconds: 30,
  });
  assert.ok(bootstrapTimeout > 20 * 60_000);
  assert.ok(bootstrapTimeout <= 29 * 60_000);
});

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("dispatcher isolates discovery in an inactive Linux.do task tab", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-search",
      claim_token: CLAIM_TOKEN,
      type: "search",
      keywords: ["AI"],
    };
    await executeLinuxdoTask(task);
    await flush();
    assert.deepEqual(chromeMock.createdTabs.at(-1), {
      active: false,
      url: "about:blank",
    });
    assert.deepEqual(chromeMock.updatedTabs, [
      { tabId: 42, muted: true },
      { tabId: 42, url: "https://linux.do/?openbiliclaw_linuxdo_task=1" },
    ]);
    assert.deepEqual(chromeMock.executedScripts.at(-1), {
      files: ["dist/content/linuxdo.js"],
      tabId: chromeMock.sentMessages.at(-1)?.tabId,
      world: "ISOLATED",
    });
    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "ok",
      items: [],
      scope_counts: {},
    });
    await flush();
  } finally {
    chromeMock.restore();
  }
});

test("content-script transport failure restarts the same runner once before failing", async () => {
  const chromeMock = installChromeMock();
  let sendAttempts = 0;
  chromeMock.sendMessageImpl = async () => {
    sendAttempts += 1;
    if (chromeMock.reloadedTabs.length === 0) throw new Error("receiver_not_ready");
    return { status: "ok", actions: [] };
  };
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-runner-restart",
      claim_token: CLAIM_TOKEN,
      type: "hot",
    };
    await executeLinuxdoTask(task);
    await flush();
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;
    assert.equal(typeof tabId, "number");
    assert.equal(sendAttempts, 1);

    // Move beyond the readiness retry window. The next bounded retry must
    // reload the same task tab, not release the lease and claim a second task.
    await new Promise((resolve) => setTimeout(resolve, 8_500));
    assert.deepEqual(chromeMock.reloadedTabs, [tabId]);
    const attemptsBeforeRestart = sendAttempts;
    chromeMock.emitTabUpdated(tabId as number, { status: "complete" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(sendAttempts, attemptsBeforeRestart + 1);
    assert.equal(chromeMock.createdTabs.length, 1);

    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty",
      items: [],
      scope_counts: {},
    });
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("only bootstrap task tabs are foregrounded for browser login visibility", () => {
  assert.equal(shouldOpenLinuxdoTaskActive({ id: "b", claim_token: CLAIM_TOKEN, type: "bootstrap_events" }), true);
  for (const [index, type] of ["search", "hot", "feed", "creator", "related"].entries()) {
    assert.equal(
      shouldOpenLinuxdoTaskActive({ id: `d-${index}`, claim_token: CLAIM_TOKEN, type }),
      false,
      `${type} discovery must run in an inactive tab`,
    );
  }
});

test("foreground bootstrap restores the previously active tab after backend ACK", async () => {
  const chromeMock = installChromeMock();
  chromeMock.queryResult = [{ id: 7, url: "https://example.test/", status: "complete" }];
  chromeMock.tabById.set(7, chromeMock.queryResult[0]!);
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-bootstrap-foreground",
      claim_token: CLAIM_TOKEN,
      type: "bootstrap_events",
      scopes: ["linuxdo_bookmarks"],
    };
    await executeLinuxdoTask(task);
    await flush();
    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty",
      items: [],
      scope_counts: {},
    });
    await flush();

    assert.ok(chromeMock.updatedTabs.some((row) => row.tabId === 7 && row.active === true));
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("dispatcher uses authenticated exact Linux.do source task endpoints", () => {
  const source = readFileSync("src/background/linuxdo-task-dispatcher.ts", "utf8");
  assert.match(source, /authenticatedFetch\(await apiUrl\("\/sources\/linuxdo\/next-task"\)/);
  assert.match(source, /authenticatedFetch\(await apiUrl\("\/sources\/linuxdo\/task-result"\)/);
  assert.doesNotMatch(source, /fetch\(await apiUrl\("\/sources\/linuxdo\//);
});

test("service worker wires Linux.do polling, kick and task-result closure", () => {
  const source = readFileSync("src/background/service-worker.ts", "utf8");
  assert.match(source, /startLinuxdoTaskPolling\(\)/);
  assert.match(source, /eventType === "linuxdo_task_available"/);
  assert.match(source, /message\.action === "LINUXDO_TASK_RESULT"/);
  assert.match(source, /handleLinuxdoTaskAlarm\(alarm\.name\)/);
  const runtimeConnect = source.indexOf("const runtimeStreamReady = connectRuntimeStream()");
  const linuxdoRecovery = source.indexOf("await ensureLinuxdoTaskRecovery()");
  const nativeSaveRecovery = source.indexOf("await ensureNativeSaveTaskRecovery()");
  assert.ok(
    runtimeConnect >= 0 &&
      linuxdoRecovery >= 0 &&
      nativeSaveRecovery >= 0 &&
      runtimeConnect < linuxdoRecovery &&
      linuxdoRecovery < nativeSaveRecovery,
  );
});

test("polling never claims a Linux.do row while another source owns the task-tab mutex", async () => {
  const chromeMock = installChromeMock();
  const globalState = globalThis as typeof globalThis & {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  globalState.__OBC_DISPATCHER_MUTEX_HOLDER__ = "reddit";
  globalState.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  try {
    await pollLinuxdoTaskNow();
    assert.deepEqual(chromeMock.fetchCalls, []);
    assert.deepEqual(chromeMock.createdTabs, []);
  } finally {
    chromeMock.restore();
  }
});

test("dispatcher terminally rejects an invalid task after claiming it", async () => {
  const chromeMock = installChromeMock();
  chromeMock.fetchImpl = async (input, init) => {
    const url = String(input);
    chromeMock.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.includes("/sources/linuxdo/next-task")) {
      return new Response(JSON.stringify({
        id: "invalid-linuxdo-task",
        claim_token: CLAIM_TOKEN,
        type: "feed",
        request_interval_seconds: 31,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    await pollLinuxdoTaskNow();
    assert.deepEqual(chromeMock.createdTabs, []);
    const resultCall = chromeMock.fetchCalls.find((call) =>
      call.url.includes("/sources/linuxdo/task-result")
    );
    assert.deepEqual(resultCall?.body, {
      task_id: "invalid-linuxdo-task",
      claim_token: CLAIM_TOKEN,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "invalid_task_payload",
    });
  } finally {
    chromeMock.restore();
  }
});

test("invalid claimed task keeps its exact rejection until backend ACK", async () => {
  const chromeMock = installChromeMock();
  let acceptResult = false;
  chromeMock.fetchImpl = async (input, init) => {
    const url = String(input);
    chromeMock.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.includes("/sources/linuxdo/next-task")) {
      return new Response(JSON.stringify({
        id: "invalid-linuxdo-replay",
        claim_token: CLAIM_TOKEN,
        type: "feed",
        request_interval_seconds: 31,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({ ok: acceptResult }), {
      status: acceptResult ? 200 : 503,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    await pollLinuxdoTaskNow();
    const stored = chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner as {
      final_result?: unknown;
    };
    const expected = {
      task_id: "invalid-linuxdo-replay",
      claim_token: CLAIM_TOKEN,
      status: "failed",
      items: [],
      scope_counts: {},
      error: "invalid_task_payload",
    };
    assert.deepEqual(stored.final_result, expected);

    resetLinuxdoTaskRuntimeForTest();
    acceptResult = true;
    await ensureLinuxdoTaskRecovery();
    await flush();

    assert.deepEqual(
      chromeMock.fetchCalls
        .filter((call) => call.url.includes("/sources/linuxdo/task-result"))
        .at(-1)?.body,
      expected,
    );
    assert.equal(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner, undefined);
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("MV3 recovery rebinds the persisted Linux.do runner before accepting its result", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-recovered",
      claim_token: CLAIM_TOKEN,
      type: "feed",
    };
    await executeLinuxdoTask(task);
    await flush();
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;
    assert.equal(typeof tabId, "number");
    assert.ok(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner);
    const initialExecuteMessages = chromeMock.sentMessages.length;

    resetLinuxdoTaskRuntimeForTest();
    await ensureLinuxdoTaskRecovery();
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.ok(chromeMock.sentMessages.length > initialExecuteMessages);
    const replay = chromeMock.sentMessages.at(-1)?.message as {
      data?: { task_id?: string };
    };
    assert.equal(replay.data?.task_id, task.id);
    await handleLinuxdoTaskResult(
      {
        task_id: task.id,
        claim_token: task.claim_token,
        status: "empty",
        items: [],
        scope_counts: {},
      },
      { id: tabId, url: "https://linux.do/?openbiliclaw_linuxdo_task=1" },
    );
    await flush();

    assert.ok(chromeMock.removedTabs.includes(tabId as number));
    assert.ok(
      chromeMock.fetchCalls.some((call) => call.url.includes("/sources/linuxdo/task-result")),
    );
    assert.equal(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner, undefined);
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("full extension reload refreshes the runner document before replay", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-full-reload",
      claim_token: CLAIM_TOKEN,
      type: "feed",
      max_items: 2,
    };
    await executeLinuxdoTask(task);
    await new Promise((resolve) => setTimeout(resolve, 20));
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;
    const initialMessages = chromeMock.sentMessages.length;
    assert.equal(typeof tabId, "number");

    delete chromeMock.runtimeSessionStorage.openbiliclaw_linuxdo_runtime_session;
    resetLinuxdoTaskRuntimeForTest();
    await ensureLinuxdoTaskRecovery();
    await flush();

    assert.deepEqual(chromeMock.reloadedTabs, [tabId]);
    assert.equal(chromeMock.sentMessages.length, initialMessages);
    chromeMock.emitTabUpdated(tabId as number, { status: "complete" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(chromeMock.sentMessages.length, initialMessages + 1);

    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty",
      items: [],
      scope_counts: {},
    }, { id: tabId, url: "https://linux.do/?openbiliclaw_linuxdo_task=1" });
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("MV3 recovery waits for an occupied mutex then restores without claiming", async () => {
  const chromeMock = installChromeMock();
  const globalState = globalThis as typeof globalThis & {
    __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
    __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
  };
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-recovery-waits",
      claim_token: CLAIM_TOKEN,
      type: "feed",
    };
    await executeLinuxdoTask(task);
    await flush();
    assert.ok(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner);

    resetLinuxdoTaskRuntimeForTest();
    globalState.__OBC_DISPATCHER_MUTEX_HOLDER__ = "reddit";
    globalState.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();

    let recoveryFinished = false;
    const recovery = ensureLinuxdoTaskRecovery().then(() => {
      recoveryFinished = true;
    });
    const poll = pollLinuxdoTaskNow();
    await flush();

    assert.equal(recoveryFinished, false);
    assert.equal(
      chromeMock.fetchCalls.some((call) => call.url.includes("/sources/linuxdo/next-task")),
      false,
    );

    releaseDispatcherMutex("reddit");
    await recovery;
    await poll;

    assert.equal(recoveryFinished, true);
    assert.equal(
      chromeMock.fetchCalls.some((call) => call.url.includes("/sources/linuxdo/next-task")),
      false,
    );
    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty",
      items: [],
      scope_counts: {},
    });
    await flush();
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("unacknowledged final survives MV3 recycle and a Linux.do redirect", async () => {
  const chromeMock = installChromeMock();
  let acceptResult = false;
  chromeMock.fetchImpl = async (input, init) => {
    const url = String(input);
    chromeMock.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.includes("/sources/linuxdo/task-result") && !acceptResult) {
      return new Response(JSON.stringify({ ok: false }), { status: 503 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-final-replay",
      claim_token: CLAIM_TOKEN,
      type: "feed",
    };
    await executeLinuxdoTask(task);
    await flush();
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;
    assert.equal(typeof tabId, "number");
    chromeMock.tabById.set(tabId as number, {
      id: tabId,
      status: "complete",
      url: "https://linux.do/login-return-without-task-marker",
    });

    const result = {
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty" as const,
      items: [],
      scope_counts: {},
    };
    await assert.rejects(() => handleLinuxdoTaskResult(result));
    const stored = chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner as {
      final_result?: unknown;
    };
    assert.deepEqual(stored.final_result, result);
    assert.equal(chromeMock.removedTabs.includes(tabId as number), false);

    resetLinuxdoTaskRuntimeForTest();
    acceptResult = true;
    await ensureLinuxdoTaskRecovery();
    await flush();
    await flush();

    const posted = chromeMock.fetchCalls
      .filter((call) => call.url.includes("/sources/linuxdo/task-result"))
      .at(-1)?.body;
    assert.deepEqual(posted, result);
    assert.ok(chromeMock.removedTabs.includes(tabId as number));
    assert.equal(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner, undefined);
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("stale result cannot overwrite or clean up a newer in-memory task", async () => {
  const chromeMock = installChromeMock();
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-current-owner",
      claim_token: CLAIM_TOKEN,
      type: "feed",
    };
    await executeLinuxdoTask(task);
    await flush();
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;

    await assert.rejects(
      () => handleLinuxdoTaskResult(
        {
          task_id: "linuxdo-stale-owner",
          claim_token: "stale-claim",
          status: "empty",
          items: [],
          scope_counts: {},
        },
        { id: tabId, url: "https://linux.do/?openbiliclaw_linuxdo_task=1" },
      ),
      /owner_mismatch/,
    );

    assert.equal(
      chromeMock.fetchCalls.some((call) => call.url.includes("/sources/linuxdo/task-result")),
      false,
    );
    assert.equal(chromeMock.removedTabs.includes(tabId as number), false);
    assert.ok(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner);

    await handleLinuxdoTaskResult(
      {
        task_id: task.id,
        claim_token: task.claim_token,
        status: "empty",
        items: [],
        scope_counts: {},
      },
      { id: tabId, url: "https://linux.do/?openbiliclaw_linuxdo_task=1" },
    );
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});

test("backend claim rejection stops exact-final retries and releases the runner", async () => {
  const chromeMock = installChromeMock();
  chromeMock.fetchImpl = async (input, init) => {
    const url = String(input);
    chromeMock.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(JSON.stringify({ detail: "task_claim_conflict" }), { status: 409 });
  };
  try {
    const task: LinuxdoTask = {
      id: "linuxdo-rejected-owner",
      claim_token: CLAIM_TOKEN,
      type: "feed",
    };
    await executeLinuxdoTask(task);
    await flush();
    const tabId = chromeMock.sentMessages.at(-1)?.tabId;

    await handleLinuxdoTaskResult({
      task_id: task.id,
      claim_token: task.claim_token,
      status: "empty",
      items: [],
      scope_counts: {},
    });
    await flush();

    assert.equal(
      chromeMock.fetchCalls.filter((call) => call.url.includes("/sources/linuxdo/task-result"))
        .length,
      1,
    );
    assert.ok(chromeMock.removedTabs.includes(tabId as number));
    assert.equal(chromeMock.sessionStorage.openbiliclaw_linuxdo_task_runner, undefined);
  } finally {
    resetLinuxdoTaskRuntimeForTest();
    chromeMock.restore();
  }
});
