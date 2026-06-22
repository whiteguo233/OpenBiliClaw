import assert from "node:assert/strict";
import test from "node:test";

import {
  actionsForE2EPlatform,
  isActionAllowed,
  isExtensionE2ERuntimeEvent,
} from "../src/shared/e2e.ts";

test("isExtensionE2ERuntimeEvent recognizes signed extension e2e runtime events", () => {
  assert.equal(
    isExtensionE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 30,
    }),
    true,
  );

  assert.equal(
    isExtensionE2ERuntimeEvent({
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["youtube"],
      actions: { youtube: ["snapshot"] },
      allow_state_changing: false,
      timeout_seconds: 30,
    }),
    false,
  );
});

test("actionsForE2EPlatform deduplicates requested platform actions", () => {
  const actions = actionsForE2EPlatform(
    {
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["twitter"],
      actions: { twitter: ["click", "click", "share"] },
      allow_state_changing: false,
      timeout_seconds: 30,
    },
    "twitter",
  );

  assert.deepEqual(actions, ["click", "share"]);
});

test("actionsForE2EPlatform uses default safe actions when platform actions are omitted", () => {
  const actions = actionsForE2EPlatform(
    {
      type: "extension_e2e_run",
      run_id: "e2e-test",
      token: "secret",
      platforms: ["douyin"],
      actions: {},
      allow_state_changing: false,
      timeout_seconds: 30,
    },
    "douyin",
  );

  assert.deepEqual(actions, ["snapshot", "scroll", "click", "share"]);
});

test("isActionAllowed blocks state changing actions unless explicitly allowed", () => {
  assert.equal(isActionAllowed("like", false), false);
  assert.equal(isActionAllowed("favorite", false), false);
  assert.equal(isActionAllowed("follow", true), true);
  assert.equal(isActionAllowed("share", false), true);
});
