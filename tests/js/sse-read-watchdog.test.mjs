import test from "node:test";
import assert from "node:assert/strict";

// api.js derives BASE_URL from the page origin at module evaluation time.
globalThis.location = { protocol: "http:", host: "test.local", href: "http://test.local/" };

await import("../../src/openbiliclaw/web/shared/agent-chat.js");
const { createSseReadWatchdog, SSE_READ_WATCHDOG_MS } = globalThis.OpenBiliClawAgentChat;
const { streamAgentChatTurn } = await import("../../src/openbiliclaw/web/js/api.js");

const encoder = new TextEncoder();

/** Response stub whose reader never yields a byte until the fetch signal aborts. */
function installStalledSseFetch() {
  let capturedSignal = null;
  globalThis.fetch = async (_url, options = {}) => {
    capturedSignal = options.signal;
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: () =>
            new Promise((_resolve, reject) => {
              capturedSignal.addEventListener("abort", () => reject(capturedSignal.reason));
            }),
        }),
      },
    };
  };
  return () => capturedSignal;
}

/** Response stub emitting ``chunks`` with ``delayMs`` between reads, then EOF. */
function installChunkedSseFetch(chunks, delayMs = 0) {
  globalThis.fetch = async () => ({
    ok: true,
    body: {
      getReader: () => {
        let index = 0;
        return {
          read: async () => {
            if (index >= chunks.length) return { done: true };
            if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
            const value = encoder.encode(chunks[index]);
            index += 1;
            return { done: false, value };
          },
        };
      },
    },
  });
}

test("watchdog aborts the signal after a silent window", async () => {
  const watchdog = createSseReadWatchdog({ timeoutMs: 20 });
  const aborted = new Promise((resolve) => watchdog.signal.addEventListener("abort", resolve));
  watchdog.reset();
  await aborted;
  assert.equal(watchdog.signal.aborted, true);
  assert.match(String(watchdog.signal.reason?.message || ""), /SSE/);
});

test("reset postpones the abort and cancel disarms it", async () => {
  const watchdog = createSseReadWatchdog({ timeoutMs: 40 });
  watchdog.reset();
  await new Promise((resolve) => setTimeout(resolve, 25));
  watchdog.reset(); // 字节到达 → 重新计时
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.equal(watchdog.signal.aborted, false);
  watchdog.cancel();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(watchdog.signal.aborted, false);
});

test("default watchdog window is 60s", () => {
  assert.equal(SSE_READ_WATCHDOG_MS, 60_000);
});

test("stalled SSE stream rejects instead of hanging forever", async () => {
  installStalledSseFetch();
  const startedAt = Date.now();
  await assert.rejects(streamAgentChatTurn({ message: "你好", watchdogMs: 30, onEvent() {} }));
  assert.ok(Date.now() - startedAt < 5_000, "watchdog abort should settle the stream quickly");
});

test("heartbeat comments feed the watchdog and the stream completes", async () => {
  installChunkedSseFetch(
    [": ping\n\n", ": ping\n\n", 'event: done\ndata: {"reply":"想好了"}\n\n'],
    20,
  );
  const seen = [];
  const done = await streamAgentChatTurn({
    message: "你好",
    watchdogMs: 200,
    onEvent: (name, data) => seen.push([name, data]),
  });
  assert.deepEqual(done, { reply: "想好了" });
  assert.deepEqual(seen, [["done", { reply: "想好了" }]]);
});

test("SSE error frames still reject with agentStreamError", async () => {
  installChunkedSseFetch(['event: error\ndata: {"error":"模型挂了"}\n\n']);
  await assert.rejects(
    streamAgentChatTurn({ message: "你好", watchdogMs: 200, onEvent() {} }),
    (error) => {
      assert.equal(error.agentStreamError, true);
      assert.match(error.message, /模型挂了/);
      return true;
    },
  );
});
