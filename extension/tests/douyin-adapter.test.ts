import test from "node:test";
import assert from "node:assert/strict";

import {
  detectDouyinPageType,
  douyinAdapter,
  extractAwemeId,
} from "../src/shared/platforms/douyin.ts";

test("douyinAdapter exposes source identity and video selector", () => {
  assert.equal(douyinAdapter.sourcePlatform, "douyin");
  assert.equal(douyinAdapter.videoSelector, "video");
});

test("detectDouyinPageType classifies common douyin pages", () => {
  assert.equal(detectDouyinPageType("https://www.douyin.com/video/7330000000000000000"), "video");
  assert.equal(detectDouyinPageType("https://www.douyin.com/search/cat"), "search");
  assert.equal(detectDouyinPageType("https://www.douyin.com/user/MS4wLjABAAAA"), "user");
  assert.equal(detectDouyinPageType("https://www.douyin.com/"), "home");
});

test("extractAwemeId pulls douyin video ids", () => {
  assert.equal(
    extractAwemeId("https://www.douyin.com/video/7330000000000000000"),
    "7330000000000000000",
  );
  assert.equal(extractAwemeId("https://www.douyin.com/user/MS4wLjABAAAA"), null);
});

test("douyinAdapter infers unified action types", () => {
  assert.equal(douyinAdapter.inferActionType({ text: "点赞", ariaLabel: null, className: "" }), "like");
  assert.equal(douyinAdapter.inferActionType({ text: "收藏", ariaLabel: null, className: "" }), "favorite");
  assert.equal(douyinAdapter.inferActionType({ text: "评论", ariaLabel: null, className: "" }), "comment");
  assert.equal(douyinAdapter.inferActionType({ text: "分享", ariaLabel: null, className: "" }), "share");
  assert.equal(douyinAdapter.inferActionType({ text: "关注", ariaLabel: null, className: "" }), "follow");
  assert.equal(
    douyinAdapter.inferActionType({ text: "不感兴趣", ariaLabel: null, className: "" }),
    "dislike",
  );
});

test("douyinAdapter metadata carries aweme_id", () => {
  assert.deepEqual(
    douyinAdapter.buildEventMetadata("https://www.douyin.com/video/7330000000000000000"),
    { aweme_id: "7330000000000000000" },
  );
});
