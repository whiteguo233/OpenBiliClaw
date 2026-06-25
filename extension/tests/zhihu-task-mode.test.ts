import test from "node:test";
import assert from "node:assert/strict";

import {
  isZhihuTaskTabLocation,
  ZHIHU_TASK_TAB_PARAM,
  ZHIHU_TASK_TAB_URL,
} from "../src/content/zhihu/task-mode.ts";

test("Zhihu task tab marker is encoded in the hash", () => {
  assert.equal(ZHIHU_TASK_TAB_URL, `https://www.zhihu.com/#${ZHIHU_TASK_TAB_PARAM}=1`);
  assert.equal(isZhihuTaskTabLocation({ hash: `#${ZHIHU_TASK_TAB_PARAM}=1` }), true);
});

test("Zhihu task mode accepts search or hash marker only", () => {
  assert.equal(isZhihuTaskTabLocation({ search: `?${ZHIHU_TASK_TAB_PARAM}=1` }), true);
  assert.equal(isZhihuTaskTabLocation({ hash: "#other=1", search: "?q=abc" }), false);
  assert.equal(isZhihuTaskTabLocation(undefined), false);
});
