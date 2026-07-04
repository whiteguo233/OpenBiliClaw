import test from "node:test";
import assert from "node:assert/strict";

import {
  OFFLINE_BACKEND_POLL_INTERVAL_MS,
  createOfflineBackendPoller,
} from "../popup/popup-connection-poller.js";

test("offline backend poller retries until liveness recovers", async () => {
  const delays: number[] = [];
  const timers = new Map<number, () => Promise<void>>();
  let nextTimerId = 1;
  let online = false;
  const probeResults = [false, true];
  let onlineCallbackCount = 0;

  const poller = createOfflineBackendPoller({
    isOnline: () => online,
    checkBackendStatus: async () => probeResults.shift() ?? false,
    onOnline: async () => {
      online = true;
      onlineCallbackCount += 1;
    },
    setTimeoutImpl(callback: () => Promise<void>, delay: number) {
      const id = nextTimerId++;
      delays.push(delay);
      timers.set(id, callback);
      return id;
    },
    clearTimeoutImpl(id: number) {
      timers.delete(id);
    },
  });

  const runNextTimer = async () => {
    const entry = timers.entries().next().value;
    assert.ok(entry, "expected a scheduled timer");
    const [id, callback] = entry;
    timers.delete(id);
    await callback();
  };

  poller.start();
  assert.deepEqual(delays, [OFFLINE_BACKEND_POLL_INTERVAL_MS]);

  await runNextTimer();
  assert.equal(online, false);
  assert.deepEqual(delays, [
    OFFLINE_BACKEND_POLL_INTERVAL_MS,
    OFFLINE_BACKEND_POLL_INTERVAL_MS,
  ]);

  await runNextTimer();
  assert.equal(online, true);
  assert.equal(onlineCallbackCount, 1);
  assert.equal(timers.size, 0);
});

test("offline backend poller does not schedule while already online", () => {
  const delays: number[] = [];
  const poller = createOfflineBackendPoller({
    isOnline: () => true,
    checkBackendStatus: async () => true,
    onOnline: async () => {},
    setTimeoutImpl(_callback: () => Promise<void>, delay: number) {
      delays.push(delay);
      return 1;
    },
    clearTimeoutImpl() {},
  });

  poller.start();

  assert.deepEqual(delays, []);
});
