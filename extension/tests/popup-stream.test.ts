import test from "node:test";
import assert from "node:assert/strict";

import {
  createRuntimeStreamClient,
  createRuntimeStreamUrl,
} from "../popup/popup-stream.js";
import { getPopupState, mergeRuntimeStatusEvent } from "../popup/popup-helpers.js";

test("createRuntimeStreamUrl converts backend http url to websocket runtime stream", () => {
  assert.equal(
    createRuntimeStreamUrl("http://127.0.0.1:8420/api"),
    "ws://127.0.0.1:8420/api/runtime-stream",
  );
  assert.equal(
    createRuntimeStreamUrl("https://api.example.com/api"),
    "wss://api.example.com/api/runtime-stream",
  );
  assert.equal(
    createRuntimeStreamUrl("http://127.0.0.1:19090/api"),
    "ws://127.0.0.1:19090/api/runtime-stream",
  );
  assert.equal(
    createRuntimeStreamUrl("https://api.example.com/api", "short-session"),
    "wss://api.example.com/api/runtime-stream?token=short-session",
  );
});

class FakeWebSocket {
  static latest: FakeWebSocket | null = null;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.latest = this;
  }

  close() {}
}

class ClosingFakeWebSocket extends FakeWebSocket {
  override close() {
    this.onclose?.();
  }
}

test("runtime stream client dispatches parsed events", async () => {
  const received: Array<Record<string, unknown>> = [];

  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    onEvent(event) {
      received.push(event);
    },
  });

  client.connect();
  FakeWebSocket.latest?.onmessage?.({
    data: JSON.stringify({
      type: "refresh.strategy",
      message: "先从你刚刚的口味里搜一轮",
      pool_available_count: 42,
    }),
  });

  assert.deepEqual(received, [
    {
      type: "refresh.strategy",
      message: "先从你刚刚的口味里搜一轮",
      pool_available_count: 42,
    },
  ]);
});

test("runtime stream client calls onConnect when socket opens", () => {
  let connected = false;

  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    onConnect() {
      connected = true;
    },
  });

  client.connect();
  assert.equal(connected, false);

  FakeWebSocket.latest?.onopen?.();
  assert.equal(connected, true);
});

test("first runtime stream connection reconciles a terminal status missed before subscription", async () => {
  const received: Array<Record<string, unknown>> = [];
  let fetches = 0;
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: async () => {
      fetches += 1;
      return { initialized: true, manual_refresh_state: "failed", manual_refresh_message: "连接失败" };
    },
    onStatusSnapshot(status) { received.push(status); },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(fetches, 1);
  assert.equal(received[0]?.manual_refresh_state, "failed");
  client.disconnect();
});

test("late connection snapshot never overwrites a newer runtime event", async () => {
  let resolveSnapshot: (value: Record<string, unknown>) => void = () => {};
  let fetches = 0;
  let status = { manual_refresh_state: "running" };
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: () => { fetches += 1; return new Promise((resolve) => { resolveSnapshot = resolve; }); },
    onStatusSnapshot(snapshot) { status = snapshot; },
    onEvent(event) { status = { manual_refresh_state: event.type === "refresh.failed" ? "failed" : "running" }; },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  assert.equal(fetches, 1);
  FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "refresh.failed" }) });
  resolveSnapshot({ manual_refresh_state: "running" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(status.manual_refresh_state, "failed");
  client.disconnect();
});

test("a stopped runtime stream ignores its pending status snapshot", async () => {
  let resolveSnapshot: (value: Record<string, unknown>) => void = () => {};
  let fetches = 0;
  let delivered = false;
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: () => { fetches += 1; return new Promise((resolve) => { resolveSnapshot = resolve; }); },
    onStatusSnapshot() { delivered = true; },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  assert.equal(fetches, 1);
  client.disconnect();
  resolveSnapshot({ manual_refresh_state: "failed" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(delivered, false);
});

test("heartbeat, activity and task wake-up frames do not discard connection catch-up", async () => {
  let resolveSnapshot: (value: Record<string, unknown>) => void = () => {};
  const received: Array<Record<string, unknown>> = [];
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: () => new Promise((resolve) => { resolveSnapshot = resolve; }),
    onStatusSnapshot(status) { received.push(status); },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  for (const type of ["runtime.heartbeat", "activity.added", "instagram_task_available"]) {
    FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify({ type }) });
  }
  resolveSnapshot({ manual_refresh_state: "failed" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(received[0]?.manual_refresh_state, "failed");
  client.disconnect();
});

for (const available of [0, 3]) test(`pool count ${available} survives catch-up without dropping a missed terminal state`, async () => {
  let resolveSnapshot: (value: Record<string, unknown>) => void = () => {};
  let status = { initialized: true, manual_refresh_state: "running", pool_available_count: 0 };
  let snapshots = 0;
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: () => new Promise((resolve) => { resolveSnapshot = resolve; }),
    onStatusSnapshot(snapshot) { snapshots += 1; status = snapshot; },
    onEvent(event) { status = mergeRuntimeStatusEvent(status, event); },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "pool_status", pool_available_count: available }) });
  resolveSnapshot({ initialized: true, manual_refresh_state: "failed", manual_refresh_message: "连接失败", pool_available_count: 0 });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(snapshots, 1);
  assert.equal(status.pool_available_count, available);
  assert.equal(status.manual_refresh_state, "failed");
  if (available === 0) assert.equal(getPopupState({ online: true, items: [], runtimeStatus: status }).kind, "discovery_failed");
  client.disconnect();
});

test("a new socket owns catch-up and rejects the old socket's late snapshot and close", async () => {
  const resolvers: Array<(value: Record<string, unknown>) => void> = [];
  const received: Array<Record<string, unknown>> = [];
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    reconnectDelayMs: 100_000,
    fetchStatus: () => new Promise((resolve) => { resolvers.push(resolve); }),
    onStatusSnapshot(status) { received.push(status); },
  });
  client.connect();
  const oldSocket = FakeWebSocket.latest;
  oldSocket?.onopen?.();
  oldSocket?.onclose?.();
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  assert.equal(resolvers.length, 2);
  oldSocket?.onclose?.();
  resolvers[1]({ manual_refresh_state: "failed" });
  resolvers[0]({ manual_refresh_state: "running" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(received, [{ manual_refresh_state: "failed" }]);
  client.disconnect();
});

test("failed connection catch-up keeps the live stream usable", async () => {
  const received: Array<Record<string, unknown>> = [];
  let snapshots = 0;
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    fetchStatus: async () => { throw new Error("offline"); },
    onStatusSnapshot() { snapshots += 1; },
    onEvent(event) { received.push(event); },
  });
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  await new Promise((resolve) => setTimeout(resolve, 0));
  FakeWebSocket.latest?.onmessage?.({ data: JSON.stringify({ type: "refresh.failed" }) });
  assert.equal(snapshots, 0);
  assert.deepEqual(received, [{ type: "refresh.failed" }]);
  client.disconnect();
});

test("runtime stream client calls onDisconnect when socket closes after being connected", () => {
  let disconnected = false;

  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    reconnectDelayMs: 100_000,
    onDisconnect() {
      disconnected = true;
    },
  });

  client.connect();

  // Close without ever connecting — should NOT trigger onDisconnect
  FakeWebSocket.latest?.onclose?.();
  assert.equal(disconnected, false);

  // Now simulate a successful connection then disconnect
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  FakeWebSocket.latest?.onclose?.();
  assert.equal(disconnected, true);

  client.disconnect();
});

test("runtime stream client does not report an intentional shutdown as a disconnect", () => {
  let disconnectCount = 0;
  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: ClosingFakeWebSocket as never,
    onDisconnect() {
      disconnectCount += 1;
    },
  });

  client.connect();
  FakeWebSocket.latest?.onopen?.();
  client.disconnect();

  assert.equal(disconnectCount, 0);
});

test("runtime stream client resolves backend URL dynamically when no explicit backendUrl is given", async () => {
  FakeWebSocket.latest = null;
  const client = createRuntimeStreamClient({
    backendUrl: null,
    resolveBackendUrl: async () => "http://127.0.0.1:19090/api",
    resolveSessionToken: async () => "session-token",
    WebSocketImpl: FakeWebSocket as never,
  });

  client.connect();
  // resolveBackendUrl is async — wait one microtask flush before checking.
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(
    FakeWebSocket.latest?.url,
    "ws://127.0.0.1:19090/api/runtime-stream?token=session-token",
  );
  client.disconnect();
});

test("runtime stream client keeps a fixed reconnect delay across repeated failures", () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const delays: number[] = [];
  const callbacks: Array<() => void> = [];

  globalThis.setTimeout = ((callback: () => void, delay?: number) => {
    delays.push(Number(delay ?? 0));
    callbacks.push(callback);
    return callbacks.length as never;
  }) as typeof setTimeout;
  globalThis.clearTimeout = (() => {}) as typeof clearTimeout;

  try {
    const client = createRuntimeStreamClient({
      backendUrl: "http://127.0.0.1:8420/api",
      WebSocketImpl: FakeWebSocket as never,
      reconnectDelayMs: 750,
    });

    client.connect();
    FakeWebSocket.latest?.onclose?.();
    callbacks.shift()?.();
    FakeWebSocket.latest?.onclose?.();

    assert.deepEqual(delays, [750, 750]);
    client.disconnect();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

test("runtime stream client resets wasConnected after disconnect so reconnect triggers onConnect again", () => {
  const events: string[] = [];

  const client = createRuntimeStreamClient({
    backendUrl: "http://127.0.0.1:8420/api",
    WebSocketImpl: FakeWebSocket as never,
    reconnectDelayMs: 100_000,
    onConnect() {
      events.push("connect");
    },
    onDisconnect() {
      events.push("disconnect");
    },
  });

  client.connect();
  FakeWebSocket.latest?.onopen?.();
  FakeWebSocket.latest?.onclose?.();
  assert.deepEqual(events, ["connect", "disconnect"]);

  // Simulate reconnect (new socket created)
  client.connect();
  FakeWebSocket.latest?.onopen?.();
  assert.deepEqual(events, ["connect", "disconnect", "connect"]);

  client.disconnect();
});
