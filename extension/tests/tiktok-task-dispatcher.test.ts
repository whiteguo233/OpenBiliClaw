/**
 * Tests for the TikTok background task dispatcher's pure helpers,
 * durable pending-result delivery, and poll lifecycle.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildTiktokExecuteMessageData,
  buildTiktokTaskUrl,
  computeTiktokTaskTimeoutMs,
  executeTask,
  flushPendingTiktokTaskResult,
  isValidTiktokTask,
  pollTiktokTaskNow,
  pollTiktokTaskOnce,
  postTiktokNativeSaveResult,
  postTiktokTaskResult,
  resetTiktokTaskStateForTest,
  TIKTOK_PENDING_RESULT_LOCAL_KEY,
  type TiktokTask,
} from "../src/background/tiktok-task-dispatcher.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";
import type { NativeSaveResult, NativeSaveTask } from "../src/shared/native-save.ts";

const nativeTask: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174099",
  type: "native_save",
  platform: "tiktok",
  platform_slug: "tiktok",
  item_key: "tiktok:7234567890123456789",
  content_id: "7234567890123456789",
  content_url: "https://www.tiktok.com/@creator/video/7234567890123456789",
  content_type: "video",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "TikTok Favorites",
};

type DispatcherMutexGlobal = typeof globalThis & {
  __OBC_DISPATCHER_MUTEX_HOLDER__?: string;
  __OBC_DISPATCHER_MUTEX_HELD_SINCE__?: number;
};

function clearDispatcherMutex(): void {
  const shared = globalThis as DispatcherMutexGlobal;
  delete shared.__OBC_DISPATCHER_MUTEX_HOLDER__;
  delete shared.__OBC_DISPATCHER_MUTEX_HELD_SINCE__;
}

const likeTask: TiktokTask = { id: "task-like", type: "tt_like" };

// ---------------------------------------------------------------------------
// Task validation / URL / timeout / message shape
// ---------------------------------------------------------------------------

test("isValidTiktokTask accepts scope-typed tasks with optional payload", () => {
  for (const type of ["tt_like", "tt_collect", "tt_history", "tt_follow"] as const) {
    assert.equal(isValidTiktokTask({ id: "t", type }), true);
  }
  assert.equal(
    isValidTiktokTask({
      id: "t",
      type: "tt_history",
      payload: { max_items: 200, max_scroll_rounds: 10, max_stagnant_scroll_rounds: 4 },
    }),
    true,
  );
});

test("isValidTiktokTask rejects malformed input", () => {
  assert.equal(isValidTiktokTask(null), false);
  assert.equal(isValidTiktokTask("tt_like"), false);
  assert.equal(isValidTiktokTask({}), false);
  assert.equal(isValidTiktokTask({ id: "", type: "tt_like" }), false);
  assert.equal(isValidTiktokTask({ id: "t" }), false);
  assert.equal(isValidTiktokTask({ id: "t", type: "bootstrap_profile" }), false);
  assert.equal(isValidTiktokTask({ id: "t", type: "dy_like" }), false);
  assert.equal(isValidTiktokTask({ id: "t", type: "tt_like", payload: "nope" }), false);
  assert.equal(
    isValidTiktokTask({ id: "t", type: "tt_like", payload: { max_items: "300" } }),
    false,
  );
});

test("tiktok task native_save union closes through the exact authenticated result contract", async () => {
  assert.equal(isValidTiktokTask(nativeTask), true);
  assert.equal(
    isValidTiktokTask({ ...nativeTask, platform: "douyin", platform_slug: "dy" }),
    false,
  );
  assert.equal(buildTiktokTaskUrl(nativeTask), nativeTask.content_url);
  const result: NativeSaveResult = {
    task_id: nativeTask.id,
    item_key: nativeTask.item_key,
    status: "synced",
    error_code: "",
    error_message: "",
  };
  const calls: unknown[] = [];
  await executeTask(nativeTask, {
    run: async (receivedTask, slug, postResult) => {
      calls.push([receivedTask, slug]);
      await postResult(result);
    },
    postResult: async (received) => { calls.push(received); },
  });
  assert.deepEqual(calls, [[nativeTask, "tiktok"], result]);

  const requests: Array<[string, RequestInit]> = [];
  const state = installChromeMock();
  try {
  await postTiktokNativeSaveResult(result, {
    resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
    fetch: async (input, init) => { requests.push([input, init]); return { ok: true, status: 200 }; },
  });
  assert.ok(requests[0]?.[1].signal instanceof AbortSignal);
  assert.deepEqual(requests.map(([url, init]) => {
    const { signal: _signal, ...rest } = init;
    return [url, rest];
  }), [[
    "http://127.0.0.1:8420/api/sources/tiktok/task-result",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    },
  ]]);
  } finally { state.restore(); }
});

test("buildTiktokTaskUrl lands on the tiktok.com homepage with the task marker", () => {
  assert.equal(
    buildTiktokTaskUrl(likeTask),
    "https://www.tiktok.com/?openbiliclaw_tiktok_task=1",
  );
});

test("computeTiktokTaskTimeoutMs scales with scroll rounds and caps at the ceiling", () => {
  assert.equal(computeTiktokTaskTimeoutMs(likeTask), 90_000);
  const withRounds = computeTiktokTaskTimeoutMs({
    id: "t",
    type: "tt_collect",
    payload: { max_scroll_rounds: 15 },
  });
  assert.equal(withRounds, 90_000 + 15 * 3_000);
  const huge = computeTiktokTaskTimeoutMs({
    id: "t",
    type: "tt_like",
    payload: { max_scroll_rounds: 10_000 },
  });
  assert.equal(huge, 360_000);
});

test("buildTiktokExecuteMessageData fills defaults and forwards payload overrides", () => {
  assert.deepEqual(buildTiktokExecuteMessageData(likeTask), {
    task_id: "task-like",
    scope: "tt_like",
    max_items: 300,
    max_scroll_rounds: 15,
    max_stagnant_scroll_rounds: 5,
  });
  assert.deepEqual(
    buildTiktokExecuteMessageData({
      id: "t2",
      type: "tt_follow",
      payload: { max_items: 50, max_scroll_rounds: 3, max_stagnant_scroll_rounds: 2 },
    }),
    {
      task_id: "t2",
      scope: "tt_follow",
      max_items: 50,
      max_scroll_rounds: 3,
      max_stagnant_scroll_rounds: 2,
    },
  );
});

test("isValidTiktokTask accepts the backend's top-level spread payload", () => {
  // GET /api/sources/tiktok/next-task returns {id, type, **payload_json}.
  assert.equal(
    isValidTiktokTask({
      id: "t",
      type: "tt_history",
      max_items: 200,
      max_scroll_rounds: 10,
      max_stagnant_scroll_rounds: 4,
    }),
    true,
  );
  assert.equal(
    isValidTiktokTask({ id: "t", type: "tt_like", max_items: "300" }),
    false,
  );
});

test("computeTiktokTaskTimeoutMs reads the top-level spread payload", () => {
  assert.equal(
    computeTiktokTaskTimeoutMs({ id: "t", type: "tt_collect", max_scroll_rounds: 15 }),
    90_000 + 15 * 3_000,
  );
});

test("buildTiktokExecuteMessageData prefers top-level limits over nested payload", () => {
  assert.deepEqual(
    buildTiktokExecuteMessageData({
      id: "t3",
      type: "tt_like",
      max_items: 120,
      max_scroll_rounds: 6,
    }),
    {
      task_id: "t3",
      scope: "tt_like",
      max_items: 120,
      max_scroll_rounds: 6,
      max_stagnant_scroll_rounds: 5,
    },
  );
});

// ---------------------------------------------------------------------------
// Pending-result durability (contract [task] retry)
// ---------------------------------------------------------------------------

test("task-result delivery retries transient failures with the same body and clears the pending slot on ACK", async () => {
  const state = installChromeMock();
  try {
    const result = { task_id: "r1", status: "failed" as const, error: "task_timeout" };
    const responses: Array<{ ok: boolean; status: number } | Error> = [
      { ok: false, status: 503 },
      new Error("backend restarting"),
      { ok: true, status: 200 },
    ];
    const requests: Array<{ input: string; init: RequestInit }> = [];
    const delays: number[] = [];

    await postTiktokTaskResult(
      result,
      {
        resolveUrl: async (path) => `http://127.0.0.1:8420/api${path}`,
        fetch: async (input, init) => {
          requests.push({ input, init });
          const response = responses.shift();
          if (response instanceof Error) throw response;
          assert.ok(response);
          return response;
        },
        sleep: async (delayMs) => { delays.push(delayMs); },
      },
      { maxAttempts: 3, baseDelayMs: 10 },
    );

    assert.equal(requests.length, 3);
    assert.deepEqual(delays, [10, 20]);
    assert.ok(requests.every(({ input }) => input.endsWith("/api/sources/tiktok/task-result")));
    assert.ok(requests.every(({ init }) => init.body === JSON.stringify(result)));
    assert.equal(
      state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY],
      undefined,
      "the pending slot must be cleared after a 2xx ACK",
    );
  } finally {
    state.restore();
  }
});

test("task-result delivery persists the canonical body before POST and keeps it when unacknowledged", async () => {
  const state = installChromeMock();
  try {
    const result = { task_id: "r2", status: "failed" as const, error: "task_timeout" };
    let pendingAtFirstPost: unknown;
    let calls = 0;
    await assert.rejects(
      postTiktokTaskResult(
        result,
        {
          resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
          fetch: async () => {
            calls += 1;
            if (calls === 1) {
              pendingAtFirstPost = state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY];
            }
            return { ok: false, status: 503 };
          },
          sleep: async () => {},
        },
        { baseDelayMs: 0 },
      ),
      /tiktok_task_result_unacknowledged: HTTP 503/,
    );
    assert.equal(calls, 3);
    assert.deepEqual(
      (pendingAtFirstPost as { body: string }).body,
      JSON.stringify(result),
      "the exact canonical payload must be persisted before the first POST",
    );
    assert.ok(
      state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY],
      "an unacknowledged payload stays pending for the next wake",
    );
  } finally {
    state.restore();
  }
});

test("an explicit 4xx terminal rejection drops the pending slot without erroring", async () => {
  const state = installChromeMock();
  try {
    let calls = 0;
    await postTiktokTaskResult(
      { task_id: "r3", status: "failed", error: "task_timeout" },
      {
        resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
        fetch: async () => {
          calls += 1;
          return { ok: false, status: 409 };
        },
        sleep: async () => {},
      },
      { baseDelayMs: 0 },
    );
    assert.equal(calls, 1, "terminal rejections are not retried");
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally {
    state.restore();
  }
});

test("flushPendingTiktokTaskResult replays a queued payload once and clears it on ACK", async () => {
  const state = installChromeMock();
  try {
    const queued = { task_id: "queued", status: "ok", videos: [], scope_counts: { tt_like: 3 } };
    state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] = {
      body: JSON.stringify(queued),
      queued_at_ms: Date.now(),
    };
    const bodies: string[] = [];
    await flushPendingTiktokTaskResult({
      resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
      fetch: async (_input, init) => {
        bodies.push(String(init.body));
        return { ok: true, status: 200 };
      },
      sleep: async () => {},
    });
    assert.deepEqual(bodies, [JSON.stringify(queued)]);
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally {
    state.restore();
  }
});

test("flushPendingTiktokTaskResult keeps the slot when the backend is still down", async () => {
  const state = installChromeMock();
  try {
    state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] = {
      body: JSON.stringify({ task_id: "queued", status: "ok" }),
      queued_at_ms: Date.now(),
    };
    await flushPendingTiktokTaskResult({
      resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
      fetch: async () => {
        throw new Error("connection refused");
      },
      sleep: async () => {},
    });
    assert.ok(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY]);
  } finally {
    state.restore();
  }
});

// ---------------------------------------------------------------------------
// Poll lifecycle
// ---------------------------------------------------------------------------

test("poll takes the cross-source mutex before claiming a backend task", async () => {
  clearDispatcherMutex();
  const shared = globalThis as DispatcherMutexGlobal;
  shared.__OBC_DISPATCHER_MUTEX_HOLDER__ = "dy";
  shared.__OBC_DISPATCHER_MUTEX_HELD_SINCE__ = Date.now();
  let fetchCalls = 0;
  try {
    await pollTiktokTaskOnce({
      ensureRecovery: async () => {},
      fetchTask: async () => {
        fetchCalls += 1;
        return null;
      },
      execute: async () => "accepted",
      reportDeclined: async () => {},
    });
    assert.equal(fetchCalls, 0);
  } finally {
    clearDispatcherMutex();
  }
});

test("poll does not claim when the runtime has no tabs execution capability", async () => {
  clearDispatcherMutex();
  let fetchCalls = 0;
  await pollTiktokTaskOnce({
    ensureRecovery: async () => {},
    canExecute: () => false,
    fetchTask: async () => {
      fetchCalls += 1;
      return null;
    },
    execute: async () => "accepted",
    reportDeclined: async () => {},
  });
  assert.equal(fetchCalls, 0);
  clearDispatcherMutex();
});

test("concurrent TikTok polls share one claim lifecycle", async () => {
  clearDispatcherMutex();
  let releaseRecovery!: () => void;
  const recoveryGate = new Promise<void>((resolve) => {
    releaseRecovery = resolve;
  });
  let recoveryCalls = 0;
  let fetchCalls = 0;
  const dependencies = {
    ensureRecovery: async () => {
      recoveryCalls += 1;
      await recoveryGate;
    },
    fetchTask: async () => {
      fetchCalls += 1;
      return null;
    },
    execute: async () => "accepted" as const,
    reportDeclined: async () => {},
  };

  const first = pollTiktokTaskOnce(dependencies);
  const second = pollTiktokTaskOnce(dependencies);
  assert.equal(first, second);
  releaseRecovery();
  await Promise.all([first, second]);

  assert.equal(recoveryCalls, 1);
  assert.equal(fetchCalls, 1);
  clearDispatcherMutex();
});

test("a declined executor reports the claimed task and releases the mutex", async () => {
  clearDispatcherMutex();
  const declined: string[] = [];
  await pollTiktokTaskOnce({
    ensureRecovery: async () => {},
    fetchTask: async () => likeTask,
    execute: async () => "declined",
    reportDeclined: async (claimed) => { declined.push(claimed.id); },
  });
  const shared = globalThis as DispatcherMutexGlobal;
  assert.deepEqual(declined, [likeTask.id]);
  assert.equal(shared.__OBC_DISPATCHER_MUTEX_HOLDER__, undefined);
  clearDispatcherMutex();
});

test("native-save releases the scope-task mutex before durable execution", async () => {
  clearDispatcherMutex();
  const calls: Array<{ task: NativeSaveTask; mutexAlreadyHeld: boolean; holder?: string }> = [];
  await pollTiktokTaskOnce({
    ensureRecovery: async () => {},
    fetchTask: async () => nativeTask,
    execute: async (task, mutexAlreadyHeld) => {
      const shared = globalThis as DispatcherMutexGlobal;
      calls.push({
        task: task as NativeSaveTask,
        mutexAlreadyHeld,
        holder: shared.__OBC_DISPATCHER_MUTEX_HOLDER__,
      });
      return "accepted";
    },
    reportDeclined: async () => {},
  });

  assert.deepEqual(calls, [{ task: nativeTask, mutexAlreadyHeld: false, holder: undefined }]);
  clearDispatcherMutex();
});

test("pollTiktokTaskNow exists as the WS-driven immediate-poll entry point", () => {
  // Service-worker.ts calls this when the backend broadcasts
  // `tiktok_task_available`; guarantee the export shape.
  assert.equal(typeof pollTiktokTaskOnce, "function");
  assert.equal(typeof pollTiktokTaskNow, "function");
  assert.equal(typeof resetTiktokTaskStateForTest, "function");
});


test("backend bootstrap_profile wire is accepted with frozen scope and item budgets", () => {
  const task = { id: "actual-backend-task", type: "bootstrap_profile", scopes: ["tt_like", "tt_collect", "tt_history", "tt_follow"], max_items_per_scope: 300, max_scroll_rounds: 15 };
  assert.equal(isValidTiktokTask(task), true);
  assert.equal(isValidTiktokTask({ ...task, scopes: ["tt_like", "tt_like"] }), false);
  assert.equal(isValidTiktokTask({ ...task, scopes: ["dy_like"] }), false);
  assert.equal(isValidTiktokTask({ ...task, max_items_per_scope: 0 }), false);
});

test("failed partial delivery survives a later timeout result and drains FIFO unchanged", async () => {
  const state = installChromeMock();
  const bodies: string[] = [];
  let online = false;
  const transport = {
    resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
    fetch: async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return { ok: online, status: online ? 200 : 503 };
    },
    sleep: async () => {},
  };
  try {
    const partial = { task_id: "same", status: "partial" as const, videos: [{ scope: "tt_like" as const, video_id: "7300000000000000001", url: "https://www.tiktok.com/@c/video/7300000000000000001", title: "keep", author: "c", author_handle: "c", cover_url: "" }] };
    const final = { task_id: "same", status: "degraded" as const, videos: partial.videos, error: "task_timeout" };
    await assert.rejects(postTiktokTaskResult(partial, transport, { maxAttempts: 1 }));
    await assert.rejects(postTiktokTaskResult(final, transport, { maxAttempts: 1 }));
    const pending = state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] as { body: string; following: Array<{ body: string }> };
    assert.equal(pending.body, JSON.stringify(partial));
    assert.equal(pending.following[0].body, JSON.stringify(final));
    // New worker; durable storage survives the module state reset.
    resetTiktokTaskStateForTest();
    online = true;
    bodies.length = 0;
    await flushPendingTiktokTaskResult(transport);
    assert.deepEqual(bodies, [JSON.stringify(partial), JSON.stringify(final)]);
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("storage failure prevents POST and retains the prior pending result", async () => {
  const state = installChromeMock();
  try {
    const prior = { body: JSON.stringify({ task_id: "prior", status: "partial" }), queued_at_ms: 1 };
    state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] = prior;
    state.sessionSetImpl = async () => { throw new Error("storage unavailable"); };
    let posts = 0;
    await assert.rejects(postTiktokTaskResult({ task_id: "new", status: "failed" }, {
      resolveUrl: async () => "url",
      fetch: async () => { posts++; return { ok: true, status: 200 }; },
      sleep: async () => {},
    }), /storage unavailable/);
    assert.equal(posts, 0);
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], prior);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});


test("a stalled POST aborts without losing its body or wedging subsequent replay", async () => {
  const state = installChromeMock();
  const result = { task_id: "stalled", status: "failed" as const };
  let signal: AbortSignal | null | undefined;
  try {
    await assert.rejects(postTiktokTaskResult(result, {
      resolveUrl: async () => "http://127.0.0.1:8420/api/sources/tiktok/task-result",
      fetch: async (_url, init) => { signal = init.signal; return new Promise(() => {}); },
      sleep: async () => {},
    }, { maxAttempts: 1, requestTimeoutMs: 5 }), /task_result_request_timeout/);
    assert.equal(signal?.aborted, true);
    assert.equal((state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] as { body: string }).body, JSON.stringify(result));
    const posted: string[] = [];
    await flushPendingTiktokTaskResult({
      resolveUrl: async () => "url",
      fetch: async (_url, init) => { posted.push(String(init.body)); return { ok: true, status: 200 }; },
      sleep: async () => {},
    });
    assert.deepEqual(posted, [JSON.stringify(result)]);
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("URL resolution completing after its deadline cannot send a late callback", async () => {
  const state = installChromeMock();
  let resolveUrl!: (url: string) => void;
  let posts = 0;
  try {
    await assert.rejects(postTiktokTaskResult({ task_id: "late-url", status: "failed" }, {
      resolveUrl: () => new Promise(resolve => { resolveUrl = resolve; }),
      fetch: async () => { posts++; return { ok: true, status: 200 }; },
      sleep: async () => {},
    }, { maxAttempts: 1, requestTimeoutMs: 5 }), /task_result_request_timeout/);
    resolveUrl("url");
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(posts, 0);
    assert.ok(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY]);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});
