import test from "node:test";
import assert from "node:assert/strict";

import {
  createRuntimeStreamClient,
  createRuntimeStreamUrl,
} from "../popup/popup-stream.js";

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
  FakeWebSocket.latest?.onopen?.();
  FakeWebSocket.latest?.onclose?.();
  assert.equal(disconnected, true);

  client.disconnect();
});

test("runtime stream client resolves backend URL dynamically when no explicit backendUrl is given", async () => {
  FakeWebSocket.latest = null;
  const client = createRuntimeStreamClient({
    backendUrl: null,
    resolveBackendUrl: async () => "http://127.0.0.1:19090/api",
    WebSocketImpl: FakeWebSocket as never,
  });

  client.connect();
  // resolveBackendUrl is async — wait one microtask flush before checking.
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(FakeWebSocket.latest?.url, "ws://127.0.0.1:19090/api/runtime-stream");
  client.disconnect();
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
  FakeWebSocket.latest?.onopen?.();
  assert.deepEqual(events, ["connect", "disconnect", "connect"]);

  client.disconnect();
});
