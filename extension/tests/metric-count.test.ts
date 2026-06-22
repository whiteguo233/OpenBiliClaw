import test from "node:test";
import assert from "node:assert/strict";

import { normalizeMetricCountText } from "../src/content/metric-count.ts";

test("normalizeMetricCountText parses compact platform counts", () => {
  assert.equal(normalizeMetricCountText("1.2万"), 12_000);
  assert.equal(normalizeMetricCountText("3k"), 3_000);
  assert.equal(normalizeMetricCountText("1,234"), 1_234);
  assert.equal(normalizeMetricCountText("赞 42"), 42);
  assert.equal(normalizeMetricCountText(""), 0);
  assert.equal(normalizeMetricCountText("--"), 0);
});
