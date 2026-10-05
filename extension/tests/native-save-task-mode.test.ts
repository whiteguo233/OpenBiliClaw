import assert from "node:assert/strict";
import test from "node:test";

import { shouldStartPassiveCollector } from "../src/content/native-save/task-mode.ts";

test("native-save task tabs suppress passive behavior collection", async () => {
  assert.equal(await shouldStartPassiveCollector(async () => ({
    native_save_task_tab: true,
  })), false);
});

test("ordinary tabs and worker restarts keep passive behavior collection", async () => {
  assert.equal(await shouldStartPassiveCollector(async () => ({
    native_save_task_tab: false,
  })), true);
  assert.equal(await shouldStartPassiveCollector(async () => {
    throw new Error("worker restarting");
  }), true);
});
