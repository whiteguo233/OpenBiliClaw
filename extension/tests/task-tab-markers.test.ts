import assert from "node:assert/strict";
import test from "node:test";

import {
  isTaskTabUrl,
  TASK_TAB_MARKERS,
  withTaskTabMarker,
} from "../src/shared/task-tab.ts";

test("withTaskTabMarker adds a query marker without losing existing parameters", () => {
  assert.equal(
    withTaskTabMarker(
      "https://www.xiaohongshu.com/search_result?keyword=cat",
      "openbiliclaw_xhs_task",
    ),
    "https://www.xiaohongshu.com/search_result?keyword=cat&openbiliclaw_xhs_task=1",
  );
  assert.equal(
    withTaskTabMarker("https://www.douyin.com/", "openbiliclaw_dy_task"),
    "https://www.douyin.com/?openbiliclaw_dy_task=1",
  );
});

test("isTaskTabUrl recognizes task markers in query and hash", () => {
  assert.equal(isTaskTabUrl("https://www.xiaohongshu.com/explore?openbiliclaw_xhs_task=1"), true);
  assert.equal(isTaskTabUrl("https://www.zhihu.com/#openbiliclaw_zhihu_task=1"), true);
  assert.equal(isTaskTabUrl("https://www.douyin.com/"), false);
  assert.equal(isTaskTabUrl("https://www.bilibili.com/video/BV1xx"), false);
});

test("TASK_TAB_MARKERS covers the active background task surfaces", () => {
  for (const marker of [
    "openbiliclaw_xhs_task=1",
    "openbiliclaw_dy_task=1",
    "openbiliclaw_bili_task=1",
    "openbiliclaw_yt_task=1",
  ]) {
    assert.ok(TASK_TAB_MARKERS.includes(marker));
  }
});
