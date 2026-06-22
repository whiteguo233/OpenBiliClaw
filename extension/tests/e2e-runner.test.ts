import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { handleE2ERuntimeEvent } from "../src/background/e2e-runner.ts";
import { installChromeMock } from "./helpers/chrome-mock.ts";

function delay(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

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
