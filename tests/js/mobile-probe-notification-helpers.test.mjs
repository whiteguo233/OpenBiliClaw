import test from "node:test";
import assert from "node:assert/strict";

import {
  filterVisibleProbes,
  forgetHandledProbe,
  mergeProbeNotifications,
  normalizeProbeType,
  probeNotificationKey,
  rememberHandledProbe,
  removeProbeFromNotifications,
  resetHandledProbesForTests,
  shouldDisplayProbeFromWebSocket,
  shouldHydrateProbe,
} from "../../src/openbiliclaw/web/js/views/probe-notification-helpers.js";

test("probeNotificationKey normalizes type and domain", () => {
  resetHandledProbesForTests();

  assert.equal(normalizeProbeType("avoidance.probe"), "avoidance.probe");
  assert.equal(normalizeProbeType("legacy"), "interest.probe");
  assert.equal(
    probeNotificationKey("interest.probe", "  MixedCase Domain  "),
    "interest.probe:mixedcase domain",
  );
  assert.equal(probeNotificationKey("interest.probe", ""), "");
});

test("handled probes are skipped by hydrate and websocket gates", () => {
  resetHandledProbesForTests();
  rememberHandledProbe("建筑美学", "interest.probe");

  assert.equal(shouldHydrateProbe({ domain: "建筑美学" }, "interest.probe"), false);
  assert.equal(shouldDisplayProbeFromWebSocket({ domain: "建筑美学" }, "interest.probe"), false);
  assert.equal(shouldHydrateProbe({ domain: "建筑美学" }, "avoidance.probe"), true);
  assert.equal(shouldHydrateProbe({ domain: "城市基础设施", status: "confirmed" }), false);

  forgetHandledProbe("建筑美学", "interest.probe");
  assert.equal(shouldHydrateProbe({ domain: "建筑美学" }, "interest.probe"), true);
});

test("mergeProbeNotifications dedupes and filters handled probes", () => {
  resetHandledProbesForTests();
  rememberHandledProbe("浅层热点复读", "avoidance.probe");

  const merged = mergeProbeNotifications(
    [
      { type: "interest.probe", domain: "建筑美学" },
      { type: "avoidance.probe", domain: "浅层热点复读" },
    ],
    [
      { type: "interest.probe", domain: "建筑美学", reason: "duplicate" },
      { type: "interest.probe", domain: "城市基础设施" },
    ],
  );

  assert.deepEqual(merged.map((item) => `${item.type}:${item.domain}`), [
    "interest.probe:建筑美学",
    "interest.probe:城市基础设施",
  ]);
});

test("filterVisibleProbes filters a single profile list by handled state", () => {
  resetHandledProbesForTests();
  rememberHandledProbe("建筑美学", "interest.probe");

  assert.deepEqual(
    filterVisibleProbes(
      [
        { type: "interest.probe", domain: "建筑美学" },
        { type: "interest.probe", domain: "城市基础设施" },
      ],
      "interest.probe",
    ),
    [{ type: "interest.probe", domain: "城市基础设施" }],
  );
});

test("removeProbeFromNotifications keeps probe kind separate", () => {
  resetHandledProbesForTests();

  const next = removeProbeFromNotifications(
    [
      { type: "interest.probe", domain: "建筑美学" },
      { type: "avoidance.probe", domain: "建筑美学" },
      { type: "interest.probe", domain: "城市基础设施" },
    ],
    "建筑美学",
    "interest.probe",
  );
  assert.deepEqual(next, [
    { type: "avoidance.probe", domain: "建筑美学" },
    { type: "interest.probe", domain: "城市基础设施" },
  ]);
});
