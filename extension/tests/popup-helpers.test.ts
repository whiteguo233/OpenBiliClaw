import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  buildContentUrl,
  buildImageProxyPath,
  buildRecommendationClickPayload,
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildVideoUrl,
  getDelightUiState,
  formatRelativeTimestamp,
  getCommentSubmitUiState,
  getCognitionHistoryUiState,
  getConnectionBadgeState,
  getDisplayedPoolStatusSummary,
  getHintBannerState,
  getManualRefreshResultHint,
  getReadyRecommendationHint,
  getNextExpandedCognitionIndex,
  getRuntimeRefreshSubmissionState,
  getSubmissionProgressMessage,
  mergeDelightCandidate,
  normalizeCognitionUpdateCard,
  normalizeDelightCandidate,
  getRealtimePoolStatusSummary,
  getPoolStatusSummary,
  getPopupState,
  shouldSubmitChatOnEnter,
  getTabButtonState,
  mergeRuntimeStatusEvent,
  normalizeActivityFeed,
  normalizeRecommendation,
  normalizeProfileSummary,
  normalizeRuntimeStatus,
  shouldFetchProfileSummary,
  shouldAutoLoadRecommendations,
  validateCommentInput,
} from "../popup/popup-helpers.js";

test("buildVideoUrl builds bilibili video url from bvid", () => {
  assert.equal(
    buildVideoUrl("BV1xx411c7mD"),
    "https://www.bilibili.com/video/BV1xx411c7mD",
  );
});

test("buildContentUrl and click payload keep YouTube items source-aware", () => {
  const item = normalizeRecommendation({
    id: 42,
    bvid: "KPoJ7p9iy4Q",
    content_id: "KPoJ7p9iy4Q",
    title: "A YouTube deep dive",
    source_platform: "youtube",
  });
  const url = buildContentUrl(item);

  assert.equal(url, "https://www.youtube.com/watch?v=KPoJ7p9iy4Q");
  assert.deepEqual(buildRecommendationClickPayload(item, url), {
    bvid: "KPoJ7p9iy4Q",
    content_id: "KPoJ7p9iy4Q",
    content_url: "https://www.youtube.com/watch?v=KPoJ7p9iy4Q",
    source_platform: "youtube",
    title: "A YouTube deep dive",
    recommendation_id: 42,
    topic_label: "",
    up_name: "这位 UP 还没认出来",
  });
});

test("normalizeRecommendation keeps title and up-name fallbacks but leaves copy empty", () => {
  const item = normalizeRecommendation({
    id: 7,
    bvid: "BV1popup",
    title: "",
    up_name: "",
    cover_url: " https://i0.hdslb.com/bfs/archive/popup-cover.jpg ",
    expression: "",
    topic_label: "",
    presented: 0,
  });

  assert.equal(item.title, "这条标题还没对上号");
  assert.equal(item.up_name, "这位 UP 还没认出来");
  assert.equal(item.cover_url, "https://i0.hdslb.com/bfs/archive/popup-cover.jpg");
  assert.equal(item.expression, "");
  assert.equal(item.topic_label, "");
  assert.equal(item.presented, false);
});

test("normalizeProfileSummary keeps speculative avoidances", () => {
  const summary = normalizeProfileSummary({
    initialized: true,
    speculative_avoidances: [
      {
        domain: "浅层热点复读",
        reason: "信息密度低",
        source_mode: "negative_signal",
        status: "active",
        specifics: [{ name: "标题党热点解读" }],
      },
    ],
  });

  assert.equal(summary.speculative_avoidances[0].domain, "浅层热点复读");
  assert.equal(summary.speculative_avoidances[0].source_mode, "negative_signal");
  assert.equal(summary.speculative_avoidances[0].specifics[0].name, "标题党热点解读");
});

test("shouldAutoLoadRecommendations requires user scroll intent", () => {
  assert.equal(
    shouldAutoLoadRecommendations({
      activeTab: "recommend",
      loadingMore: false,
      hasMoreRecommendations: true,
      userArmed: false,
    }),
    false,
  );
  assert.equal(
    shouldAutoLoadRecommendations({
      activeTab: "recommend",
      loadingMore: false,
      hasMoreRecommendations: true,
      userArmed: true,
    }),
    true,
  );
  assert.equal(
    shouldAutoLoadRecommendations({
      activeTab: "profile",
      loadingMore: false,
      hasMoreRecommendations: true,
      userArmed: true,
    }),
    false,
  );
  assert.equal(
    shouldAutoLoadRecommendations({
      activeTab: "recommend",
      loadingMore: true,
      hasMoreRecommendations: true,
      userArmed: true,
    }),
    false,
  );
  assert.equal(
    shouldAutoLoadRecommendations({
      activeTab: "recommend",
      loadingMore: false,
      hasMoreRecommendations: false,
      userArmed: true,
    }),
    false,
  );
});

test("normalizeRecommendation keeps cover empty when missing", () => {
  const item = normalizeRecommendation({
    id: 9,
    bvid: "BV1nocover",
    title: "没有封面也要能展示",
    up_name: "阿B",
  });

  assert.equal(item.cover_url, "");
});

test("normalizeRecommendation upgrades protocol-relative and http covers to https", () => {
  const protocolRelative = normalizeRecommendation({
    id: 10,
    bvid: "BV1proto",
    title: "协议相对地址",
    up_name: "阿B",
    cover_url: "//i1.hdslb.com/bfs/archive/protocol.jpg",
  });
  const insecure = normalizeRecommendation({
    id: 11,
    bvid: "BV1http",
    title: "http 地址",
    up_name: "阿B",
    cover_url: "http://i2.hdslb.com/bfs/archive/insecure.jpg",
  });

  assert.equal(
    protocolRelative.cover_url,
    "https://i1.hdslb.com/bfs/archive/protocol.jpg",
  );
  assert.equal(
    insecure.cover_url,
    "https://i2.hdslb.com/bfs/archive/insecure.jpg",
  );
});

test("buildImageProxyPath returns encoded backend proxy path for valid cover urls", () => {
  assert.equal(
    buildImageProxyPath("https://i1.hdslb.com/bfs/archive/demo.jpg"),
    "/api/image-proxy?url=https%3A%2F%2Fi1.hdslb.com%2Fbfs%2Farchive%2Fdemo.jpg",
  );
  assert.equal(
    buildImageProxyPath("https://sns-webpic-qc.xhscdn.com/demo.jpg"),
    "/api/image-proxy?url=https%3A%2F%2Fsns-webpic-qc.xhscdn.com%2Fdemo.jpg",
  );
  assert.equal(buildImageProxyPath("not-a-url"), "");
});

test("popup image rendering uses backend proxy without referrerPolicy", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.doesNotMatch(popupJs, /referrerPolicy = "no-referrer"/);
  assert.match(popupJs, /setProxyImageSrc/);
  assert.doesNotMatch(popupJs, /image\.src = (item|delight)\.cover_url/);
});

test("normalizeRecommendation does not fall back to relevance_reason when expression is missing", () => {
  const item = normalizeRecommendation({
    id: 8,
    bvid: "BV1reason",
    title: "讲透链路",
    up_name: "观察站",
    expression: "",
    relevance_reason: "这条会对上你最近那股想把事情一步步理顺的劲头。",
    topic_label: "",
    presented: 0,
  });

  assert.equal(item.expression, "");
});

test("normalizeDelightCandidate fills stable fallbacks and upgrades cover urls", () => {
  const item = normalizeDelightCandidate({
    bvid: "BV1DELIGHT",
    title: "",
    delight_reason: "",
    delight_score: 0.72,
    delight_hook: "换个方向试试",
    cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
  });

  assert.deepEqual(item, {
    bvid: "BV1DELIGHT",
    title: "这条惊喜推荐还没起好标题",
    delight_reason: "这条可能会给你一点意外之喜。",
    delight_score: 0.72,
    delight_hook: "换个方向试试",
    cover_url: "https://i0.hdslb.com/bfs/archive/delight-cover.jpg",
    content_url: "",
    source_platform: "",
    state: "pending",
    response_message: "",
    chat_reply: "",
    turns: [],
    composer_open: false,
    chat_draft: "",
    chat_turn_id: "",
  });
});

test("mergeDelightCandidate keeps handled local state for the same bvid and ignores dismissed items", () => {
  const current = normalizeDelightCandidate({
    bvid: "BV1DELIGHT",
    title: "旧标题",
    delight_reason: "旧理由",
    state: "viewed",
    response_message: "已打开，阿B 会把这次点击当成强信号。",
  });

  const merged = mergeDelightCandidate(current, {
    bvid: "BV1DELIGHT",
    title: "新标题",
    delight_reason: "新理由",
    delight_score: 0.9,
  });
  const ignored = mergeDelightCandidate(current, {
    bvid: "BV1SNOOZED",
    title: "先别出现",
  }, ["BV1SNOOZED"]);

  assert.equal(merged.title, "新标题");
  assert.equal(merged.delight_reason, "新理由");
  assert.equal(merged.state, "viewed");
  assert.equal(merged.response_message, "已打开，阿B 会把这次点击当成强信号。");
  assert.equal(ignored, current);
});

test("getDelightUiState keeps handled delight visible with stable copy and highlight state", () => {
  const uiState = getDelightUiState(
    normalizeDelightCandidate({
      bvid: "BV1DELIGHT",
      title: "这条你会意外喜欢",
      delight_reason: "它不完全像你最近常看的，但入口很准。",
      delight_score: 0.88,
      state: "viewed",
    }),
    { highlightBvid: "BV1DELIGHT" },
  );

  assert.deepEqual(uiState, {
    visible: true,
    highlighted: true,
    handled: true,
    score_label: "大概率会戳中你",
    response_tone: "success",
    response_message: "已打开，阿B 会把这次点击当成强信号。",
  });
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

test("getPopupState does not show init prompt while refresh or pool signals are active", () => {
  assert.deepEqual(
    getPopupState({
      online: true,
      items: [],
      runtimeStatus: {
        initialized: false,
        manual_refresh_state: "running",
        manual_refresh_message: "正在初始化后的首轮补货。",
      },
    }),
    {
      kind: "refreshing",
      message: "正在初始化后的首轮补货。",
      items: [],
    },
  );

  assert.deepEqual(
    getPopupState({
      online: true,
      items: [],
      runtimeStatus: {
        initialized: false,
        recommendation_count: 0,
        pool_available_count: 12,
        last_replenished_count: 12,
      },
    }),
    {
      kind: "empty",
      message: "这会儿还没新东西，先运行 init、discover 或 recommend",
      items: [],
    },
  );
});

test("normalizeRuntimeStatus fills stable fallback fields", () => {
  assert.deepEqual(normalizeRuntimeStatus({ initialized: true, unread_count: "2" }), {
    initialized: true,
    recommendation_count: 0,
    pending_signal_events: 0,
    last_refresh_at: "",
    last_notification_at: "",
    unread_count: 2,
    pool_available_count: 0,
    pool_raw_count: 0,
    pool_pending_count: 0,
    pool_target_count: 0,
    last_discovered_count: 0,
    last_replenished_count: 0,
    recent_pool_topics: [],
    manual_refresh_state: "idle",
    manual_refresh_message: "",
  });
});

test("shouldFetchProfileSummary allows force refresh after profile is cached", () => {
  assert.equal(
    shouldFetchProfileSummary({ online: true, profileLoaded: true, force: false }),
    false,
  );
  assert.equal(
    shouldFetchProfileSummary({ online: true, profileLoaded: true, force: true }),
    true,
  );
  assert.equal(
    shouldFetchProfileSummary({ online: false, profileLoaded: false, force: true }),
    false,
  );
});

test("getPoolStatusSummary builds pool inventory copy", () => {
  assert.deepEqual(
    getPoolStatusSummary({
      initialized: true,
      pool_available_count: 28,
      pool_target_count: 30,
      last_replenished_count: 6,
      recent_pool_topics: ["国际时事", "宏观经济", "纪录片"],
    }),
    {
      available: "还有 28 条可换",
      replenished: "刚补进 6 条",
      topics: "国际时事 / 宏观经济 / 纪录片",
    },
  );
});

test("getPoolStatusSummary shows enough-stock copy when pool is already full", () => {
  assert.deepEqual(
    getPoolStatusSummary({
      initialized: true,
      pool_available_count: 155,
      pool_target_count: 150,
      last_replenished_count: 0,
      recent_pool_topics: [],
    }),
    {
      available: "还有 155 条可换",
      replenished: "这会儿先不补货",
      topics: "先把这一池给你慢慢换开",
    },
  );
});

test("getPoolStatusSummary prefers running copy over stale zero-replenishment copy", () => {
  assert.deepEqual(
    getPoolStatusSummary({
      initialized: true,
      pool_available_count: 0,
      pool_pending_count: 142,
      pool_target_count: 300,
      last_discovered_count: 0,
      last_replenished_count: 0,
      recent_pool_topics: [],
      manual_refresh_state: "running",
    }),
    {
      available: "找到 142 条素材，正在整理成可换内容",
      replenished: "正在整理",
      topics: "整理好就能换，不会把素材数当可换数",
    },
  );
});

test("getPoolStatusSummary never labels pending material as swappable", () => {
  const summary = getPoolStatusSummary({
    initialized: true,
    pool_available_count: 0,
    pool_pending_count: 142,
    pool_target_count: 300,
    manual_refresh_state: "idle",
  });

  assert(summary);
  assert.equal(summary.available.includes("142 条可换"), false);
  assert.equal(summary.available, "找到 142 条素材，正在整理成可换内容");
});

test("getPoolStatusSummary explains discovered-but-not-added refresh result", () => {
  assert.deepEqual(
    getPoolStatusSummary({
      initialized: true,
      pool_available_count: 0,
      pool_target_count: 300,
      last_discovered_count: 12,
      last_replenished_count: 0,
      recent_pool_topics: [],
      manual_refresh_state: "success",
    }),
    {
      available: "还有 0 条可换",
      replenished: "这轮找到了内容",
      topics: "但可立即换的库存还没变",
    },
  );
});

test("getReadyRecommendationHint prefers pool inventory over unread history", () => {
  assert.deepEqual(
    getReadyRecommendationHint({
      initialized: true,
      unread_count: 3195,
      pool_available_count: 28,
      manual_refresh_state: "idle",
      last_replenished_count: 6,
    }),
    {
      message: "这池里还有 28 条可换，想看就点，不想看就直说。",
      tone: "success",
    },
  );
});

test("getReadyRecommendationHint explains empty pool while refresh is still running", () => {
  assert.deepEqual(
    getReadyRecommendationHint({
      initialized: true,
      unread_count: 3195,
      pool_available_count: 0,
      manual_refresh_state: "running",
      last_replenished_count: 0,
    }),
    {
      message: "这池先翻到头了，后台还在继续补新的。",
      tone: "info",
    },
  );
});

test("getManualRefreshResultHint explains stale positive pool count after empty reshuffle", () => {
  assert.deepEqual(
    getManualRefreshResultHint({ itemCount: 0, hadAdvertisedInventory: true }),
    {
      message: "池子状态刚刚同步，正在整理内容。",
      tone: "info",
    },
  );
});

test("mergeRuntimeStatusEvent updates pool fields from runtime stream payload", () => {
  const merged = mergeRuntimeStatusEvent(
    {
      initialized: true,
      pool_available_count: 28,
      last_replenished_count: 0,
      recent_pool_topics: [],
    },
    {
      type: "refresh.pool_updated",
      message: "刚补进 6 条新的",
      pool_available_count: 34,
      pool_raw_count: 176,
      pool_pending_count: 142,
      last_replenished_count: 6,
      recent_pool_topics: ["国际时事", "宏观经济"],
    },
  );

  assert.equal(merged.pool_available_count, 34);
  assert.equal(merged.pool_raw_count, 176);
  assert.equal(merged.pool_pending_count, 142);
  assert.equal(merged.last_replenished_count, 6);
  assert.deepEqual(merged.recent_pool_topics, ["国际时事", "宏观经济"]);
});

test("getRealtimePoolStatusSummary prefers runtime stream message when available", () => {
  assert.deepEqual(
    getRealtimePoolStatusSummary(
      {
        initialized: true,
        pool_available_count: 34,
        last_replenished_count: 6,
        recent_pool_topics: ["国际时事", "宏观经济"],
      },
      {
        type: "refresh.strategy",
        message: "先从你刚刚的口味里搜一轮",
      },
    ),
    {
      available: "还有 34 条可换",
      replenished: "刚补进 6 条",
      topics: "先从你刚刚的口味里搜一轮",
    },
  );
});

test("getDisplayedPoolStatusSummary prefers active refresh message over cached runtime event", () => {
  assert.deepEqual(
    getDisplayedPoolStatusSummary(
      {
        initialized: true,
        pool_available_count: 34,
        last_replenished_count: 6,
        recent_pool_topics: ["国际时事", "宏观经济"],
      },
      {
        type: "refresh.strategy",
        message: "先从你刚刚的口味里搜一轮",
      },
      "正在给你换一批…",
    ),
    {
      available: "还有 34 条可换",
      replenished: "刚补进 6 条",
      topics: "正在给你换一批…",
    },
  );
});

test("normalizeActivityFeed keeps stable summaries and tones", () => {
  assert.deepEqual(
    normalizeActivityFeed({
      live_summary: "正在补候选",
      headline: "阿B 刚记下了你最近更吃深拆",
      items: [
        {
          id: "cog-1",
          kind: "cognition",
          summary: "阿B 刚记下了你最近更吃深拆",
          detail: "这会继续影响后面的推荐。",
          created_at: "2026-03-15T12:00:00+08:00",
          tone: "success",
        },
      ],
    }),
    {
      live_summary: "正在补候选",
      headline: "阿B 刚记下了你最近更吃深拆",
      items: [
        {
          id: "cog-1",
          kind: "cognition",
          summary: "阿B 刚记下了你最近更吃深拆",
          detail: "这会继续影响后面的推荐。",
          created_at: "2026-03-15T12:00:00+08:00",
          tone: "success",
        },
      ],
      has_more: false,
      next_cursor: "",
    },
  );
});

test("getActivityCardState prefers runtime event for line1 and feed headline for line2", () => {
  assert.deepEqual(
    getActivityCardState({
      feed: {
        live_summary: "阿B 先替你盯着。",
        headline: "阿B 刚记下了：你最近更吃因果链。",
        items: [
          {
            id: "cog-1",
            kind: "cognition",
            summary: "阿B 刚记下了：你最近更吃因果链。",
            detail: "",
            created_at: "2026-03-15T12:00:00+08:00",
            tone: "success",
          },
        ],
      },
      runtimeEvent: {
        type: "refresh.strategy",
        message: "正在补相关推荐候选",
      },
      expanded: true,
    }),
    {
      line1: "正在补相关推荐候选",
      line2: "阿B 刚记下了：你最近更吃因果链。",
      items: [
        {
          id: "cog-1",
          kind: "cognition",
          summary: "阿B 刚记下了：你最近更吃因果链。",
          detail: "",
          created_at: "2026-03-15T12:00:00+08:00",
          tone: "success",
        },
      ],
      expanded: true,
      has_more: false,
      next_cursor: "",
    },
  );
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

test("getCommentSubmitUiState exposes idle submitting success and error states", () => {
  assert.deepEqual(getCommentSubmitUiState("idle"), {
    buttonLabel: "发出去",
    disabled: false,
    statusMessage: "",
  });

  assert.deepEqual(getCommentSubmitUiState("submitting"), {
    buttonLabel: "发送中...",
    disabled: true,
    statusMessage: "正在发出去，记一下你的这句。",
  });

  assert.deepEqual(getCommentSubmitUiState("success"), {
    buttonLabel: "已发出",
    disabled: true,
    statusMessage: "刚刚发出去了，会影响后面的推荐。",
  });

  assert.deepEqual(getCommentSubmitUiState("error"), {
    buttonLabel: "发出去",
    disabled: false,
    statusMessage: "这句还没发出去，可以再试一次。",
  });
});

test("getSubmissionProgressMessage describes chat and feedback stages", () => {
  assert.equal(
    getSubmissionProgressMessage("chat", "waiting_reply"),
    "消息已发出，正在等阿B回复。",
  );
  assert.equal(
    getSubmissionProgressMessage("chat", "waiting_slow"),
    "阿B 还在整理这句，可能在调用模型。",
  );
  assert.equal(
    getSubmissionProgressMessage("feedback", "accepted"),
    "反馈已记下，后台正在更新画像和推荐。",
  );
  assert.equal(
    getSubmissionProgressMessage("feedback", "refreshing_activity"),
    "画像已同步，正在刷新最近动态。",
  );
});

test("getRuntimeRefreshSubmissionState maps runtime events to feedback progress", () => {
  assert.deepEqual(
    getRuntimeRefreshSubmissionState({
      type: "refresh.strategy",
      message: "先从你刚刚的口味里搜一轮",
    }),
    {
      done: false,
      message: "后台正在处理：先从你刚刚的口味里搜一轮",
      tone: "info",
    },
  );
  assert.deepEqual(
    getRuntimeRefreshSubmissionState({
      type: "refresh.pool_updated",
      message: "刚补进 6 条新的",
    }),
    {
      done: true,
      message: "推荐池已同步：刚补进 6 条新的",
      tone: "success",
    },
  );
  assert.deepEqual(
    getRuntimeRefreshSubmissionState({
      type: "refresh.failed",
      message: "这次补货没跑通",
    }),
    {
      done: true,
      message: "反馈已记下，但后台补货这次没跑通。",
      tone: "error",
    },
  );
});

test("shouldSubmitChatOnEnter only submits on plain Enter", () => {
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false }),
    true,
  );
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: true, ctrlKey: false, metaKey: false, altKey: false, isComposing: false }),
    false,
  );
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: true }),
    false,
  );
  assert.equal(
    shouldSubmitChatOnEnter({ key: "a", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, isComposing: false }),
    false,
  );
});

test("normalizeProfileSummary fills stable fallback fields", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: true,
      personality_portrait: "  喜欢深度分析  ",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      mbti: { type: "INTJ", dimensions: { E_I: { pole: "I", strength: 0.8 } }, confidence: 0.7 },
      values: ["独立思考", "  真实  "],
      motivational_drivers: ["  建立判断确定性  ", "  找到秩序感 "],
      likes: [{ domain: "  国际新闻  ", weight: 0.9, specifics: [{ name: "  中东局势  ", weight: 0.7 }] }],
      dislikes: [{ domain: "  标题党  ", weight: 0.8, specifics: [] }],
      favorite_up_users: ["  经济观察  ", "构图实验室"],
      life_stage: "  职业上升期  ",
      current_phase: "  最近更像在一边吸收信息，一边整理判断。  ",
      cognitive_style: ["  会先看结构  ", " 对证据敏感 "],
      style: { preferred_duration: "long", preferred_pace: "moderate", quality_sensitivity: 0.7, humor_preference: 0.3, depth_preference: 0.9 },
      context: { weekday_patterns: "  晚上深度阅读  ", weekend_patterns: "", time_of_day_patterns: "", session_type: "deep_dive" },
      exploration_openness: 0.72,
      speculative_interests: [
        { domain: "  建筑美学  ", reason: "  你最近的审美倾向  ", confidence: 0.4, confirmation_count: 1, confirmation_threshold: 3, status: "active", specifics: [{ name: "  现代主义建筑  ", confirmation_count: 1 }] },
      ],
      recent_cognition_updates: [
        {
          summary: "  阿B 记住了你会吃深拆这一路。  ",
          context_line: "  基于最近主题：国际新闻 / 商业案例  ",
          impact: "  画像里这条兴趣会更靠前。 ",
          reasoning: "  最近重复出现，不像一次随手点开。 ",
          evidence: "  最近连续点开深拆视频。 ",
          source: " chat ",
          source_label: " 聊天 ",
          expand_hint: " expandable ",
          created_at: " 2026-03-14T22:30:00 ",
        },
      ],
      has_more_cognition_updates: true,
      next_cognition_cursor: " 3 ",
    }),
    {
      initialized: true,
      personality_portrait: "喜欢深度分析",
      core_traits: ["理性", "好奇"],
      deep_needs: ["理解世界"],
      mbti: { type: "INTJ", dimensions: { E_I: { pole: "I", strength: 0.8 } }, confidence: 0.7 },
      values: ["独立思考", "真实"],
      motivational_drivers: ["建立判断确定性", "找到秩序感"],
      likes: [{ domain: "国际新闻", weight: 0.9, specifics: [{ name: "中东局势", weight: 0.7 }] }],
      dislikes: [{ domain: "标题党", weight: 0.8, specifics: [] }],
      favorite_up_users: ["经济观察", "构图实验室"],
      life_stage: "职业上升期",
      current_phase: "最近更像在一边吸收信息，一边整理判断。",
      cognitive_style: ["会先看结构", "对证据敏感"],
      style: { preferred_duration: "long", preferred_pace: "moderate", quality_sensitivity: 0.7, humor_preference: 0.3, depth_preference: 0.9 },
      context: { weekday_patterns: "晚上深度阅读", weekend_patterns: "", time_of_day_patterns: "", session_type: "deep_dive" },
      exploration_openness: 0.72,
      speculative_interests: [
        { domain: "建筑美学", reason: "你最近的审美倾向", confidence: 0.4, confirmation_count: 1, confirmation_threshold: 3, status: "active", specifics: [{ name: "现代主义建筑", confirmation_count: 1 }] },
      ],
      speculative_avoidances: [],
      recent_cognition_updates: [
        {
          summary: "阿B 记住了你会吃深拆这一路。",
          contextLine: "基于最近主题：国际新闻 / 商业案例",
          impact: "画像里这条兴趣会更靠前。",
          reasoning: "最近重复出现，不像一次随手点开。",
          evidence: "最近连续点开深拆视频。",
          source: "chat",
          sourceLabel: "聊天",
          expandHint: "expandable",
          expandLabel: "展开",
          created_at: "2026-03-14T22:30:00",
          expandable: true,
        },
      ],
      has_more_cognition_updates: true,
      next_cognition_cursor: "3",
      active_insights: [],
      recent_awareness: [],
    },
  );
});

test("normalizeCognitionUpdateCard falls back cleanly for legacy summary-only items", () => {
  assert.deepEqual(normalizeCognitionUpdateCard("  阿B 又对上了一点。  "), {
    summary: "阿B 又对上了一点。",
    contextLine: "基于最近几条相关内容",
    impact: "",
    reasoning: "",
    evidence: "",
    source: "",
    sourceLabel: "",
    expandHint: "summary_only",
    expandLabel: "仅结论",
    created_at: "",
    expandable: false,
  });

  assert.deepEqual(
    normalizeCognitionUpdateCard({
      summary: "  阿B 现在更确定你会吃地缘深拆这一口。 ",
      context_line: "  基于最近主题：地缘政治深拆  ",
      impact: " ",
      reasoning: "",
      evidence: " 最近连续点开相关内容。 ",
      source: " feedback ",
      source_label: " 推荐反馈 ",
    }),
    {
      summary: "阿B 现在更确定你会吃地缘深拆这一口。",
      contextLine: "基于最近主题：地缘政治深拆",
      impact: "",
      reasoning: "",
      evidence: "最近连续点开相关内容。",
      source: "feedback",
      sourceLabel: "推荐反馈",
      expandHint: "expandable",
      expandLabel: "展开",
      created_at: "",
      expandable: true,
    },
  );
});

test("getNextExpandedCognitionIndex toggles the same card and switches across cards", () => {
  assert.equal(getNextExpandedCognitionIndex(null, 0), 0);
  assert.equal(getNextExpandedCognitionIndex(0, 0), null);
  assert.equal(getNextExpandedCognitionIndex(0, 2), 2);
});

test("formatRelativeTimestamp returns Chinese relative labels across time buckets", () => {
  const now = Date.parse("2026-03-15T12:00:00Z");

  // Empty / invalid input
  assert.equal(formatRelativeTimestamp("", now), "");
  assert.equal(formatRelativeTimestamp("  ", now), "");
  assert.equal(formatRelativeTimestamp("not-a-date", now), "");

  // Under 1 minute → "刚刚"
  assert.equal(
    formatRelativeTimestamp("2026-03-15T11:59:30Z", now),
    "刚刚",
  );

  // Future timestamps also collapse to "刚刚"
  assert.equal(
    formatRelativeTimestamp("2026-03-15T12:01:00Z", now),
    "刚刚",
  );

  // Under 1 hour → "N 分钟前"
  assert.equal(
    formatRelativeTimestamp("2026-03-15T11:48:00Z", now),
    "12 分钟前",
  );

  // Under 1 day → "N 小时前"
  assert.equal(
    formatRelativeTimestamp("2026-03-15T09:00:00Z", now),
    "3 小时前",
  );

  // Under 1 week → "N 天前"
  assert.equal(
    formatRelativeTimestamp("2026-03-13T12:00:00Z", now),
    "2 天前",
  );

  // Older than a week → "MM-DD HH:mm"
  const older = formatRelativeTimestamp("2026-03-01T10:30:00Z", now);
  assert.match(older, /^\d{2}-\d{2} \d{2}:\d{2}$/);
});

test("normalizeProfileSummary keeps the newer low-roleplay fallback copy", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: false,
      personality_portrait: "",
      core_traits: [],
      deep_needs: [],
    }),
    {
      initialized: false,
      personality_portrait: "画像还在慢慢攒，先多看一阵。",
      core_traits: [],
      deep_needs: [],
      mbti: null,
      values: [],
      motivational_drivers: [],
      likes: [],
      dislikes: [],
      favorite_up_users: [],
      life_stage: "",
      current_phase: "",
      cognitive_style: [],
      style: null,
      context: null,
      exploration_openness: 0.5,
      speculative_interests: [],
      speculative_avoidances: [],
      recent_cognition_updates: [],
      has_more_cognition_updates: false,
      next_cognition_cursor: "",
      active_insights: [],
      recent_awareness: [],
    },
  );
});

test("buildNextCognitionHistoryState appends the next page and keeps pagination metadata", () => {
  const firstPage = normalizeProfileSummary({
    initialized: true,
    recent_cognition_updates: [
      {
        summary: "第一页第一条",
        context_line: "来自：《第一条内容》",
        source: "feedback",
        source_label: "推荐反馈",
        expand_hint: "summary_only",
      },
      {
        summary: "第一页第二条",
        context_line: "来自最近这轮聊天",
        source: "chat",
        source_label: "聊天",
        expand_hint: "summary_only",
      },
    ],
    has_more_cognition_updates: true,
    next_cognition_cursor: "2",
  });

  const secondPage = normalizeProfileSummary({
    initialized: true,
    recent_cognition_updates: [
      {
        summary: "第二页第一条",
        context_line: "基于最近主题：画像变化",
        impact: "画像继续变化",
        source_label: "聚合观察",
      },
    ],
    has_more_cognition_updates: false,
    next_cognition_cursor: "",
  });

  assert.deepEqual(buildNextCognitionHistoryState(firstPage, secondPage), {
    items: [
      {
        summary: "第一页第一条",
        contextLine: "来自：《第一条内容》",
        impact: "",
        reasoning: "",
        evidence: "",
        source: "feedback",
        sourceLabel: "推荐反馈",
        expandHint: "summary_only",
        expandLabel: "仅结论",
        created_at: "",
        expandable: false,
      },
      {
        summary: "第一页第二条",
        contextLine: "来自最近这轮聊天",
        impact: "",
        reasoning: "",
        evidence: "",
        source: "chat",
        sourceLabel: "聊天",
        expandHint: "summary_only",
        expandLabel: "仅结论",
        created_at: "",
        expandable: false,
      },
      {
        summary: "第二页第一条",
        contextLine: "基于最近主题：画像变化",
        impact: "画像继续变化",
        reasoning: "",
        evidence: "",
        source: "",
        sourceLabel: "聚合观察",
        expandHint: "expandable",
        expandLabel: "展开",
        created_at: "",
        expandable: true,
      },
    ],
    hasMore: false,
    nextCursor: "",
    loadingMore: false,
    loadMoreError: "",
  });
});

test("getCognitionHistoryUiState exposes loading, retry and completion copy", () => {
  assert.deepEqual(
    getCognitionHistoryUiState({
      items: [{ summary: "第一条", expandable: false }],
      hasMore: true,
      nextCursor: "1",
      loadingMore: true,
      loadMoreError: "",
    }),
    {
      canLoadMore: false,
      loadingLabel: "正在加载更多变化…",
      actionLabel: "加载更多",
      statusMessage: "正在往下翻阿B 最近记下的变化。",
    },
  );

  assert.deepEqual(
    getCognitionHistoryUiState({
      items: [{ summary: "第一条", expandable: false }],
      hasMore: true,
      nextCursor: "1",
      loadingMore: false,
      loadMoreError: "网络断了",
    }),
    {
      canLoadMore: true,
      loadingLabel: "",
      actionLabel: "重试加载",
      statusMessage: "这段历史还没拉下来，可以再试一次。",
    },
  );

  assert.deepEqual(
    getCognitionHistoryUiState({
      items: [{ summary: "第一条", expandable: false }],
      hasMore: false,
      nextCursor: "",
      loadingMore: false,
      loadMoreError: "",
    }),
    {
      canLoadMore: false,
      loadingLabel: "",
      actionLabel: "加载更多",
      statusMessage: "已经看到最近这段时间的变化了。",
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

test("getHintBannerState normalizes supported tones", () => {
  assert.deepEqual(getHintBannerState("success"), {
    tone: "success",
  });
  assert.deepEqual(getHintBannerState("error"), {
    tone: "error",
  });
  assert.deepEqual(getHintBannerState("weird"), {
    tone: "info",
  });
});
