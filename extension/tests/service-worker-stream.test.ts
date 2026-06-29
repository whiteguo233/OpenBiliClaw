import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("runtime stream connection gates concurrent async health probes", () => {
  const source = readFileSync(resolve("src", "background", "service-worker.ts"), "utf8");
  const connectStart = source.indexOf("async function connectRuntimeStream");
  const connectEnd = source.indexOf("function scheduleWsReconnect", connectStart);
  const connectBlock = source.slice(connectStart, connectEnd);

  assert.match(source, /let runtimeConnectInFlight = false;/);
  assert.match(connectBlock, /runtimeSocket !== null \|\| runtimeConnectInFlight/);

  const markInFlight = connectBlock.indexOf("runtimeConnectInFlight = true");
  const healthProbe = connectBlock.indexOf("await isBackendAlive");
  assert.ok(markInFlight >= 0, "connectRuntimeStream should mark connection as in-flight");
  assert.ok(
    markInFlight < healthProbe,
    "connectRuntimeStream should mark in-flight before awaiting the health probe",
  );

  assert.match(connectBlock, /finally \{\s*runtimeConnectInFlight = false;\s*\}/);
});

test("service worker starts platform task polling during hot reload bootstrap", () => {
  const source = readFileSync(resolve("src", "background", "service-worker.ts"), "utf8");
  const bootstrapStart = source.indexOf("ensureFlushAlarm();", source.indexOf("chrome.notifications"));
  const bootstrapEnd = source.indexOf("onBackendEndpointChange", bootstrapStart);
  const bootstrapBlock = source.slice(bootstrapStart, bootstrapEnd);

  assert.match(source, /function startPlatformTaskPolling\(\): void \{/);
  assert.match(bootstrapBlock, /startPlatformTaskPolling\(\);/);
  assert.match(bootstrapBlock, /startCookieSync\(\);/);
});

test("background runtime stream reconnect uses a fixed high-frequency interval", () => {
  const source = readFileSync(resolve("src", "background", "service-worker.ts"), "utf8");
  const scheduleStart = source.indexOf("function scheduleWsReconnect");
  const scheduleEnd = source.indexOf("// ---------------------------------------------------------------------------", scheduleStart);
  const scheduleBlock = source.slice(scheduleStart, scheduleEnd);

  assert.match(source, /const WS_RECONNECT_DELAY = 1_000;/);
  assert.doesNotMatch(source, /WS_RECONNECT_MAX_DELAY/);
  assert.doesNotMatch(source, /wsReconnectDelay/);
  assert.doesNotMatch(source, /Math\.min\(wsReconnectDelay \* 2/);
  assert.match(scheduleBlock, /}, WS_RECONNECT_DELAY\);/);
});
