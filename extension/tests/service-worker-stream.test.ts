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
