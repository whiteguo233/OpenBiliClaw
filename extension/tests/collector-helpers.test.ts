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

test("inferBilibiliActionType ignores dislike keywords in long titles and copy (issue 205)", () => {
  // Long `title` attribute carrying a full video title that merely mentions
  // the keyword — clicking the card is a view, not a dislike (#205).
  assert.equal(
    inferBilibiliActionType({
      text: "",
      ariaLabel: "不喜欢可以不看！红磷结构深度解析完整版",
      className: "",
    }),
    null,
  );
  // Long card copy mentioning 不感兴趣 must stay null (#200 regression).
  assert.equal(
    inferBilibiliActionType({
      text: "盘点UP主们不感兴趣却爆火的选题：从选题数据看流量密码，完整统计与复盘分析全在这里",
      ariaLabel: null,
      className: "",
    }),
    null,
  );
  // A long aria-label must not leak the keyword through the class path either.
  assert.equal(
    inferBilibiliActionType({
      text: "",
      ariaLabel: "不喜欢可以不看！红磷结构深度解析完整版",
      className: "video-page-card",
    }),
    null,
  );
});

test("inferBilibiliActionType still recognizes real short dislike controls (issue 205)", () => {
  assert.equal(
    inferBilibiliActionType({ text: "不感兴趣", ariaLabel: null, className: "" }),
    "dislike",
  );
  assert.equal(
    inferBilibiliActionType({ text: "不喜欢", ariaLabel: null, className: "" }),
    "dislike",
  );
  // Bilibili's video-page dislike button is class-only (e.g. video-dislike).
  assert.equal(
    inferBilibiliActionType({ text: "", ariaLabel: "", className: "video-dislike" }),
    "dislike",
  );
});

test("other zh platforms ignore negative keywords in card copy but keep short controls (issue 205)", async () => {
  const { inferDouyinActionType } = await import("../src/shared/platforms/douyin.ts");
  const { inferZhihuActionType } = await import("../src/shared/platforms/zhihu.ts");

  // Long card copy mentioning the keyword is a click, not feedback.
  assert.equal(
    inferDouyinActionType({ text: "大家都表示不喜欢这种标题党视频，完整盘点", ariaLabel: null, className: "" }),
    null,
  );
  assert.equal(
    inferZhihuActionType({ text: "为什么很多人不喜欢小提琴入门教材中的这首曲子", ariaLabel: null, className: "" }),
    null,
  );
  // Real control labels still work.
  assert.equal(inferDouyinActionType({ text: "不感兴趣", ariaLabel: null, className: "" }), "dislike");
  assert.equal(inferZhihuActionType({ text: "反对", ariaLabel: null, className: "" }), "dislike");
  assert.equal(inferZhihuActionType({ text: "赞同", ariaLabel: null, className: "" }), "like");
});

test("reddit and youtube match English keywords as whole words only (issue 205)", async () => {
  const { inferRedditActionType } = await import("../src/shared/platforms/reddit.ts");
  const { inferYoutubeActionType } = await import("../src/shared/platforms/youtube.ts");

  // Post/video titles containing inflected or compound forms are clicks.
  assert.equal(
    inferRedditActionType({ text: "This post got downvoted to oblivion", ariaLabel: null, className: "" }),
    null,
  );
  // Real reddit post titles carry the standalone verb — only anchored vote
  // controls (aria/class) may fire votes, never bare title text (#205).
  assert.equal(
    inferRedditActionType({ text: "Should I downvote this?", ariaLabel: null, className: "" }),
    null,
  );
  assert.equal(
    inferRedditActionType({ text: "", ariaLabel: "Downvote", className: "" }),
    "dislike",
  );
  assert.equal(
    inferRedditActionType({ text: "", ariaLabel: null, className: "arrow upvote" }),
    "like",
  );
  assert.equal(
    inferYoutubeActionType({ text: "This is unlikely to work", ariaLabel: null, className: "" }),
    null,
  );
  // Real controls still work, including count labels and sentence aria-labels.
  assert.equal(inferRedditActionType({ text: "32 comments", ariaLabel: null, className: "" }), "comment");
  assert.equal(inferRedditActionType({ text: "", ariaLabel: "downvote", className: "" }), "dislike");
  assert.equal(inferYoutubeActionType({ text: "Like", ariaLabel: "Like this video", className: "" }), "like");
  assert.equal(inferYoutubeActionType({ text: "", ariaLabel: "Dislike this video", className: "" }), "dislike");
  assert.equal(inferYoutubeActionType({ text: "Subscribe", ariaLabel: null, className: "" }), "follow");
});

test("youtube result-card aria-labels (full video titles) never fire votes (issue 205)", async () => {
  const { inferYoutubeActionType } = await import("../src/shared/platforms/youtube.ts");
  // Live-verified shape: search-result anchors carry aria-label =
  // "<full title> by <author> <duration>" — titles containing the verb must
  // not become feedback even as whole words.
  assert.equal(
    inferYoutubeActionType({
      text: "Why Do So Many People Dislike Cops?",
      ariaLabel: "Why Do So Many People Dislike Cops? 26分钟",
      className: "yt-simple-endpoint",
    }),
    null,
  );
  assert.equal(
    inferYoutubeActionType({
      text: "Utsu-P - dislike feat. 狐子",
      ariaLabel: "Utsu-P - dislike feat. 狐子 2分钟33秒钟",
      className: "yt-simple-endpoint",
    }),
    null,
  );
  // Verb-first aria-labels (watch-page controls) still fire.
  assert.equal(
    inferYoutubeActionType({ text: "", ariaLabel: "不喜欢此视频", className: "yt-spec-button-shape-next" }),
    "dislike",
  );
});

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

test("buildActionHintFromClickTarget reads aria-pressed from the attributed element", () => {
  const makeButton = (pressed: string | null) => {
    const button = {
      textContent: "Like",
      className: "like-btn",
      getAttribute(name: string) {
        if (name === "aria-label") return "Like";
        if (name === "aria-pressed") return pressed;
        return null;
      },
      closest() {
        return button;
      },
    };
    return button;
  };

  assert.equal(
    buildActionHintFromClickTarget(makeButton("true") as unknown as Element).pressed,
    true,
  );
  assert.equal(
    buildActionHintFromClickTarget(makeButton("false") as unknown as Element).pressed,
    false,
  );
  assert.equal(
    buildActionHintFromClickTarget(makeButton(null) as unknown as Element).pressed,
    null,
  );
  // Any non-boolean aria-pressed value ("mixed") degrades to null (fail open).
  assert.equal(
    buildActionHintFromClickTarget(makeButton("mixed") as unknown as Element).pressed,
    null,
  );
});

test("buildActionHintFromClickTarget defaults pressed to null when the attr is absent", () => {
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
  assert.equal(buildActionHintFromClickTarget(button as unknown as Element).pressed, null);
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

test("inferBilibiliActionType ignores negative keywords embedded in long card copy", () => {
  // Clicking a Bilibili video card gives the kernel an action hint whose
  // textContent is the card's full description. A passing mention of
  // "不感兴趣" in that copy must not turn the click into a dislike.
  assert.equal(
    inferBilibiliActionType({
      text: "B站越刷越无聊？你有没有想过：为什么平台总是给你推荐这些内容？点了“不感兴趣”之后，它到底有没有真的记住？",
      ariaLabel: null,
      className: "video-container-v1",
    }),
    null,
  );
  // Short control labels remain authoritative.
  assert.equal(
    inferBilibiliActionType({ text: "不感兴趣", ariaLabel: null, className: "" }),
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
    detectXiaohongshuPageType(
      "https://www.xiaohongshu.com/search_result/69dea966000000001a0280ad",
    ),
    "note",
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
