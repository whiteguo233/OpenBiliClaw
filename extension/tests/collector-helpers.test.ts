import test from "node:test";
import assert from "node:assert/strict";

import {
  detectPageType,
  extractBvid,
  inferActionType,
} from "../src/shared/behavior.ts";
import {
  buildDedupeKey,
  enqueueBufferedEvent,
} from "../src/background/buffer.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

function makeEvent(
  type: string,
  overrides: Partial<BehaviorEvent> = {},
): BehaviorEvent {
  return {
    type,
    url: "https://www.bilibili.com/video/BV1AB411c7mD",
    title: "示例视频",
    timestamp: 1_710_000_000_000,
    context: {
      pageType: "video",
      viewport: { width: 1440, height: 900 },
      scrollPosition: 0,
    },
    metadata: {},
    ...overrides,
  };
}

test("detectPageType classifies common bilibili pages", () => {
  assert.equal(
    detectPageType("https://www.bilibili.com/video/BV1AB411c7mD"),
    "video",
  );
  assert.equal(
    detectPageType("https://search.bilibili.com/all?keyword=test"),
    "search",
  );
  assert.equal(detectPageType("https://space.bilibili.com/12345"), "user");
  assert.equal(detectPageType("https://www.bilibili.com/v/knowledge/"), "category");
  assert.equal(detectPageType("https://www.bilibili.com/"), "home");
});

test("extractBvid returns BV id from video url", () => {
  assert.equal(
    extractBvid("https://www.bilibili.com/video/BV1AB411c7mD?p=2"),
    "BV1AB411c7mD",
  );
  assert.equal(extractBvid("https://www.bilibili.com/"), null);
});

test("inferActionType recognizes common bilibili action buttons", () => {
  assert.equal(inferActionType({ text: "点赞", ariaLabel: null, className: "" }), "like");
  assert.equal(inferActionType({ text: "", ariaLabel: "投币", className: "" }), "coin");
  assert.equal(
    inferActionType({ text: "收藏", ariaLabel: null, className: "collect-btn" }),
    "favorite",
  );
  assert.equal(
    inferActionType({ text: "发表评论", ariaLabel: null, className: "comment-submit" }),
    "comment",
  );
  assert.equal(inferActionType({ text: "分享", ariaLabel: null, className: "" }), null);
});

test("buildDedupeKey collapses high-frequency page events", () => {
  const scrollEvent = makeEvent("scroll");
  const hoverEvent = makeEvent("hover", { metadata: { href: "/video/BV1Xx" } });
  const clickEvent = makeEvent("click");

  assert.match(buildDedupeKey(scrollEvent), /^scroll:/);
  assert.match(buildDedupeKey(hoverEvent), /^hover:/);
  assert.equal(buildDedupeKey(clickEvent), null);
});

test("enqueueBufferedEvent replaces duplicate scroll events instead of growing buffer", () => {
  const first = makeEvent("scroll", {
    timestamp: 100,
    context: {
      pageType: "video",
      viewport: { width: 1280, height: 720 },
      scrollPosition: 120,
    },
    metadata: { scrollRatio: 0.3 },
  });
  const second = makeEvent("scroll", {
    timestamp: 200,
    context: {
      pageType: "video",
      viewport: { width: 1280, height: 720 },
      scrollPosition: 360,
    },
    metadata: { scrollRatio: 0.8 },
  });

  const withFirst = enqueueBufferedEvent([], first, 50);
  const withSecond = enqueueBufferedEvent(withFirst, second, 50);

  assert.equal(withFirst.length, 1);
  assert.equal(withSecond.length, 1);
  assert.equal(withSecond[0]?.timestamp, 200);
  assert.deepEqual(withSecond[0]?.metadata, { scrollRatio: 0.8 });
});
