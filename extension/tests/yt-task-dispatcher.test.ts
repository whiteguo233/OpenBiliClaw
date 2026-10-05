import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  computeYtTaskTimeoutMs,
  isValidYtTask,
  postYtNativeSaveResult,
  YT_TASK_SESSION_KEY,
  YT_TASK_TIMEOUT_ALARM_NAME,
} from "../src/background/yt-task-dispatcher.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const nativeTask: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174007",
  type: "native_save",
  platform: "youtube",
  platform_slug: "yt",
  item_key: "youtube:dQw4w9WgXcQ",
  content_id: "dQw4w9WgXcQ",
  content_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  content_type: "video",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "OpenBiliClaw",
};

test("yt task union preserves bootstrap validation and adds exact YouTube native save", () => {
  const source = readFileSync(resolve("src/background/yt-task-dispatcher.ts"), "utf8");
  assert.match(source, /isNativeSaveTask\(task\)/);
  assert.match(source, /task\.platform === ["']youtube["']/);
  assert.match(source, /task\.platform_slug === ["']yt["']/);
  assert.match(source, /t\.type !== ["']bootstrap_profile["']/);
  assert.equal(nativeTask.platform, "youtube");
  assert.equal(isValidYtTask({ id: "bootstrap", type: "bootstrap_profile", scopes: ["yt_likes"] }), true);
  assert.equal(isValidYtTask(nativeTask), true);
  assert.equal(isValidYtTask({ ...nativeTask, platform: "twitter", platform_slug: "x" }), false);
  assert.equal(computeYtTaskTimeoutMs({ id: "bootstrap", type: "bootstrap_profile" }), 120_000);
});

test("yt native branch precedes bootstrap parsing and uses shared authenticated runner result POST", () => {
  const source = readFileSync(resolve("src/background/yt-task-dispatcher.ts"), "utf8");
  const executeStart = source.indexOf("export async function executeTask");
  const executeEnd = source.indexOf("// ---------------------------------------------------------------------------\n// Result handler", executeStart);
  const executeSource = source.slice(executeStart, executeEnd);
  assert.match(executeSource, /task\.type === ["']native_save["'][\s\S]*runNativeSaveTask\(task, ["']yt["'], postYtNativeSaveResult\)/);
  assert.ok(executeSource.indexOf('task.type === "native_save"') < executeSource.indexOf("task.scopes"));
  assert.match(source, /postYtNativeSaveResult[\s\S]*\/sources\/yt\/task-result/);
});

test("YouTube native result transport requires a 2xx acknowledgement", async () => {
  const result = {
    task_id: nativeTask.id,
    item_key: nativeTask.item_key,
    status: "synced" as const,
    error_code: "",
    error_message: "",
  };
  const calls: Array<{ init: RequestInit; url: string }> = [];
  const transport = {
    resolveUrl: async (path: string) => `http://127.0.0.1:8420/api${path}`,
    fetch: async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return { ok: true };
    },
  };
  await postYtNativeSaveResult(result, undefined, transport);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/sources/yt/task-result");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), result);

  await assert.rejects(postYtNativeSaveResult(result, undefined, {
    ...transport,
    fetch: async () => ({ ok: false }),
  }), /not acknowledged/);
});

test("YouTube content entry installs native executor without removing legacy listener", () => {
  const source = readFileSync(resolve("src/content/youtube.ts"), "utf8");
  assert.match(source, /installYtMessageListener\(\)/);
  assert.match(source, /installNativeSaveExecutor\(["']youtube["'], saveYouTube, verifyYouTube\)/);
  assert.ok(source.indexOf("installNativeSaveExecutor(") < source.indexOf("startCollector("));
  assert.match(source, /shouldStartPassiveCollector\(\)[\s\S]*if \(shouldStart\) startCollector/);
});

test("yt task timeout alarm persists through MV3 service-worker sleep", () => {
  assert.equal(YT_TASK_TIMEOUT_ALARM_NAME, "openbiliclaw-yt-task-timeout");
  assert.equal(YT_TASK_SESSION_KEY, "openbiliclaw-yt-active-task");
  assert.equal(
    computeYtTaskTimeoutMs({
      id: "bootstrap",
      type: "bootstrap_profile",
      max_scroll_rounds: 30,
    }),
    300_000,
  );
  const source = readFileSync(resolve("src/background/yt-task-dispatcher.ts"), "utf8");
  assert.match(source, /chrome\.alarms\.create\(YT_TASK_TIMEOUT_ALARM_NAME, \{ when: deadlineAt \}\)/);
  assert.match(source, /chrome\.storage\?\.session/);
  assert.match(source, /recoverInterruptedYtTask/);
  assert.match(source, /handleYtTaskAlarm/);
});
