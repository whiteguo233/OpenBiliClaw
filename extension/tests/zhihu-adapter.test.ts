import test from "node:test";
import assert from "node:assert/strict";

import {
  detectZhihuPageType,
  extractZhihuContentId,
  zhihuAdapter,
} from "../src/shared/platforms/zhihu.ts";

test("zhihuAdapter exposes source identity", () => {
  assert.equal(zhihuAdapter.sourcePlatform, "zhihu");
  assert.equal(zhihuAdapter.videoSelector, null);
});

test("detectZhihuPageType classifies common Zhihu pages", () => {
  assert.equal(
    detectZhihuPageType("https://www.zhihu.com/question/123/answer/456"),
    "answer",
  );
  assert.equal(detectZhihuPageType("https://zhuanlan.zhihu.com/p/789"), "article");
  assert.equal(detectZhihuPageType("https://www.zhihu.com/search?q=AI"), "search");
  assert.equal(detectZhihuPageType("https://www.zhihu.com/people/demo"), "profile");
  assert.equal(detectZhihuPageType("https://www.zhihu.com/"), "home");
});

test("extractZhihuContentId reads answer, article and question URLs", () => {
  assert.equal(
    extractZhihuContentId("https://www.zhihu.com/question/123/answer/456"),
    "answer:456",
  );
  assert.equal(extractZhihuContentId("https://zhuanlan.zhihu.com/p/789"), "article:789");
  assert.equal(extractZhihuContentId("https://www.zhihu.com/question/123"), "question:123");
  assert.equal(extractZhihuContentId("https://www.zhihu.com/search?q=AI"), null);
});

test("zhihuAdapter infers unified action types", () => {
  assert.equal(zhihuAdapter.inferActionType({ text: "赞同", ariaLabel: null, className: "" }), "like");
  assert.equal(zhihuAdapter.inferActionType({ text: "喜欢", ariaLabel: null, className: "" }), "like");
  assert.equal(zhihuAdapter.inferActionType({ text: "收藏", ariaLabel: null, className: "" }), "favorite");
  assert.equal(zhihuAdapter.inferActionType({ text: "关注", ariaLabel: null, className: "" }), "follow");
  assert.equal(zhihuAdapter.inferActionType({ text: "评论", ariaLabel: null, className: "" }), "comment");
});
