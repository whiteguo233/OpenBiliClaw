import test from "node:test";
import assert from "node:assert/strict";

import {
  buildFeedbackPayload,
  buildVideoUrl,
  getConnectionBadgeState,
  getPopupState,
  getTabButtonState,
  normalizeRecommendation,
  normalizeProfileSummary,
  normalizeRuntimeStatus,
  validateCommentInput,
} from "../popup/popup-helpers.js";

test("buildVideoUrl builds bilibili video url from bvid", () => {
  assert.equal(
    buildVideoUrl("BV1xx411c7mD"),
    "https://www.bilibili.com/video/BV1xx411c7mD",
  );
});

test("normalizeRecommendation fills stable fallback fields", () => {
  const item = normalizeRecommendation({
    id: 7,
    bvid: "BV1popup",
    title: "",
    up_name: "",
    expression: "",
    topic_label: "",
    presented: 0,
  });

  assert.equal(item.title, "这条标题还没对上号");
  assert.equal(item.up_name, "这位 UP 还没认出来");
  assert.equal(item.expression, "这条已经进了你的推荐区，点开看看。");
  assert.equal(item.topic_label, "");
  assert.equal(item.presented, false);
});

test("getPopupState distinguishes offline uninitialized refreshing empty and ready states", () => {
  assert.deepEqual(getPopupState({ online: false, items: [] }), {
    kind: "offline",
    message: "后端还没开张，先运行 openbiliclaw start",
    items: [],
  });

  assert.deepEqual(getPopupState({ online: true, items: [] }), {
    kind: "uninitialized",
    message: "还没完成初始化，先运行 openbiliclaw init",
    items: [],
  });

  assert.deepEqual(
    getPopupState({
      online: true,
      items: [],
      runtimeStatus: { initialized: true, pending_signal_events: 4 },
    }),
    {
      kind: "refreshing",
      message: "正在根据你最近的新行为补货，再刷一会儿就会更新。",
      items: [],
    },
  );

  assert.deepEqual(
    getPopupState({
      online: true,
      items: [],
      runtimeStatus: { initialized: true, pending_signal_events: 0 },
    }),
    {
      kind: "empty",
      message: "这会儿还没新东西，先运行 init、discover 或 recommend",
      items: [],
    },
  );

  const ready = getPopupState({
    online: true,
    items: [
      {
        id: 3,
        bvid: "BV1ready",
        title: "讲透城市叙事",
        up_name: "城市观察局",
        expression: "这条会对上你最近那股想把问题想透的劲头。",
        topic_label: "你最近那股想把问题想透的劲头",
        presented: true,
      },
    ],
    runtimeStatus: { initialized: true, recommendation_count: 1, unread_count: 1 },
  });

  assert.equal(ready.kind, "ready");
  assert.equal(ready.items.length, 1);
  assert.equal(ready.items[0]?.bvid, "BV1ready");
});

test("normalizeRuntimeStatus fills stable fallback fields", () => {
  assert.deepEqual(normalizeRuntimeStatus({ initialized: true, unread_count: "2" }), {
    initialized: true,
    recommendation_count: 0,
    pending_signal_events: 0,
    last_refresh_at: "",
    last_notification_at: "",
    unread_count: 2,
    manual_refresh_state: "idle",
    manual_refresh_message: "",
  });
});

test("buildFeedbackPayload builds like and dislike payloads", () => {
  assert.deepEqual(buildFeedbackPayload(7, "like"), {
    recommendation_id: 7,
    feedback_type: "like",
    note: "",
  });

  assert.deepEqual(buildFeedbackPayload(8, "dislike"), {
    recommendation_id: 8,
    feedback_type: "dislike",
    note: "",
  });
});

test("validateCommentInput requires non-empty note", () => {
  assert.deepEqual(validateCommentInput(""), {
    valid: false,
    message: "请先写一句你的想法。",
  });

  assert.deepEqual(validateCommentInput("  方向不错  "), {
    valid: true,
    message: "",
  });
});

test("buildFeedbackPayload trims comment note", () => {
  assert.deepEqual(buildFeedbackPayload(9, "comment", "  方向不错，但我想看更深一点。 "), {
    recommendation_id: 9,
    feedback_type: "comment",
    note: "方向不错，但我想看更深一点。",
  });
});

test("normalizeProfileSummary fills stable fallback fields", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: true,
      personality_portrait: "  喜欢深度分析  ",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
      recent_cognition_updates: ["  阿B 记住了你会吃深拆这一路。  "],
    }),
    {
      initialized: true,
      personality_portrait: "喜欢深度分析",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
      recent_cognition_updates: ["阿B 记住了你会吃深拆这一路。"],
    },
  );
});

test("normalizeProfileSummary keeps the newer low-roleplay fallback copy", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: false,
      personality_portrait: "",
      core_traits: [],
      deep_needs: [],
      top_interests: [],
    }),
    {
      initialized: false,
      personality_portrait: "画像还在慢慢攒，先多看一阵。",
      core_traits: [],
      deep_needs: [],
      top_interests: [],
      recent_cognition_updates: [],
    },
  );
});

test("getTabButtonState highlights current tab", () => {
  assert.deepEqual(getTabButtonState("recommend", "recommend"), {
    selected: true,
    tabIndex: 0,
  });

  assert.deepEqual(getTabButtonState("profile", "recommend"), {
    selected: false,
    tabIndex: -1,
  });
});

test("getConnectionBadgeState returns compact status copy for popup header", () => {
  assert.deepEqual(getConnectionBadgeState(true), {
    tone: "online",
    label: "已连接",
  });

  assert.deepEqual(getConnectionBadgeState(false), {
    tone: "offline",
    label: "未连接",
  });
});
