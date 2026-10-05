import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSaveTask,
} from "../src/shared/native-save.ts";

const TASK_ID = "123e4567-e89b-12d3-a456-426614174000";
const validTask: NativeSaveTask = {
  id: TASK_ID,
  type: "native_save",
  platform: "reddit",
  platform_slug: "reddit",
  item_key: "reddit:t3_abc",
  content_id: "t3_abc",
  content_url: "https://www.reddit.com/r/test/comments/abc/demo/",
  content_type: "post",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "Reddit Saved",
};

test("native save accepts only the exact platform, slug, and HTTPS host contract", () => {
  const cases = [
    ["youtube", "yt", "https://youtu.be/video-123"],
    ["xiaohongshu", "xhs", "https://www.xiaohongshu.com/explore/note-123"],
    ["douyin", "dy", "https://www.iesdouyin.com/share/video/123"],
    ["twitter", "x", "https://twitter.com/user/status/123"],
    ["zhihu", "zhihu", "https://www.zhihu.com/question/1/answer/2"],
    ["reddit", "reddit", "https://redd.it/abc"],
  ] as const;

  for (const [platform, platform_slug, content_url] of cases) {
    assert.equal(isNativeSaveTask({
      ...validTask,
      platform,
      platform_slug,
      item_key: `${platform}:content-123`,
      content_id: "content-123",
      content_url,
    }), true);
  }
  assert.equal(isNativeSaveTask({ ...validTask, type: "search" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, platform_slug: "x" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "javascript:alert(1)" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "http://reddit.com/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://reddit.com.evil.test/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://user:secret@reddit.com/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://reddit.com:8443/post" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://reddit.com/post#secret" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_url: "https://reddit.com/post?token=secret" }), false);
  assert.equal(isNativeSaveTask({
    ...validTask,
    platform: "youtube",
    platform_slug: "yt",
    item_key: "youtube:video-123",
    content_id: "video-123",
    content_url: "https://www.youtube.com/watch?v=video-123&token=secret",
  }), false);
  assert.equal(isNativeSaveTask({
    ...validTask,
    platform: "youtube",
    platform_slug: "yt",
    item_key: "youtube:video-123",
    content_id: "video-123",
    content_url: "https://www.youtube.com/watch?v=video-123",
  }), true);
  const xhsTask = {
    ...validTask,
    platform: "xiaohongshu",
    platform_slug: "xhs",
    item_key: "xiaohongshu:note-123",
    content_id: "note-123",
  } as const;
  assert.equal(isNativeSaveTask({
    ...xhsTask,
    content_url: "https://www.xiaohongshu.com/explore/note-123?xsec_token=public-note-token&xsec_source=pc_feed",
  }), true);
  for (const content_url of [
    "https://www.xiaohongshu.com/explore/note-123?token=secret",
    "https://www.xiaohongshu.com/explore/note-123?xsec_token=",
    "https://www.xiaohongshu.com/explore/note-123?xsec_token=one&xsec_token=two",
    "https://www.xiaohongshu.com/explore/note-123?xsec_token=public-note-token&xsec_source=",
  ]) {
    assert.equal(isNativeSaveTask({ ...xhsTask, content_url }), false);
  }
});

test("native save rejects malformed, overlong, and inconsistent task fields", () => {
  assert.equal(isNativeSaveTask(validTask), true);
  assert.equal(isNativeSaveTask({ ...validTask, id: "not-a-canonical-uuid" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, item_key: "reddit:t3_wrong" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, item_key: "reddit:bad id", content_id: "bad id" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_id: "x".repeat(513) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, content_type: "x".repeat(129) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, requested_action: "like" }), false);
  assert.equal(isNativeSaveTask({ ...validTask, target_label: "x".repeat(257) }), false);
  assert.equal(isNativeSaveTask({ ...validTask, cookie: "secret" }), false);
});

test("native save result sanitizer emits only backend allow-listed status and code pairs", () => {
  assert.deepEqual(sanitizeNativeSaveResult({ status: "login_required", response_body: "<html>secret" }), {
    status: "login_required",
    error_code: "",
    error_message: "Platform login required",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "unsupported", error_code: "unsupported_content_type" }), {
    status: "unsupported",
    error_code: "unsupported_content_type",
    error_message: "Content type is unsupported for platform native save",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "unsupported", error_message: "raw response" }), {
    status: "unsupported",
    error_code: "unsupported_content_type",
    error_message: "Content type is unsupported for platform native save",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "rate_limited", error_message: "token=secret" }), {
    status: "rate_limited",
    error_code: "",
    error_message: "Platform native save rate limited",
  });
  assert.deepEqual(sanitizeNativeSaveResult({ status: "failed", error_code: "unknown", error_message: "<body>cookie=secret</body>" }), {
    status: "failed",
    error_code: "native_save_failed",
    error_message: "Platform native save failed",
  });

  for (const code of [
    "native_content_not_ready",
    "native_control_not_found",
    "native_dialog_not_opened",
    "native_target_not_found",
    "native_request_rejected",
    "native_confirmation_not_observed",
  ] as const) {
    const result = sanitizeNativeSaveResult({
      status: "failed",
      error_code: code,
      error_message: "cookie=must-not-cross",
    });
    assert.equal(result.status, "failed");
    assert.equal(result.error_code, code);
    assert.doesNotMatch(result.error_message, /cookie|must-not-cross/);
  }
  assert.equal(
    sanitizeNativeSaveResult({ status: "failed", error_code: "selector=.secret" }).error_code,
    "native_save_failed",
  );
});

test("native save content runtime allows the matching hostname and executes once per task ID", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalLocation = (globalThis as { location?: unknown }).location;
  const listeners: Array<(message: unknown) => unknown> = [];
  const emitted: unknown[] = [];
  (globalThis as { location?: unknown }).location = {
    hostname: "www.reddit.com",
    href: validTask.content_url,
  };
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: { addListener: (listener: (message: unknown) => unknown) => listeners.push(listener) },
      sendMessage: async (message: unknown) => emitted.push(message),
    },
  };
  try {
    const { installNativeSaveExecutor } = await import(`../src/content/native-save/runtime.ts?test=${Date.now()}`);
    let executions = 0;
    installNativeSaveExecutor("reddit", async () => {
      executions += 1;
      return { status: "synced" };
    });
    const execute = { type: "NATIVE_SAVE_EXECUTE", task: validTask };
    assert.equal(await listeners[0](execute), true);
    assert.equal(await listeners[0](execute), true);
    assert.equal(executions, 1);
    const expected = {
      type: "NATIVE_SAVE_RESULT",
      platform: "reddit",
      task_id: TASK_ID,
      item_key: validTask.item_key,
      document_instance_id: (emitted[0] as { document_instance_id?: unknown })
        .document_instance_id,
      status: "synced",
      error_code: "",
      error_message: "",
    };
    assert.equal(typeof expected.document_instance_id, "string");
    assert.deepEqual(emitted, [expected, expected]);
    const ready = await listeners[0]({ type: "NATIVE_SAVE_READY", platform: "reddit" });
    assert.deepEqual(ready, {
      ready: true,
      document_instance_id: expected.document_instance_id,
    });
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    (globalThis as { location?: unknown }).location = originalLocation;
  }
});

test("native save content runtime shares one in-flight outcome across duplicate executes", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalLocation = (globalThis as { location?: unknown }).location;
  const listeners: Array<(message: unknown) => Promise<boolean>> = [];
  const emitted: unknown[] = [];
  (globalThis as { location?: unknown }).location = { hostname: "www.reddit.com", href: validTask.content_url };
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: { addListener: (listener: (message: unknown) => Promise<boolean>) => listeners.push(listener) },
      sendMessage: async (message: unknown) => emitted.push(message),
    },
  };
  try {
    const { installNativeSaveExecutor } = await import(`../src/content/native-save/runtime.ts?inflight=${Date.now()}`);
    let executions = 0;
    let resolveExecutor!: (value: unknown) => void;
    const executorResult = new Promise((resolve) => { resolveExecutor = resolve; });
    installNativeSaveExecutor("reddit", async () => {
      executions += 1;
      return executorResult;
    });
    const execute = { type: "NATIVE_SAVE_EXECUTE", task: validTask };
    const first = listeners[0](execute);
    const duplicate = listeners[0](execute);
    resolveExecutor({ status: "synced" });
    assert.deepEqual(await Promise.all([first, duplicate]), [true, true]);
    assert.equal(executions, 1);
    assert.equal(emitted.length, 2);
    assert.deepEqual(emitted[0], emitted[1]);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    (globalThis as { location?: unknown }).location = originalLocation;
  }
});

test("native save content runtime routes persisted confirmation to a read-only verifier", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalLocation = (globalThis as { location?: unknown }).location;
  const listeners: Array<(message: unknown) => Promise<boolean>> = [];
  const emitted: unknown[] = [];
  (globalThis as { location?: unknown }).location = {
    hostname: "www.reddit.com",
    href: validTask.content_url,
  };
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: { addListener: (listener: (message: unknown) => Promise<boolean>) => listeners.push(listener) },
      sendMessage: async (message: unknown) => emitted.push(message),
    },
  };
  try {
    const { installNativeSaveExecutor } = await import(
      `../src/content/native-save/runtime.ts?verify=${Date.now()}`
    );
    let mutations = 0;
    let verifications = 0;
    installNativeSaveExecutor(
      "reddit",
      async () => { mutations += 1; return { status: "synced" }; },
      async () => { verifications += 1; return { status: "already_synced" }; },
    );
    assert.equal(await listeners[0]({
      type: "NATIVE_SAVE_EXECUTE",
      task: validTask,
      execution_id: "verification-phase-1",
      verification_only: true,
    }), true);
    assert.equal(mutations, 0);
    assert.equal(verifications, 1);
    assert.equal((emitted[0] as { status?: unknown }).status, "already_synced");
    assert.equal(await listeners[0]({
      type: "NATIVE_SAVE_EXECUTE",
      task: validTask,
      verification_only: "yes",
    }), false);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    (globalThis as { location?: unknown }).location = originalLocation;
  }
});

test("native save content runtime evicts the oldest completed outcome after 256 task IDs", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const originalLocation = (globalThis as { location?: unknown }).location;
  const listeners: Array<(message: unknown) => Promise<boolean>> = [];
  (globalThis as { location?: unknown }).location = { hostname: "www.reddit.com", href: validTask.content_url };
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: { addListener: (listener: (message: unknown) => Promise<boolean>) => listeners.push(listener) },
      sendMessage: async () => {},
    },
  };
  try {
    const { installNativeSaveExecutor } = await import(`../src/content/native-save/runtime.ts?eviction=${Date.now()}`);
    let executions = 0;
    installNativeSaveExecutor("reddit", async () => {
      executions += 1;
      return { status: "synced" };
    });
    const taskAt = (index: number): NativeSaveTask => ({
      ...validTask,
      id: `123e4567-e89b-42d3-a456-${index.toString(16).padStart(12, "0")}`,
    });
    for (let index = 0; index < 257; index += 1) {
      await listeners[0]({ type: "NATIVE_SAVE_EXECUTE", task: taskAt(index) });
    }
    await listeners[0]({ type: "NATIVE_SAVE_EXECUTE", task: taskAt(0) });
    assert.equal(executions, 258);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    (globalThis as { location?: unknown }).location = originalLocation;
  }
});

test("native save docs identify all six executors and current Zhihu target semantics", () => {
  const runtime = readFileSync(resolve("../docs/modules/runtime.md"), "utf8");
  const changelog = readFileSync(resolve("../docs/changelog.md"), "utf8");
  const architecture = readFileSync(resolve("../docs/architecture.md"), "utf8");
  const extensionModule = readFileSync(resolve("../docs/modules/extension.md"), "utf8");
  const savedSyncModule = readFileSync(resolve("../docs/modules/saved-sync.md"), "utf8");
  for (const text of [runtime, changelog, architecture]) {
    assert.match(text, /NATIVE_SAVE_EXECUTE/);
    assert.match(text, /6\/6.*executor|executor.*6\/6/);
    assert.match(text, /共享 MV3 recovery barrier/);
    assert.match(text, /知乎.*(?:typed|question|answer|article)/i);
    assert.match(text, /OpenBiliClaw/);
    assert.match(text, /知乎收藏/);
    assert.match(text, /已收藏/);
    assert.match(text, /fixture/);
  }
  const spec = readFileSync(resolve("../docs/spec.md"), "utf8");
  assert.match(spec, /shared MV3 recovery barrier/);
  assert.match(runtime, /createYouTubeBrowserEnvironment/);
  assert.match(runtime, /createXiaohongshuBrowserEnvironment/);
  assert.match(runtime, /createDouyinBrowserEnvironment/);
  assert.match(runtime, /6\/6 executor 已接/);
  assert.match(extensionModule, /6\/6 executor.*(?:已接入|真实账号验证)/);
  assert.match(extensionModule, /Reddit \/ X \/ YouTube \/ 小红书 \/ 抖音 \/ 知乎六个 executor 均已接入并完成 fixture/);
  assert.doesNotMatch(extensionModule, /其它平台账号写入 adapter 仍属后续计划/);
  assert.match(savedSyncModule, /6\/6 executor.*(?:已接入|真实账号验证)/);
  assert.match(savedSyncModule, /知乎.*知乎收藏.*(?:不再|不).*命名/s);
  assert.doesNotMatch(savedSyncModule, /扩展 executor 尚未实现/);
});

test("README prose reports all six extension platforms and current Zhihu global favorite", () => {
  const readme = readFileSync(resolve("../README.md"), "utf8");
  const readmeEn = readFileSync(resolve("../README_EN.md"), "utf8");
  assert.match(readme, /Reddit\/X、YouTube、小红书、抖音与知乎原生保存 executor 已 6\/6 接入.*fixture.*知乎.*全局.*知乎收藏/);
  assert.doesNotMatch(readme, /扩展只负责同步 x\.com cookie \+ 捕获互动/);
  assert.match(readmeEn, /Reddit\/X, YouTube, Xiaohongshu, Douyin, and Zhihu native-save executors are wired 6\/6.*fixture-tested.*Zhihu.*global.*知乎收藏/i);
  assert.doesNotMatch(readmeEn, /extension only syncs the x\.com cookie and captures engagement/i);

  const coreFeatures = readme.match(/## ✨ 核心特性([\s\S]*?)\n## /)?.[1] ?? "";
  const keyFeatures = readmeEn.match(/## ✨ Key Features([\s\S]*?)\n## /)?.[1] ?? "";
  assert.match(coreFeatures, /2026-07-14.*七平台.*synced\/already_synced/);
  assert.match(keyFeatures, /2026-07-14.*seven platforms.*synced\/already_synced/i);
});
