/**
 * Tests for the TikTok dispatcher's MV3 resilience: active-task
 * persistence into chrome.storage.session, recovery after a simulated
 * worker unload, the chrome.alarms timeout backstop, the pending-result
 * flush, and the orphan task-tab sweep.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  ensureTiktokTaskRecovery,
  executeTask,
  handleTiktokScopeResult,
  handleTiktokTaskAlarm,
  resetTiktokTaskStateForTest,
  TIKTOK_PENDING_RESULT_LOCAL_KEY,
  TIKTOK_TASK_STATE_SESSION_KEY,
  type PersistedTiktokTaskState,
  type TiktokTask,
} from "../src/background/tiktok-task-dispatcher.ts";
import { installChromeMock, type ChromeMockState } from "./helpers/chrome-mock.ts";

const TASK_TIMEOUT_ALARM_NAME = "openbiliclaw-tiktok-task-timeout";

interface AlarmCreateCall {
  name: string;
  when?: number;
  periodInMinutes?: number;
}

function installAlarmsMock(): { created: AlarmCreateCall[]; cleared: string[] } {
  const created: AlarmCreateCall[] = [];
  const cleared: string[] = [];
  const chromeApi = (globalThis as { chrome?: Record<string, unknown> }).chrome;
  assert.ok(chromeApi, "chrome mock must be installed before the alarms mock");
  chromeApi.alarms = {
    create(name: string, info: { when?: number; periodInMinutes?: number }) {
      created.push({ name, ...info });
    },
    async clear(name: string) {
      cleared.push(name);
      return true;
    },
  };
  return { created, cleared };
}

function persistedRecord(overrides: Partial<PersistedTiktokTaskState> = {}): PersistedTiktokTaskState {
  const task: TiktokTask = { id: "t-restore", type: "tt_like" };
  return {
    task,
    task_tab_id: 42,
    owns_task_tab: true,
    deadline_ms: Date.now() + 60_000,
    progress: {
      task_id: task.id,
      scope: "tt_like",
      max_items: 300,
      max_scroll_rounds: 15,
      max_stagnant_scroll_rounds: 5,
    },
    ...overrides,
  };
}

function taskResultPosts(state: ChromeMockState): Array<Record<string, unknown>> {
  return state.fetchCalls
    .filter((call) => call.url.endsWith("/api/sources/tiktok/task-result"))
    .map((call) => call.body as Record<string, unknown>);
}

async function settleSessionWrites(): Promise<void> {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

test("executeTask persists the active task snapshot and arms the timeout alarm", async () => {
  const state = installChromeMock();
  const alarms = installAlarmsMock();
  try {
    const disposition = await executeTask({
      id: "t-persist",
      type: "tt_collect",
      payload: { max_items: 100, max_scroll_rounds: 5 },
    });
    assert.equal(disposition, "accepted");
    await settleSessionWrites();

    const record = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.ok(record, "active task snapshot must be persisted");
    assert.equal(record.task.id, "t-persist");
    assert.equal(record.task.type, "tt_collect");
    assert.equal(record.owns_task_tab, true);
    assert.equal(record.task_tab_id, state.createdTabs[0] ? 42 : null);
    assert.equal(record.progress?.task_id, "t-persist");
    assert.ok(record.deadline_ms > Date.now(), "deadline must be in the future");

    // Contract extension.background = true: the task tab stays backgrounded.
    assert.equal(state.createdTabs[0]?.active, false);
    assert.equal(state.createdTabs[0]?.url, "about:blank");
    assert.ok(state.updatedTabs.some(tab => tab.url?.includes("openbiliclaw_tiktok_task=1")));

    const timeoutAlarm = alarms.created.find((call) => call.name === TASK_TIMEOUT_ALARM_NAME);
    assert.ok(timeoutAlarm, "timeout alarm must backstop the in-process timer");
    assert.equal(timeoutAlarm.when, record.deadline_ms);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("a fresh worker restores the persisted task and reports a late result (partial + final)", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord();
    state.tabById.set(42, {
      id: 42,
      status: "complete",
      url: "https://www.tiktok.com/@demo",
    });
    // Simulate the service worker being unloaded and a new one waking up.
    resetTiktokTaskStateForTest();

    await handleTiktokScopeResult({
      task_id: "t-restore",
      scope: "tt_like",
      items: [
        {
          scope: "tt_like",
          video_id: "7300000000000000001",
          url: "https://www.tiktok.com/@creator/video/7300000000000000001",
          title: "liked video",
          author_handle: "creator",
          author: "creator",
          cover_url: "",
        },
      ],
      scope_count: 1,
      status: "ok",
    });
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 2, "late result must post partial + final");
    assert.equal(posts[0].task_id, "t-restore");
    assert.equal(posts[0].status, "partial");
    assert.deepEqual(posts[0].scope_counts, { tt_like: 1 });
    assert.equal(posts[1].task_id, "t-restore");
    assert.equal(posts[1].status, "ok");
    assert.deepEqual(posts[1].videos, posts[0].videos, "final freezes the full canonical rows");
    assert.ok(
      state.removedTabs.includes(42),
      "the restored task tab must be closed on the final result",
    );
    assert.equal(state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY], undefined);
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("a failed scope result finalises the task as failed without a partial", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord();
    state.tabById.set(42, { id: 42, status: "complete", url: "https://www.tiktok.com/login" });
    resetTiktokTaskStateForTest();

    await handleTiktokScopeResult({
      task_id: "t-restore",
      scope: "tt_like",
      items: [],
      scope_count: 0,
      status: "failed",
      error: "login_wall",
    });
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1, "a login wall is terminal, never a pseudo-empty success");
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "login_wall");
    assert.ok(state.removedTabs.includes(42));
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("recovery settles an expired persisted task as task_timeout and closes its tab", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({
      deadline_ms: Date.now() - 1_000,
    });
    resetTiktokTaskStateForTest();

    await ensureTiktokTaskRecovery();
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].task_id, "t-restore");
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_timeout");
    assert.ok(state.removedTabs.includes(42));
    assert.equal(state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY], undefined);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("the timeout alarm is the authoritative backstop after a worker unload", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({
      deadline_ms: Date.now() - 500,
    });
    resetTiktokTaskStateForTest();

    handleTiktokTaskAlarm(TASK_TIMEOUT_ALARM_NAME);
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_timeout");
    assert.ok(state.removedTabs.includes(42));
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("recovery fails a task whose recorded tab is already gone", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.getImpl = async (tabId) => {
      throw new Error(`No tab with id: ${tabId}`);
    };
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({ task_tab_id: 99 });
    resetTiktokTaskStateForTest();

    await ensureTiktokTaskRecovery();
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_tab_closed");
    assert.deepEqual(state.removedTabs, []);
    assert.equal(state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY], undefined);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("wake-up sweeps orphan TikTok task-marker tabs but never user tabs", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.queryResult = [
      { id: 7, url: "https://www.tiktok.com/?openbiliclaw_tiktok_task=1" },
      { id: 8, url: "https://www.tiktok.com/" },
      { id: 9, url: "https://www.tiktok.com/?openbiliclaw_dy_task=1" },
    ];
    resetTiktokTaskStateForTest();

    await ensureTiktokTaskRecovery();

    assert.deepEqual(state.removedTabs, [7]);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("wake-up flushes a pending unacknowledged task result before claiming work", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    const queued = { task_id: "queued-final", status: "ok", videos: [], scope_counts: { tt_history: 4 } };
    state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] = {
      body: JSON.stringify(queued),
      queued_at_ms: Date.now(),
    };
    resetTiktokTaskStateForTest();

    await ensureTiktokTaskRecovery();
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].task_id, "queued-final");
    assert.equal(posts[0].status, "ok");
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("an early timeout alarm does not fail a live task before its deadline", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    const disposition = await executeTask({ id: "t-live", type: "tt_history" });
    assert.equal(disposition, "accepted");

    handleTiktokTaskAlarm(TASK_TIMEOUT_ALARM_NAME);
    await settleSessionWrites();

    assert.deepEqual(taskResultPosts(state), []);
    assert.deepEqual(state.removedTabs, []);
    const record = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.equal(record.task.id, "t-live");
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

test("a restored task ignores results for a different scope or task", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord();
    resetTiktokTaskStateForTest();

    await handleTiktokScopeResult({
      task_id: "t-restore",
      scope: "tt_collect",
      items: [],
      scope_count: 0,
      status: "empty",
    });
    await handleTiktokScopeResult({
      task_id: "someone-else",
      scope: "tt_like",
      items: [],
      scope_count: 0,
      status: "empty",
    });
    await settleSessionWrites();

    assert.deepEqual(taskResultPosts(state), []);
    assert.deepEqual(state.removedTabs, []);
    const record = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.equal(record.task.id, "t-restore", "restored task must stay in flight");
  } finally {
    resetTiktokTaskStateForTest();
    state.restore();
  }
});

function scopeItem(scope: "tt_like" | "tt_collect" | "tt_history", id: string) {
  return { scope, video_id: id, url: `https://www.tiktok.com/@creator/video/${id}`, title: "signal", author_handle: "creator", author: "Creator", cover_url: "" };
}

test("actual backend bootstrap scopes execute sequentially, survive worker restart, and freeze full results", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    // Exact shape from enqueue_tiktok_bootstrap -> GET next-task.
    const task = { id: "full-bootstrap", type: "bootstrap_profile" as const,
      scopes: ["tt_like", "tt_collect", "tt_history", "tt_follow"] as const,
      max_items_per_scope: 1, max_scroll_rounds: 2 };
    await executeTask({ ...task, scopes: [...task.scopes] });
    await settleSessionWrites();
    assert.equal((state.sentMessages[0].message as { data: { scope: string } }).data.scope, "tt_like");
    await handleTiktokScopeResult({ task_id: task.id, scope: "tt_like", status: "ok", scope_count: 99,
      viewer_handle: "viewer", viewer_sec_uid: "uid-1",
      items: [scopeItem("tt_like", "7300000000000000001"), scopeItem("tt_like", "7300000000000000002"), scopeItem("tt_collect", "7300000000000000003")] });
    await settleSessionWrites();
    const persisted = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.equal(persisted.progress?.scope, "tt_collect");
    assert.equal(persisted.scope_results?.tt_like?.items.length, 1);
    assert.equal(persisted.scope_results?.tt_like?.status, "degraded");
    assert.equal(state.updatedTabs.filter((tab) => tab.url).length, 2);
    assert.equal(state.createdTabs[0].active, false);
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    await handleTiktokScopeResult({ task_id: task.id, scope: "tt_collect", status: "failed", error: "private_tab", scope_count: 0, items: [], viewer_handle: "viewer", viewer_sec_uid: "uid-1" });
    await handleTiktokScopeResult({ task_id: task.id, scope: "tt_history", status: "empty", scope_count: 0, items: [], viewer_handle: "viewer", viewer_sec_uid: "uid-1" });
    await handleTiktokScopeResult({ task_id: task.id, scope: "tt_follow", status: "empty", scope_count: 0, items: [], viewer_handle: "viewer", viewer_sec_uid: "uid-1" });
    await settleSessionWrites();
    const posts = taskResultPosts(state);
    const final = posts.at(-1)!;
    assert.equal(final.status, "degraded");
    assert.deepEqual(final.scope_counts, { tt_like: 1, tt_collect: 0, tt_history: 0, tt_follow: 0 });
    assert.equal((final.videos as unknown[]).length, 1);
    assert.equal(final.viewer_handle, "viewer");
    assert.equal(final.viewer_sec_uid, "uid-1");
    assert.deepEqual((final.debug as Record<string, unknown>).scope_statuses,
      { tt_like: "degraded", tt_collect: "failed", tt_history: "empty", tt_follow: "empty" });
    assert.ok(state.removedTabs.includes(42));
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("account switch between scopes retains original rows and rejects foreign account projection", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    await executeTask({ id: "identity", type: "bootstrap_profile", scopes: ["tt_like", "tt_collect"], max_items_per_scope: 10, max_scroll_rounds: 1 });
    await handleTiktokScopeResult({ task_id: "identity", scope: "tt_like", status: "ok", scope_count: 1, items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "first", viewer_sec_uid: "uid-first" });
    await handleTiktokScopeResult({ task_id: "identity", scope: "tt_collect", status: "ok", scope_count: 1, items: [scopeItem("tt_collect", "7300000000000000002")], viewer_handle: "second", viewer_sec_uid: "uid-second" });
    const final = taskResultPosts(state).at(-1)!;
    assert.equal(final.status, "degraded");
    assert.equal(final.error, "account_identity_conflict");
    assert.equal(final.viewer_handle, "first");
    assert.deepEqual((final.videos as Array<{ video_id: string }>).map((v) => v.video_id), ["7300000000000000001"]);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("foreign tab cannot finalize the active account task", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    await executeTask({ id: "owned", type: "bootstrap_profile", scopes: ["tt_like"], max_items_per_scope: 10, max_scroll_rounds: 1 });
    await handleTiktokScopeResult({ task_id: "owned", scope: "tt_like", status: "ok", scope_count: 1, items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "viewer" }, 99);
    assert.deepEqual(taskResultPosts(state), []);
    assert.deepEqual(state.removedTabs, []);
    await handleTiktokScopeResult({ task_id: "owned", scope: "tt_like", status: "ok", scope_count: 1, items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "viewer" }, 42);
    assert.equal(taskResultPosts(state).at(-1)?.status, "ok");
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("deadline recovery retains an offline partial before the degraded final", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  const onlineFetch = state.fetchImpl;
  try {
    const item = scopeItem("tt_like", "7300000000000000001");
    const scopeResult = { task_id: "t-restore", scope: "tt_like" as const, items: [item], scope_count: 1, status: "ok" as const, viewer_handle: "viewer" };
    const partial = { task_id: "t-restore", status: "partial", videos: [item], viewer_handle: "viewer" };
    state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] = { body: JSON.stringify(partial), queued_at_ms: 1 };
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({
      deadline_ms: Date.now() - 1000,
      scope_results: { tt_like: scopeResult },
    });
    state.fetchImpl = async () => new Response("offline", { status: 503 });
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    const queue = state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY] as { body: string; following: Array<{ body: string }> };
    assert.equal(queue.body, JSON.stringify(partial));
    const final = JSON.parse(queue.following[0].body);
    assert.equal(final.status, "degraded");
    assert.equal(final.error, "task_timeout");
    assert.deepEqual(final.videos, [item]);
    assert.equal(final.viewer_handle, "viewer");
    state.fetchImpl = onlineFetch;
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    assert.equal(state.sessionStorage[TIKTOK_PENDING_RESULT_LOCAL_KEY], undefined);
    assert.deepEqual(taskResultPosts(state)[0], partial);
    assert.deepEqual(taskResultPosts(state)[1], final);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("first projected handle-only identity is never upgraded by a later secUid", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    await executeTask({ id: "identity-form", type: "bootstrap_profile", scopes: ["tt_like", "tt_collect"], max_items_per_scope: 10, max_scroll_rounds: 1 });
    await handleTiktokScopeResult({ task_id: "identity-form", scope: "tt_like", status: "ok", scope_count: 1, items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "viewer" });
    await handleTiktokScopeResult({ task_id: "identity-form", scope: "tt_collect", status: "ok", scope_count: 1, items: [scopeItem("tt_collect", "7300000000000000002")], viewer_handle: "viewer", viewer_sec_uid: "later-visible-uid" });
    const posts = taskResultPosts(state);
    assert.equal(posts.length, 3);
    assert.ok(posts.every((post) => post.viewer_handle === "viewer" && !Object.hasOwn(post, "viewer_sec_uid")));
    assert.equal(posts.at(-1)?.status, "ok");
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("idle deadline advances only on accepted scope progress and survives worker recovery", async () => {
  const state = installChromeMock();
  const alarms = installAlarmsMock();
  const actualNow = Date.now;
  let now = actualNow();
  Date.now = () => now;
  try {
    await executeTask({ id: "idle", type: "bootstrap_profile", scopes: ["tt_like", "tt_collect", "tt_history", "tt_follow"], max_items_per_scope: 10, max_scroll_rounds: 15 });
    await settleSessionWrites();
    const first = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    const absolute = first.deadline_ms;
    assert.equal(absolute, now + 360_000);
    assert.equal(first.idle_deadline_ms, now + 120_000);
    assert.equal(alarms.created.filter((a) => a.name === TASK_TIMEOUT_ALARM_NAME).at(-1)?.when, first.idle_deadline_ms);
    now += 50_000;
    handleTiktokTaskAlarm(TASK_TIMEOUT_ALARM_NAME); // early alarm/heartbeat cannot extend idle
    await settleSessionWrites();
    assert.equal((state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState).idle_deadline_ms, first.idle_deadline_ms);
    await handleTiktokScopeResult({ task_id: "idle", scope: "tt_like", status: "ok", scope_count: 1, items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "viewer" });
    const progressed = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.equal(progressed.deadline_ms, absolute);
    assert.equal(progressed.idle_deadline_ms, now + 120_000);
    now += 10_000;
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery(); // re-handshake cannot extend either clock
    assert.equal(alarms.created.filter((a) => a.name === TASK_TIMEOUT_ALARM_NAME).at(-1)?.when, progressed.idle_deadline_ms);
    now = progressed.idle_deadline_ms! + 1;
    assert.ok(now < absolute);
    handleTiktokTaskAlarm(TASK_TIMEOUT_ALARM_NAME);
    await settleSessionWrites();
    const final = taskResultPosts(state).at(-1)!;
    assert.equal(final.error, "task_idle_timeout");
    assert.equal(final.status, "degraded");
    assert.equal((final.videos as unknown[]).length, 1);
  } finally { Date.now = actualNow; resetTiktokTaskStateForTest(); state.restore(); }
});

test("expired idle clock restores immediately without resetting the absolute budget", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({
      deadline_ms: Date.now() + 200_000, idle_deadline_ms: Date.now() - 100,
    });
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    assert.equal(taskResultPosts(state).at(-1)?.error, "task_idle_timeout");
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("timeout salvages matching content checkpoints and clears them only after ACK", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  const key = "openbiliclaw_tiktok_content_scope_v1";
  try {
    const item = scopeItem("tt_like", "7300000000000000001");
    state.sessionStorage[key] = { message: { task_id: "t-restore", scope: "tt_like" }, accepted_items: [item], viewer_handle: "viewer" };
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({ deadline_ms: Date.now() - 100 });
    const fetch = state.fetchImpl;
    let sawCheckpointBeforeAck = false;
    state.fetchImpl = async (input, init) => {
      if (String(input).endsWith("/task-result")) sawCheckpointBeforeAck = Boolean(state.sessionStorage[key]);
      return fetch(input, init);
    };
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    const final = taskResultPosts(state).at(-1)!;
    assert.equal(final.status, "degraded");
    assert.deepEqual(final.videos, [item]);
    assert.equal(sawCheckpointBeforeAck, true);
    assert.equal(state.sessionStorage[key], undefined);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("terminal cleanup never deletes another task's content checkpoint", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  const key = "openbiliclaw_tiktok_content_scope_v1";
  try {
    const checkpoint = { message: { task_id: "newer-task", scope: "tt_like" }, accepted_items: [scopeItem("tt_like", "7300000000000000001")], viewer_handle: "someone-else" };
    state.sessionStorage[key] = checkpoint;
    state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] = persistedRecord({ deadline_ms: Date.now() - 100 });
    resetTiktokTaskStateForTest();
    await ensureTiktokTaskRecovery();
    assert.equal(taskResultPosts(state).at(-1)?.status, "failed");
    assert.equal(state.sessionStorage[key], checkpoint);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});


test("slow content registration retries the same scope without failing or extending its deadline", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  let attempts = 0;
  state.sendMessageImpl = async () => {
    if (++attempts === 1) throw new Error("Receiving end does not exist");
    return undefined;
  };
  try {
    await executeTask({ id: "slow-register", type: "tt_like" });
    await settleSessionWrites();
    const before = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    await new Promise((resolve) => setTimeout(resolve, 550));
    assert.equal(attempts, 2);
    assert.equal(taskResultPosts(state).length, 0);
    assert.deepEqual(state.sentMessages[0], state.sentMessages[1]);
    const after = state.sessionStorage[TIKTOK_TASK_STATE_SESSION_KEY] as PersistedTiktokTaskState;
    assert.equal(after.deadline_ms, before.deadline_ms);
    assert.equal(after.idle_deadline_ms, before.idle_deadline_ms);
  } finally { resetTiktokTaskStateForTest(); state.restore(); }
});

test("delivery retries stop after cleanup and fail within the delivery budget", async () => {
  for (const cleanup of [true, false]) {
    const state = installChromeMock();
    installAlarmsMock();
    const actualNow = Date.now;
    let now = actualNow();
    Date.now = () => now;
    let attempts = 0;
    state.sendMessageImpl = async () => { attempts++; throw new Error("no listener"); };
    try {
      await executeTask({ id: "no-register", type: "tt_like" });
      await settleSessionWrites();
      if (cleanup) resetTiktokTaskStateForTest();
      now += 20_001;
      await new Promise((resolve) => setTimeout(resolve, 550));
      await settleSessionWrites();
      assert.equal(attempts, cleanup ? 1 : 2);
      assert.equal(taskResultPosts(state).length, cleanup ? 0 : 1);
      if (!cleanup) assert.equal(taskResultPosts(state)[0].error, "sendMessage_failed");
    } finally { Date.now = actualNow; resetTiktokTaskStateForTest(); state.restore(); }
  }
});

test("scope delivery retries missing or mismatched acceptance instead of silently losing the task", async () => {
  for (const first of [undefined, { accepted: false }, { accepted: true, task_id: "old", scope: "tt_like" }]) {
    const state = installChromeMock();
    installAlarmsMock();
    let attempts = 0;
    state.sendMessageImpl = async (_tab, message) => {
      if (++attempts === 1) return first;
      return { accepted: true, ...(message as { data: { task_id: string; scope: string } }).data };
    };
    try {
      await executeTask({ id: "missing-acceptance", type: "tt_like" });
      await settleSessionWrites();
      await new Promise(resolve => setTimeout(resolve, 550));
      assert.equal(attempts, 2);
      assert.deepEqual(state.sentMessages[0], state.sentMessages[1]);
      assert.equal(taskResultPosts(state).length, 0);
    } finally { resetTiktokTaskStateForTest(); state.restore(); }
  }
});
