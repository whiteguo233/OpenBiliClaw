import test from "node:test";
import assert from "node:assert/strict";

import { makeSafariArtifactName, stapleNotarizationTicket } from "../scripts/package-safari.mjs";

test("Safari package name normalizes plain and tag-shaped versions", () => {
  assert.equal(
    makeSafariArtifactName("0.3.206", "dmg"),
    "openbiliclaw-extension-v0.3.206-safari.dmg",
  );
  assert.equal(
    makeSafariArtifactName("v0.3.206", "zip"),
    "openbiliclaw-extension-v0.3.206-safari.zip",
  );
  assert.equal(
    makeSafariArtifactName("extension-v0.3.206", "dmg"),
    "openbiliclaw-extension-v0.3.206-safari.dmg",
  );
});


test("Safari retries ticket transport per phase without resubmitting notarization", async () => {
  const calls: string[] = [];
  const waits: number[] = [];
  const counts = new Map<string, number>();
  await stapleNotarizationTicket("/test/App.app", {
    run: (command: string, args: string[]) => {
      assert.equal(command, "xcrun");
      assert.equal(args[0], "stapler");
      assert.equal(args[2], "/test/App.app");
      const phase = args[1];
      calls.push(phase);
      const count = (counts.get(phase) ?? 0) + 1;
      counts.set(phase, count);
      if (count === 1) throw Object.assign(new Error("transport"), {
        status: 68, stderr: 'NSURLErrorDomain Code=-1005 "The network connection was lost."',
      });
      return "";
    },
    wait: async (ms: number) => { waits.push(ms); },
    warn: () => {},
  });
  assert.deepEqual(calls, ["staple", "staple", "validate", "validate"]);
  assert.deepEqual(waits, [5000, 5000]);
});

test("Safari fails closed for a permanent ticket validation error", async () => {
  const calls: string[] = [];
  const invalid = Object.assign(new Error("invalid ticket"), { status: 68, stderr: "Invalid signature" });
  await assert.rejects(stapleNotarizationTicket("/test/App.app", {
    run: (_: string, args: string[]) => {
      calls.push(args[1]);
      if (args[1] === "validate") throw invalid;
      return "";
    },
    wait: async () => { assert.fail("permanent failures must not retry"); },
  }), (error: unknown) => error === invalid);
  assert.deepEqual(calls, ["staple", "validate"]);
});

test("Safari stops after four failed Apple transport attempts", async () => {
  let calls = 0;
  const waits: number[] = [];
  const offline = Object.assign(new Error("offline"), { status: 68, stderr: "NSURLErrorDomain Code=-1009" });
  await assert.rejects(stapleNotarizationTicket("/test/App.app", {
    run: () => { calls += 1; throw offline; },
    wait: async (ms: number) => { waits.push(ms); },
    warn: () => {},
  }), (error: unknown) => error === offline);
  assert.equal(calls, 4);
  assert.deepEqual(waits, [5000, 15000, 30000]);
});

test("Safari retries a bounded ticket command timeout", async () => {
  let calls = 0;
  await stapleNotarizationTicket("/test/App.app", {
    run: () => {
      calls += 1;
      if (calls === 1) throw Object.assign(new Error("timeout"), { code: "ETIMEDOUT" });
      return "";
    },
    wait: async () => {},
    warn: () => {},
  });
  assert.equal(calls, 3);
});
