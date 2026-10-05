/**
 * Tests for the Douyin dispatcher's event-page resilience: active-task
 * persistence into chrome.storage.session, recovery after a simulated
 * worker unload, the chrome.alarms timeout backstop, and the orphan
 * task-tab sweep (issue #140).
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  DY_TASK_STATE_SESSION_KEY,
  ensureDyTaskRecovery,
  executeTask,
  handleDySearchResult,
  handleDyTaskAlarm,
  resetDyTaskStateForTest,
  type DyLegacyTask,
  type PersistedDyTaskState,
} from "../src/background/dy-task-dispatcher.ts";
import { installChromeMock, type ChromeMockState } from "./helpers/chrome-mock.ts";

const TASK_TIMEOUT_ALARM_NAME = "openbiliclaw-dy-task-timeout";

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

function persistedSearchRecord(
  overrides: Partial<PersistedDyTaskState> = {},
): PersistedDyTaskState {
  const task: DyLegacyTask = {
    id: "t-restore",
    type: "search",
    keywords: ["猫"],
    max_items_per_keyword: 5,
  };
  return {
    task,
    task_tab_id: 42,
    owns_task_tab: true,
    deadline_ms: Date.now() + 60_000,
    progress: null,
    search_progress: {
      task_id: task.id,
      keywords: ["猫"],
      current_keyword_idx: 0,
      accumulated_count: 0,
      max_items_per_keyword: 5,
      navigation_resume_count: 0,
      navigation_resume_dispatched: false,
      navigation_generation: 0,
    },
    hot_progress: null,
    feed_progress: null,
    ...overrides,
  };
}

function taskResultPosts(state: ChromeMockState): Array<Record<string, unknown>> {
  return state.fetchCalls
    .filter((call) => call.url.endsWith("/api/sources/dy/task-result"))
    .map((call) => call.body as Record<string, unknown>);
}

async function settleSessionWrites(): Promise<void> {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

test("executeTask persists the active search task snapshot and arms the timeout alarm", async () => {
  const state = installChromeMock();
  const alarms = installAlarmsMock();
  try {
    const disposition = await executeTask({
      id: "t-persist",
      type: "search",
      keywords: ["猫"],
    });
    assert.equal(disposition, "accepted");
    await settleSessionWrites();

    const record = state.sessionStorage[DY_TASK_STATE_SESSION_KEY] as PersistedDyTaskState;
    assert.ok(record, "active task snapshot must be persisted");
    assert.equal(record.task.id, "t-persist");
    assert.equal(record.task.type, "search");
    assert.equal(record.owns_task_tab, true);
    assert.equal(record.task_tab_id, state.createdTabs[0] ? 42 : null);
    assert.equal(record.search_progress?.task_id, "t-persist");
    assert.ok(record.deadline_ms > Date.now(), "deadline must be in the future");
    assert.ok(record.deadline_ms <= Date.now() + 181_000);

    const timeoutAlarm = alarms.created.find((call) => call.name === TASK_TIMEOUT_ALARM_NAME);
    assert.ok(timeoutAlarm, "timeout alarm must backstop the in-process timer");
    assert.equal(timeoutAlarm.when, record.deadline_ms);
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("a fresh worker restores the persisted search task and reports a late result", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[DY_TASK_STATE_SESSION_KEY] = persistedSearchRecord();
    state.tabById.set(42, {
      id: 42,
      status: "complete",
      url: "https://www.douyin.com/search/%E7%8C%AB",
    });
    // Simulate the event page being unloaded and a new worker waking up.
    resetDyTaskStateForTest();

    await handleDySearchResult({
      task_id: "t-restore",
      keyword: "猫",
      items: [],
      scope_count: 0,
      status: "empty",
    });
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 2, "late result must post partial + final");
    assert.equal(posts[0].task_id, "t-restore");
    assert.equal(posts[0].status, "partial");
    assert.equal(posts[1].task_id, "t-restore");
    assert.equal(posts[1].status, "ok");
    assert.ok(
      state.removedTabs.includes(42),
      "the restored task tab must be closed on the final result",
    );
    assert.equal(state.sessionStorage[DY_TASK_STATE_SESSION_KEY], undefined);
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("recovery settles an expired persisted task as task_timeout and closes its tab", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[DY_TASK_STATE_SESSION_KEY] = persistedSearchRecord({
      deadline_ms: Date.now() - 1_000,
    });
    resetDyTaskStateForTest();

    await ensureDyTaskRecovery();
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].task_id, "t-restore");
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_timeout");
    assert.ok(state.removedTabs.includes(42));
    assert.equal(state.sessionStorage[DY_TASK_STATE_SESSION_KEY], undefined);
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("the timeout alarm is the authoritative backstop after a worker unload", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[DY_TASK_STATE_SESSION_KEY] = persistedSearchRecord({
      deadline_ms: Date.now() - 500,
    });
    resetDyTaskStateForTest();

    handleDyTaskAlarm(TASK_TIMEOUT_ALARM_NAME);
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_timeout");
    assert.ok(state.removedTabs.includes(42));
  } finally {
    resetDyTaskStateForTest();
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
    state.sessionStorage[DY_TASK_STATE_SESSION_KEY] = persistedSearchRecord({
      task_tab_id: 99,
    });
    resetDyTaskStateForTest();

    await ensureDyTaskRecovery();
    await settleSessionWrites();

    const posts = taskResultPosts(state);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].status, "failed");
    assert.equal(posts[0].error, "task_tab_closed");
    assert.deepEqual(state.removedTabs, []);
    assert.equal(state.sessionStorage[DY_TASK_STATE_SESSION_KEY], undefined);
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("wake-up sweeps orphan Douyin task-marker tabs but never user tabs", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.queryResult = [
      { id: 7, url: "https://www.douyin.com/?openbiliclaw_dy_task=1" },
      { id: 8, url: "https://www.douyin.com/" },
      { id: 9, url: "https://www.douyin.com/?openbiliclaw_xhs_task=1" },
    ];
    resetDyTaskStateForTest();

    await ensureDyTaskRecovery();

    assert.deepEqual(state.removedTabs, [7]);
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("an early timeout alarm does not fail a live task before its deadline", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    const disposition = await executeTask({
      id: "t-live",
      type: "search",
      keywords: ["猫"],
    });
    assert.equal(disposition, "accepted");

    handleDyTaskAlarm(TASK_TIMEOUT_ALARM_NAME);
    await settleSessionWrites();

    assert.deepEqual(taskResultPosts(state), []);
    assert.deepEqual(state.removedTabs, []);
    const record = state.sessionStorage[DY_TASK_STATE_SESSION_KEY] as PersistedDyTaskState;
    assert.equal(record.task.id, "t-live");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});

test("a restored task ignores results for a different keyword", async () => {
  const state = installChromeMock();
  installAlarmsMock();
  try {
    state.sessionStorage[DY_TASK_STATE_SESSION_KEY] = persistedSearchRecord();
    resetDyTaskStateForTest();

    await handleDySearchResult({
      task_id: "t-restore",
      keyword: "美食",
      items: [],
      scope_count: 0,
      status: "empty",
    });
    await settleSessionWrites();

    assert.deepEqual(taskResultPosts(state), []);
    assert.deepEqual(state.removedTabs, []);
    const record = state.sessionStorage[DY_TASK_STATE_SESSION_KEY] as PersistedDyTaskState;
    assert.equal(record.task.id, "t-restore", "restored task must stay in flight");
  } finally {
    resetDyTaskStateForTest();
    state.restore();
  }
});
