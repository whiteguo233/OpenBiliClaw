import test from "node:test";
import assert from "node:assert/strict";
import { getReadyRecommendationHint, getPoolStatusSummary, getMobileRecommendationHeaderState, mergeRuntimeStatusEvent } from "../../src/openbiliclaw/web/js/view-models.js";

test("mobile keeps terminal discovery failure distinct from a normal empty pool", () => {
  const failed = {
    initialized: true,
    pending_signal_events: 5,
    discovery_failure_message: "首轮内容发现未完成，画像已保存。请检查来源连接后重试。",
  };
  assert.equal(getReadyRecommendationHint(failed).tone, "error");
  assert.match(getReadyRecommendationHint(failed).message, /画像已保存/);
  assert.equal(getReadyRecommendationHint({ initialized: true }).tone, "info");
  assert.equal(getReadyRecommendationHint({ ...failed, manual_refresh_state: "running" }).tone, "info");
  assert.equal(getReadyRecommendationHint({ ...failed, manual_refresh_state: "success", discovery_failure_message: "" }).tone, "info");
  assert.equal(getReadyRecommendationHint({ ...failed, manual_refresh_state: "success" }).tone, "error");
  assert.equal(getReadyRecommendationHint({ ...failed, pool_available_count: 2 }).tone, "info");
  assert.equal(getPoolStatusSummary(failed).replenished, "内容发现未完成");
  assert.equal(getMobileRecommendationHeaderState({ runtimeStatus: failed }).poolChips[2].label, "补货状态");
  const recovered = mergeRuntimeStatusEvent(failed, { type: "refresh.pool_updated", pool_available_count: 0, discovery_failure_message: "" });
  assert.equal(getReadyRecommendationHint(recovered).tone, "info");
});
