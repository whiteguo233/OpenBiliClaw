import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  authenticatedFetchBefore,
  buildSafeNativeSaveE2EResult,
  handleE2ERuntimeEvent,
  isAuthorizedNativeSaveE2ERequest,
} from "../src/background/e2e-runner.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

function delay(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

test("native-save e2e authorization rejects a state-changing request without every exact field", () => {
  const request = {
    allow_state_changing: true,
    platform: "reddit",
    action: "favorite",
    content_id: "t3_public1",
    expected_target: "Reddit Saved",
  };

  assert.equal(isAuthorizedNativeSaveE2ERequest(request), true);
  assert.equal(isAuthorizedNativeSaveE2ERequest({ ...request, allow_state_changing: false }), false);
  assert.equal(isAuthorizedNativeSaveE2ERequest({ ...request, expected_target: "OpenBiliClaw" }), false);
  assert.equal(isAuthorizedNativeSaveE2ERequest({ ...request, content_id: "https://reddit.com/?token=secret" }), false);

  for (const field of ["platform", "action", "content_id", "expected_target"] as const) {
    const incomplete: Record<string, unknown> = { ...request };
    delete incomplete[field];
    assert.equal(isAuthorizedNativeSaveE2ERequest(incomplete), false, `missing ${field}`);
  }

  for (const [field, value] of [
    ["account_id", "private-user"],
    ["cookie", "session=secret"],
    ["token", "secret"],
    ["html", "<main>private</main>"],
    ["response_body", "private"],
    ["content_url", "https://reddit.com/?token=secret"],
  ] as const) {
    assert.equal(isAuthorizedNativeSaveE2ERequest({ ...request, [field]: value }), false, field);
  }
});

test("native-save e2e result collapses to the exact safe six-field schema", () => {
  const request = {
    allow_state_changing: true,
    platform: "reddit",
    action: "watch_later",
    content_id: "t3_public1",
    expected_target: "Reddit Saved",
  };

  assert.deepEqual(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "already_synced",
      error_code: "",
    }),
    {
      platform: "reddit",
      action: "watch_later",
      content_id: "t3_public1",
      expected_target: "Reddit Saved",
      task_status: "already_synced",
      error_code: "",
    },
  );
  assert.equal(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "synced",
      error_code: "",
      response_body: "private",
    }),
    null,
  );
  assert.equal(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "synced",
      error_code: "native_save_failed",
    }),
    null,
  );
  assert.equal(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "failed",
      error_code: "",
    }),
    null,
  );
  assert.equal(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "failed",
      error_code: "secret_token_abc123",
    }),
    null,
  );
  assert.equal(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "unsupported",
      error_code: "native_save_failed",
    }),
    null,
  );
  assert.deepEqual(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "extension_required",
      error_code: "extension_unavailable",
    })?.error_code,
    "extension_unavailable",
  );
  assert.deepEqual(
    buildSafeNativeSaveE2EResult(request, {
      task_status: "failed",
      error_code: "native_confirmation_not_observed",
    })?.error_code,
    "native_confirmation_not_observed",
  );
  for (const errorCode of [
    "native_content_not_ready",
    "native_control_not_found",
    "native_dialog_not_opened",
    "native_request_rejected",
    "native_target_not_found",
  ]) {
    assert.equal(buildSafeNativeSaveE2EResult(request, {
      task_status: "failed",
      error_code: errorCode,
    })?.error_code, errorCode);
  }
});

test("e2e background runner rejects native-save mutation without exact authorization", async () => {
  const state = installChromeMock();

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-blocked",
      token: "callback-token",
      platforms: ["reddit"],
      actions: { reddit: ["favorite"] },
      allow_state_changing: true,
      timeout_seconds: 5,
    });

    assert.equal(handled, true);
    assert.deepEqual(state.createdTabs, []);
    assert.deepEqual(state.sentMessages, []);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-native-save-blocked",
      token: "callback-token",
      platforms: [{
        platform: "reddit",
        status: "failed",
        actions: [],
        error: "native-save e2e authorization required",
      }],
    });
  } finally {
    state.restore();
  }
});

test("generic e2e runner refuses native-save mutation even with exact authorization", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "favorite", status: "ok", detail: "clicked" }],
  });

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-authorized",
      token: "callback-token",
      platforms: ["reddit"],
      actions: { reddit: ["favorite"] },
      allow_state_changing: true,
      timeout_seconds: 5,
      native_save_authorization: {
        allow_state_changing: true,
        platform: "reddit",
        action: "favorite",
        content_id: "t3_public1",
        expected_target: "Reddit Saved",
      },
    });

    assert.deepEqual(state.createdTabs, []);
    assert.deepEqual(state.sentMessages, []);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-native-save-authorized",
      token: "callback-token",
      platforms: [{
        platform: "reddit",
        status: "failed",
        actions: [],
        error: "native-save e2e requires durable broker execution",
      }],
    });
  } finally {
    state.restore();
  }
});

test("dedicated native-save e2e invokes one durable broker item and posts only safe result", async () => {
  const state = installChromeMock();
  let pollCount = 0;
  state.fetchImpl = async (input, init) => {
    const url = String(input);
    state.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/saved/watch_later/sync")) {
      return new Response(JSON.stringify({
        task_id: "11111111-1111-4111-8111-111111111111",
        items: [{
          item_key: "youtube:dQw4w9WgXcQ",
          status: "pending",
          resolved_action: "watch_later",
          resolved_target: "",
          error_code: "",
          error_message: "",
        }],
      }), { status: 200 });
    }
    if (url.endsWith("/api/saved-sync/tasks/11111111-1111-4111-8111-111111111111")) {
      pollCount += 1;
      return new Response(JSON.stringify({
        task_id: "11111111-1111-4111-8111-111111111111",
        items: [{
          item_key: "youtube:dQw4w9WgXcQ",
          status: "already_synced",
          resolved_action: "watch_later",
          resolved_target: "YouTube Watch Later",
          error_code: "",
          error_message: "Cookie: secret-token",
          url: "https://youtube.com/watch?v=dQw4w9WgXcQ&token=secret",
        }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-durable",
      token: "callback-token",
      platforms: [],
      actions: {},
      allow_state_changing: true,
      timeout_seconds: 5,
      native_save_execution_deadline_ms: Date.now() + 4_000,
      native_save_callback_deadline_ms: Date.now() + 5_000,
      native_save_authorization: {
        allow_state_changing: true,
        platform: "youtube",
        action: "watch_later",
        content_id: "dQw4w9WgXcQ",
        expected_target: "YouTube Watch Later",
      },
    });

    assert.equal(handled, true);
    assert.equal(pollCount, 1);
    assert.deepEqual(state.createdTabs, []);
    assert.deepEqual(state.sentMessages, []);
    assert.deepEqual(state.fetchCalls[0], {
      url: "http://127.0.0.1:8420/api/saved/watch_later/sync",
      method: "POST",
      body: { item_keys: ["youtube:dQw4w9WgXcQ"] },
    });
    assert.deepEqual(state.fetchCalls.at(-1)?.body, {
      run_id: "e2e-native-save-durable",
      token: "callback-token",
      native_save_result: {
        platform: "youtube",
        action: "watch_later",
        content_id: "dQw4w9WgXcQ",
        expected_target: "YouTube Watch Later",
        task_status: "already_synced",
        error_code: "",
      },
    });
    assert.deepEqual(Object.keys(state.fetchCalls.at(-1)?.body?.native_save_result ?? {}), [
      "platform",
      "action",
      "content_id",
      "expected_target",
      "task_status",
      "error_code",
    ]);
  } finally {
    state.restore();
  }
});

test("dedicated native-save e2e preserves an uncertain confirmation terminal", async () => {
  const state = installChromeMock();
  state.fetchImpl = async (input, init) => {
    const url = String(input);
    state.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/saved/favorite/sync")) {
      return new Response(JSON.stringify({
        task_id: "12121212-1212-4121-8121-121212121212",
        items: [{
          item_key: "zhihu:answer:2002",
          status: "pending",
          resolved_action: "favorite",
          resolved_target: "",
          error_code: "",
        }],
      }), { status: 200 });
    }
    if (url.endsWith("/api/saved-sync/tasks/12121212-1212-4121-8121-121212121212")) {
      return new Response(JSON.stringify({
        task_id: "12121212-1212-4121-8121-121212121212",
        items: [{
          item_key: "zhihu:answer:2002",
          status: "failed",
          resolved_action: "favorite",
          resolved_target: "知乎收藏",
          error_code: "native_confirmation_not_observed",
        }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-uncertain",
      token: "callback-token",
      platforms: [],
      actions: {},
      allow_state_changing: true,
      timeout_seconds: 5,
      native_save_execution_deadline_ms: Date.now() + 4_000,
      native_save_callback_deadline_ms: Date.now() + 5_000,
      native_save_authorization: {
        allow_state_changing: true,
        platform: "zhihu",
        action: "favorite",
        content_id: "answer:2002",
        expected_target: "知乎收藏",
      },
    });

    assert.deepEqual(state.fetchCalls.at(-1)?.body?.native_save_result, {
      platform: "zhihu",
      action: "favorite",
      content_id: "answer:2002",
      expected_target: "知乎收藏",
      task_status: "failed",
      error_code: "native_confirmation_not_observed",
    });
  } finally {
    state.restore();
  }
});

test("dedicated native-save e2e fails closed when broker returns a different item", async () => {
  const state = installChromeMock();
  state.fetchImpl = async (input, init) => {
    const url = String(input);
    state.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/saved/favorite/sync")) {
      return new Response(JSON.stringify({
        task_id: "22222222-2222-4222-8222-222222222222",
        items: [{
          item_key: "reddit:t3_different",
          status: "synced",
          resolved_action: "favorite",
          resolved_target: "Reddit Saved",
          error_code: "",
        }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-mismatch",
      token: "callback-token",
      platforms: [],
      actions: {},
      allow_state_changing: true,
      timeout_seconds: 5,
      native_save_execution_deadline_ms: Date.now() + 4_000,
      native_save_callback_deadline_ms: Date.now() + 5_000,
      native_save_authorization: {
        allow_state_changing: true,
        platform: "reddit",
        action: "favorite",
        content_id: "t3_public1",
        expected_target: "Reddit Saved",
      },
    });

    assert.equal(state.fetchCalls.length, 2);
    assert.deepEqual(state.fetchCalls[1].body?.native_save_result, {
      platform: "reddit",
      action: "favorite",
      content_id: "t3_public1",
      expected_target: "Reddit Saved",
      task_status: "pending",
      error_code: "",
    });
  } finally {
    state.restore();
  }
});

test("dedicated native-save e2e bounds a hung saved-sync request", async () => {
  const state = installChromeMock();
  state.fetchImpl = async (input, init) => {
    const url = String(input);
    state.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/saved/favorite/sync")) return new Promise(() => {});
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    let hungTimer: ReturnType<typeof setTimeout> | undefined;
    const outcome = await Promise.race([
      handleE2ERuntimeEvent({
        type: "extension_e2e_run",
        run_id: "e2e-native-save-timeout",
        token: "callback-token",
        platforms: [],
        actions: {},
        allow_state_changing: true,
        timeout_seconds: 0.2,
        native_save_execution_deadline_ms: Date.now() + 200,
        native_save_callback_deadline_ms: Date.now() + 2_000,
        native_save_authorization: {
          allow_state_changing: true,
          platform: "reddit",
          action: "favorite",
          content_id: "t3_public1",
          expected_target: "Reddit Saved",
        },
      }).then(() => "resolved"),
      new Promise<"hung">((resolveHung) => {
        hungTimer = setTimeout(() => resolveHung("hung"), 3_000);
      }),
    ]);
    if (hungTimer !== undefined) clearTimeout(hungTimer);

    assert.equal(outcome, "resolved");
    assert.deepEqual(state.fetchCalls.at(-1)?.body?.native_save_result, {
      platform: "reddit",
      action: "favorite",
      content_id: "t3_public1",
      expected_target: "Reddit Saved",
      task_status: "pending",
      error_code: "",
    });
  } finally {
    state.restore();
  }
});

test("native-save e2e deadline includes slow endpoint resolution and authentication", async () => {
  let fetchStarted = false;
  await assert.rejects(
    authenticatedFetchBefore(
      Date.now() + 10,
      delay(40).then(() => "http://127.0.0.1:8420/api/saved/favorite/sync"),
      {},
      async () => {
        fetchStarted = true;
        return new Response("{}", { status: 200 });
      },
    ),
    /timed out/,
  );
  await delay(50);
  assert.equal(fetchStarted, false);

  await assert.rejects(
    authenticatedFetchBefore(
      Date.now() + 10,
      Promise.resolve("http://127.0.0.1:8420/api/saved/favorite/sync"),
      {},
      async () => new Promise(() => {}),
    ),
    /timed out/,
  );
});

test("native-save e2e clamps final poll sleep and preserves callback budget", async () => {
  const state = installChromeMock();
  state.fetchImpl = async (input, init) => {
    const url = String(input);
    state.fetchCalls.push({
      url,
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/saved/favorite/sync") || url.includes("/api/saved-sync/tasks/")) {
      return new Response(JSON.stringify({
        task_id: "33333333-3333-4333-8333-333333333333",
        items: [{
          item_key: "reddit:t3_public1",
          status: "pending",
          resolved_action: "favorite",
          resolved_target: "",
          error_code: "",
        }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    const startedAt = Date.now();
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-native-save-margin",
      token: "callback-token",
      platforms: [],
      actions: {},
      allow_state_changing: true,
      timeout_seconds: 0.1,
      native_save_execution_deadline_ms: startedAt + 30,
      native_save_callback_deadline_ms: startedAt + 120,
      native_save_authorization: {
        allow_state_changing: true,
        platform: "reddit",
        action: "favorite",
        content_id: "t3_public1",
        expected_target: "Reddit Saved",
      },
    });

    assert.ok(Date.now() < startedAt + 120);
    assert.deepEqual(state.fetchCalls.at(-1)?.body?.native_save_result, {
      platform: "reddit",
      action: "favorite",
      content_id: "t3_public1",
      expected_target: "Reddit Saved",
      task_status: "pending",
      error_code: "",
    });
  } finally {
    state.restore();
  }
});

test("e2e background runner opens a platform tab, dispatches content execution, and posts backend result", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "click", status: "ok", detail: "clicked" }],
  });

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["click"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    assert.equal(handled, true);
    assert.deepEqual(state.createdTabs, [{ active: true, url: "https://x.com/home" }]);
    assert.deepEqual(state.sentMessages, [
      {
        tabId: 42,
        message: {
          action: "OBC_E2E_EXECUTE",
          runId: "e2e-test",
          platform: "twitter",
          actions: ["click"],
          allowStateChanging: false,
        },
      },
    ]);
    assert.equal(state.fetchCalls.length, 1);
    assert.equal(state.fetchCalls[0].method, "POST");
    assert.match(state.fetchCalls[0].url, /\/api\/extension\/e2e\/result$/);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-test",
      token: "secret",
      platforms: [
        {
          platform: "twitter",
          status: "ok",
          url: "https://x.com/home",
          actions: [{ action: "click", status: "ok", detail: "clicked" }],
        },
      ],
    });
  } finally {
    state.restore();
  }
});

test("e2e background runner supports reddit platform tabs", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "click", status: "ok", detail: "clicked" }],
  });

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-reddit",
      token: "secret",
      platforms: ["reddit"],
      actions: { reddit: ["click"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    assert.deepEqual(state.createdTabs, [{ active: true, url: "https://www.reddit.com/" }]);
    assert.deepEqual(state.sentMessages[0].message, {
      action: "OBC_E2E_EXECUTE",
      runId: "e2e-reddit",
      platform: "reddit",
      actions: ["click"],
      allowStateChanging: false,
    });
  } finally {
    state.restore();
  }
});

test("e2e background runner flushes captured events before posting backend result", async () => {
  const state = installChromeMock();
  const order: string[] = [];
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "scroll", status: "ok", detail: "scrolled" }],
  });
  state.fetchImpl = async (input, init) => {
    order.push("post-result");
    state.fetchCalls.push({
      url: String(input),
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  try {
    await handleE2ERuntimeEvent(
      {
        type: "extension_e2e_run",
        run_id: "e2e-flush",
        token: "secret",
        platforms: ["douyin"],
        actions: { douyin: ["scroll"] },
        allow_state_changing: false,
        timeout_seconds: 5,
      },
      async () => {
        order.push("flush");
        assert.equal(state.fetchCalls.length, 0);
      },
    );

    assert.deepEqual(order, ["flush", "post-result"]);
    assert.equal(state.fetchCalls.length, 1);
    const body = state.fetchCalls[0].body as { run_id?: string };
    assert.equal(body.run_id, "e2e-flush");
  } finally {
    state.restore();
  }
});

test("e2e background runner reuses an existing platform tab and resets it to the platform entry", async () => {
  const state = installChromeMock();
  state.queryResult = [{ id: 7, status: "complete", url: "https://www.douyin.com/user/self" }];
  state.tabById.set(7, { id: 7, status: "complete", url: "https://www.douyin.com/user/self" });

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-reuse",
      token: "secret",
      platforms: ["douyin"],
      actions: { douyin: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    assert.deepEqual(state.createdTabs, []);
    assert.deepEqual(state.updatedTabs, [
      { tabId: 7, active: true, url: "https://www.douyin.com/" },
    ]);
    assert.equal(state.sentMessages[0].tabId, 7);
  } finally {
    state.restore();
  }
});

test("e2e background runner posts a failed platform result when content messaging throws", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => {
    throw new Error("content script unavailable");
  };

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-fail",
      token: "secret",
      platforms: ["xiaohongshu"],
      actions: { xiaohongshu: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    assert.equal(handled, true);
    assert.equal(state.fetchCalls.length, 1);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-fail",
      token: "secret",
      platforms: [
        {
          platform: "xiaohongshu",
          status: "failed",
          actions: [],
          error: "content script unavailable",
        },
      ],
    });
  } finally {
    state.restore();
  }
});

test("e2e background runner ignores non e2e runtime events", async () => {
  const state = installChromeMock();

  try {
    const handled = await handleE2ERuntimeEvent({ type: "dy_task_available" });

    assert.equal(handled, false);
    assert.deepEqual(state.createdTabs, []);
    assert.deepEqual(state.sentMessages, []);
    assert.deepEqual(state.fetchCalls, []);
  } finally {
    state.restore();
  }
});

test("e2e background runner rejects concurrent runs with a failed backend result", async () => {
  const state = installChromeMock();
  let releaseFirstRun!: () => void;
  state.sendMessageImpl = async () => {
    await new Promise<void>((resolve) => {
      releaseFirstRun = resolve;
    });
    return { status: "ok", actions: [{ action: "snapshot", status: "ok" }] };
  };

  try {
    const firstRun = handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-first",
      token: "first-token",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    await new Promise<void>((resolve) => setTimeout(resolve, 0));

    const secondHandled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-second",
      token: "second-token",
      platforms: ["douyin"],
      actions: { douyin: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 5,
    });

    assert.equal(secondHandled, true);
    assert.equal(state.fetchCalls.length, 1);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-second",
      token: "second-token",
      platforms: [
        {
          platform: "douyin",
          status: "failed",
          actions: [],
          error: "e2e run already in progress: e2e-first",
        },
      ],
    });

    releaseFirstRun();
    await firstRun;
    assert.equal(state.fetchCalls.length, 2);
  } finally {
    state.restore();
  }
});

test("e2e background runner times out unresolved content execution and clears active run", async () => {
  const state = installChromeMock();
  state.sendMessageImpl = async () => new Promise(() => {});

  try {
    const firstResult = await Promise.race([
      handleE2ERuntimeEvent({
        type: "extension_e2e_run",
        run_id: "e2e-content-timeout",
        token: "timeout-token",
        platforms: ["twitter"],
        actions: { twitter: ["snapshot"] },
        allow_state_changing: false,
        timeout_seconds: 0.01,
      }).then(() => "resolved"),
      delay(150).then(() => "hung"),
    ]);

    assert.equal(firstResult, "resolved");
    assert.equal(state.fetchCalls.length, 1);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-content-timeout",
      token: "timeout-token",
      platforms: [
        {
          platform: "twitter",
          status: "failed",
          actions: [],
          error: "Timed out waiting for OBC_E2E_EXECUTE response from tab 42",
        },
      ],
    });

    state.sendMessageImpl = async () => ({
      status: "ok",
      actions: [{ action: "snapshot", status: "ok" }],
    });

    const secondHandled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-after-timeout",
      token: "after-token",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.01,
    });

    assert.equal(secondHandled, true);
    assert.equal(state.fetchCalls.length, 2);
    assert.equal(state.fetchCalls[1].body?.run_id, "e2e-after-timeout");
    assert.equal(
      state.fetchCalls[1].body?.platforms?.[0]?.error,
      undefined,
      "activeRunId should be cleared after a content execution timeout",
    );
  } finally {
    state.restore();
  }
});

test("e2e background runner catches tab complete events that occur during the completion probe", async () => {
  const state = installChromeMock();
  state.nextCreatedTabStatus = "loading";
  let getCalls = 0;
  state.getImpl = async (tabId) => {
    getCalls += 1;
    const completed = {
      id: tabId,
      status: "complete",
      url: "https://x.com/home?redirected=1",
    };
    if (getCalls === 1) {
      state.tabById.set(tabId, completed);
      state.emitTabUpdated(tabId, { status: "complete" });
      return { id: tabId, status: "loading", url: "https://x.com/home" };
    }
    return completed;
  };
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "snapshot", status: "ok" }],
  });

  try {
    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-tab-race",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    assert.equal(state.sentMessages.length, 1);
    assert.deepEqual(state.fetchCalls[0].body, {
      run_id: "e2e-tab-race",
      token: "secret",
      platforms: [
        {
          platform: "twitter",
          status: "ok",
          url: "https://x.com/home?redirected=1",
          actions: [{ action: "snapshot", status: "ok" }],
        },
      ],
    });
  } finally {
    state.restore();
  }
});

test("e2e background runner waits for an async tab complete event before content execution", async () => {
  const state = installChromeMock();
  state.nextCreatedTabStatus = "loading";
  state.sendMessageImpl = async () => ({
    status: "ok",
    actions: [{ action: "snapshot", status: "ok" }],
  });

  try {
    const run = handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-tab-complete",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    await delay(0);
    assert.equal(state.sentMessages.length, 0);
    state.tabById.set(42, { id: 42, status: "complete", url: "https://x.com/home" });
    state.emitTabUpdated(42, { status: "complete" });

    await run;
    assert.equal(state.sentMessages.length, 1);
    assert.equal(state.fetchCalls[0].body?.platforms?.[0]?.status, "ok");
  } finally {
    state.restore();
  }
});

test("e2e background runner handles result post non-2xx and clears active run", async () => {
  const state = installChromeMock();
  const originalWarn = console.warn;
  const warnings: string[] = [];
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  state.fetchImpl = async (input, init) => {
    state.fetchCalls.push({
      url: String(input),
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return new Response(JSON.stringify({ ok: false }), { status: 500 });
  };

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-post-500",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    assert.equal(handled, true);
    assert.match(warnings.join("\n"), /result POST failed: 500/);

    state.fetchImpl = async (input, init) => {
      state.fetchCalls.push({
        url: String(input),
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    };

    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-after-500",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    assert.equal(state.fetchCalls.at(-1)?.body?.run_id, "e2e-after-500");
  } finally {
    console.warn = originalWarn;
    state.restore();
  }
});

test("e2e background runner handles result post fetch rejection and clears active run", async () => {
  const state = installChromeMock();
  const originalWarn = console.warn;
  const warnings: string[] = [];
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  state.fetchImpl = async () => {
    throw new Error("backend offline");
  };

  try {
    const handled = await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-post-reject",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    assert.equal(handled, true);
    assert.match(warnings.join("\n"), /backend offline/);

    state.fetchImpl = async (input, init) => {
      state.fetchCalls.push({
        url: String(input),
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    };

    await handleE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-after-reject",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 0.05,
    });

    assert.equal(state.fetchCalls.at(-1)?.body?.run_id, "e2e-after-reject");
  } finally {
    console.warn = originalWarn;
    state.restore();
  }
});

test("service worker wires runtime stream async errors through promise catch", () => {
  const source = readFileSync(resolve("src", "background", "service-worker.ts"), "utf8");

  assert.match(
    source,
    /void handleRuntimeEvent\(payload\)\.catch\(\(err\) => \{/,
  );
});
