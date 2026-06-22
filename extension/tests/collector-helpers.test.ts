import test from "node:test";
import assert from "node:assert/strict";

import {
  detectBilibiliPageType,
  extractBvid,
  inferBilibiliActionType,
  bilibiliAdapter,
} from "../src/shared/platforms/bilibili.ts";
import {
  detectXiaohongshuPageType,
  extractNoteId,
  xiaohongshuAdapter,
} from "../src/shared/platforms/xiaohongshu.ts";
import {
  buildDedupeKey,
  enqueueBufferedEvent,
  shouldFlushImmediately,
} from "../src/background/buffer.ts";
import {
  buildActionHintFromClickTarget,
  normalizeActionSignal,
} from "../src/shared/behavior.ts";
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
    source_platform: "bilibili",
    context: {
      pageType: "video",
      viewport: { width: 1440, height: 900 },
      scrollPosition: 0,
    },
    metadata: {},
    ...overrides,
  };
}

test("detectBilibiliPageType classifies common bilibili pages", () => {
  assert.equal(
    detectBilibiliPageType("https://www.bilibili.com/video/BV1AB411c7mD"),
    "video",
  );
  assert.equal(
    detectBilibiliPageType("https://search.bilibili.com/all?keyword=test"),
    "search",
  );
  assert.equal(detectBilibiliPageType("https://space.bilibili.com/12345"), "user");
  assert.equal(detectBilibiliPageType("https://www.bilibili.com/v/knowledge/"), "category");
  assert.equal(detectBilibiliPageType("https://www.bilibili.com/"), "home");
});

test("extractBvid returns BV id from video url", () => {
  assert.equal(
    extractBvid("https://www.bilibili.com/video/BV1AB411c7mD?p=2"),
    "BV1AB411c7mD",
  );
  assert.equal(extractBvid("https://www.bilibili.com/"), null);
});

test("inferBilibiliActionType recognizes common bilibili action buttons", () => {
  assert.equal(
    inferBilibiliActionType({ text: "点赞", ariaLabel: null, className: "" }),
    "like",
  );
  assert.equal(
    inferBilibiliActionType({ text: "", ariaLabel: "投币", className: "" }),
    "coin",
  );
  assert.equal(
    inferBilibiliActionType({ text: "收藏", ariaLabel: null, className: "collect-btn" }),
    "favorite",
  );
  assert.equal(
    inferBilibiliActionType({ text: "发表评论", ariaLabel: null, className: "comment-submit" }),
    "comment",
  );
  assert.equal(
    inferBilibiliActionType({ text: "分享", ariaLabel: null, className: "" }),
    "share",
  );
  assert.equal(
    inferBilibiliActionType({ text: "关注", ariaLabel: null, className: "" }),
    "follow",
  );
});

test("buildActionHintFromClickTarget reads action labels from ancestor buttons", () => {
  const button = {
    textContent: "分享",
    className: "yt-spec-button",
    getAttribute(name: string) {
      return name === "aria-label" ? "分享" : null;
    },
    closest() {
      return button;
    },
  };
  const innerIcon = {
    textContent: "",
    className: { baseVal: "icon-shape" },
    getAttribute(name: string) {
      return name === "class" ? "icon-shape" : null;
    },
    closest(selector: string) {
      return selector.includes("button") ? button : null;
    },
  };

  const hint = buildActionHintFromClickTarget(innerIcon as unknown as Element);

  assert.equal(hint.text, "分享");
  assert.equal(hint.ariaLabel, "分享");
  assert.equal(hint.className, "yt-spec-button");
  assert.equal(bilibiliAdapter.inferActionType(hint), "share");
});

test("buildActionHintFromClickTarget prefers a nested button over an outer card link", () => {
  const cardLink = {
    textContent: "Reply Repost Like Share",
    className: "tweet-card",
    getAttribute(name: string) {
      return name === "href" ? "/OpenAI/status/1" : null;
    },
  };
  const shareButton = {
    textContent: "",
    className: "tweet-share",
    getAttribute(name: string) {
      return name === "aria-label" ? "Share" : null;
    },
  };
  const icon = {
    textContent: "",
    className: { baseVal: "icon-share" },
    getAttribute(name: string) {
      return name === "class" ? "icon-share" : null;
    },
    closest(selector: string) {
      if (selector === "button,[role='button']") return shareButton;
      if (selector.includes("a")) return cardLink;
      return null;
    },
  };

  const hint = buildActionHintFromClickTarget(icon as unknown as Element);

  assert.equal(hint.text, "");
  assert.equal(hint.ariaLabel, "Share");
  assert.equal(hint.className, "tweet-share");
});

test("inferBilibiliActionType recognizes negative feedback controls", () => {
  assert.equal(
    inferBilibiliActionType({ text: "不感兴趣", ariaLabel: null, className: "" }),
    "dislike",
  );
  assert.equal(
    inferBilibiliActionType({ text: "", ariaLabel: "减少此类推荐", className: "" }),
    "dislike",
  );
  assert.equal(
    inferBilibiliActionType({ text: "", ariaLabel: "dislike", className: "" }),
    "dislike",
  );
});

test("collector normalizes dislike actions into feedback events", () => {
  const action = normalizeActionSignal("dislike", {
    targetText: "不感兴趣",
    href: null,
  });

  assert.equal(action.type, "feedback");
  assert.deepEqual(action.metadata, {
    targetText: "不感兴趣",
    href: null,
    feedback_type: "dislike",
    reaction: "thumbs_down",
  });
  assert.equal(shouldFlushImmediately(makeEvent(action.type)), true);
});

test("bilibiliAdapter wires content-id and source platform", () => {
  assert.equal(bilibiliAdapter.sourcePlatform, "bilibili");
  assert.equal(
    bilibiliAdapter.extractContentId("https://www.bilibili.com/video/BV1AB411c7mD"),
    "BV1AB411c7mD",
  );
  assert.equal(bilibiliAdapter.videoSelector, "video");
});

test("detectXiaohongshuPageType classifies common xhs pages", () => {
  assert.equal(
    detectXiaohongshuPageType(
      "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad",
    ),
    "note",
  );
  assert.equal(
    detectXiaohongshuPageType("https://www.xiaohongshu.com/search_result?keyword=cat"),
    "search",
  );
  assert.equal(
    detectXiaohongshuPageType("https://www.xiaohongshu.com/user/profile/abc123"),
    "user",
  );
  assert.equal(detectXiaohongshuPageType("https://www.xiaohongshu.com/explore"), "home");
});

test("extractNoteId pulls 24-char hex id from xhs urls", () => {
  assert.equal(
    extractNoteId("https://www.xiaohongshu.com/explore/69dea966000000001a0280ad"),
    "69dea966000000001a0280ad",
  );
  assert.equal(
    extractNoteId(
      "https://www.xiaohongshu.com/search_result/69dea966000000001a0280ad?xsec_token=abc",
    ),
    "69dea966000000001a0280ad",
  );
  assert.equal(extractNoteId("https://www.xiaohongshu.com/explore"), null);
  assert.equal(extractNoteId("https://www.bilibili.com/video/BV1AB411c7mD"), null);
});

test("xiaohongshuAdapter wires source platform and skips video observation", () => {
  assert.equal(xiaohongshuAdapter.sourcePlatform, "xiaohongshu");
  assert.equal(xiaohongshuAdapter.videoSelector, null);
});

test("xiaohongshuAdapter.inferActionType recognizes like/favorite/comment", () => {
  assert.equal(
    xiaohongshuAdapter.inferActionType({ text: "点赞", ariaLabel: null, className: "" }),
    "like",
  );
  assert.equal(
    xiaohongshuAdapter.inferActionType({ text: "", ariaLabel: "收藏", className: "" }),
    "favorite",
  );
  assert.equal(
    xiaohongshuAdapter.inferActionType({ text: "评论", ariaLabel: null, className: "" }),
    "comment",
  );
  // xhs has no coin button — text should not trigger a match.
  assert.equal(
    xiaohongshuAdapter.inferActionType({ text: "投币", ariaLabel: null, className: "" }),
    null,
  );
  assert.equal(
    xiaohongshuAdapter.inferActionType({ text: "分享", ariaLabel: null, className: "" }),
    "share",
  );
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
