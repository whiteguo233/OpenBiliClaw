import test from "node:test";
import assert from "node:assert/strict";

import {
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildVideoUrl,
  getCommentSubmitUiState,
  getCognitionHistoryUiState,
  getConnectionBadgeState,
  getDisplayedPoolStatusSummary,
  getHintBannerState,
  getReadyRecommendationHint,
  getNextExpandedCognitionIndex,
  getRuntimeRefreshSubmissionState,
  getSubmissionProgressMessage,
  normalizeCognitionUpdateCard,
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
  validateCommentInput,
} from "../popup/popup-helpers.js";

test("buildVideoUrl builds bilibili video url from bvid", () => {
  assert.equal(
    buildVideoUrl("BV1xx411c7mD"),
    "https://www.bilibili.com/video/BV1xx411c7mD",
  );
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
    pool_available_count: 0,
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
      pool_target_count: 300,
      last_discovered_count: 0,
      last_replenished_count: 0,
      recent_pool_topics: [],
      manual_refresh_state: "running",
    }),
    {
      available: "还有 0 条可换",
      replenished: "正在补货",
      topics: "后台还在继续给你找新的",
    },
  );
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
      last_replenished_count: 6,
      recent_pool_topics: ["国际时事", "宏观经济"],
    },
  );

  assert.equal(merged.pool_available_count, 34);
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
      cognitive_style: ["  会先看结构  ", " 对证据敏感 "],
      motivational_drivers: ["  建立判断确定性  ", "  找到秩序感 "],
      current_phase: "  最近更像在一边吸收信息，一边整理判断。  ",
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
      disliked_topics: ["标题党", "太浅了"],
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
      cognitive_style: ["会先看结构", "对证据敏感"],
      motivational_drivers: ["建立判断确定性", "找到秩序感"],
      current_phase: "最近更像在一边吸收信息，一边整理判断。",
      deep_needs: ["理解世界"],
      top_interests: ["国际新闻", "商业案例"],
      disliked_topics: ["标题党", "太浅了"],
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

test("normalizeProfileSummary keeps the newer low-roleplay fallback copy", () => {
  assert.deepEqual(
    normalizeProfileSummary({
      initialized: false,
      personality_portrait: "",
      core_traits: [],
      cognitive_style: [],
      motivational_drivers: [],
      current_phase: "",
      deep_needs: [],
      top_interests: [],
    }),
    {
      initialized: false,
      personality_portrait: "画像还在慢慢攒，先多看一阵。",
      core_traits: [],
      cognitive_style: [],
      motivational_drivers: [],
      current_phase: "",
      deep_needs: [],
      top_interests: [],
      disliked_topics: [],
      recent_cognition_updates: [],
      has_more_cognition_updates: false,
      next_cognition_cursor: "",
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
