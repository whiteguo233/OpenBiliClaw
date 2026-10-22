import test from "node:test";
import assert from "node:assert/strict";
import { waitForNativeSaveReadiness } from "../src/content/native-save/readiness.ts";

test("readiness keeps its wall-clock budget under background timer throttling", async () => {
  const actualNow = Date.now;
  let now = 0;
  let sleeps = 0;
  Date.now = () => now;
  try {
    assert.equal(await waitForNativeSaveReadiness(() => false, async () => {
      sleeps++; now += 5_000;
    }, 20_000), false);
    assert.equal(sleeps, 4);
  } finally { Date.now = actualNow; }
});

test("default readiness remains bounded when a test clock does not advance", async () => {
  let sleeps = 0;
  assert.equal(await waitForNativeSaveReadiness(() => false, async () => { sleeps++; }), false);
  assert.equal(sleeps, 39);
});
