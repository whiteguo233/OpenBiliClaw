const DEFAULT_TITLE = "这条标题还没对上号";
const DEFAULT_UP_NAME = "这位 UP 还没认出来";
const DEFAULT_EXPRESSION = "这条已经进了你的推荐区，点开看看。";
const DEFAULT_PORTRAIT = "画像还在慢慢攒，先多看一阵。";

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeCoverUrl(value) {
  const text = normalizeText(value);
  if (!text) {
    return "";
  }
  if (text.startsWith("//")) {
    return `https:${text}`;
  }
  if (text.startsWith("http://")) {
    return `https://${text.slice("http://".length)}`;
  }
  return text;
}

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
}

export function getTabButtonState(activeTab, tabName) {
  return {
    selected: activeTab === tabName,
    tabIndex: activeTab === tabName ? 0 : -1,
  };
}

export function getConnectionBadgeState(online) {
  if (online) {
    return {
      tone: "online",
      label: "已连接",
    };
  }

  return {
    tone: "offline",
    label: "未连接",
  };
}

export function getHintBannerState(tone) {
  const normalized = normalizeText(tone);
  if (normalized === "success" || normalized === "error") {
    return { tone: normalized };
  }
  return { tone: "info" };
}

export function normalizeRecommendation(item) {
  const relevanceReason = normalizeText(item?.relevance_reason);
  return {
    id: Number(item?.id ?? 0),
    bvid: normalizeText(item?.bvid),
    title: normalizeText(item?.title) || DEFAULT_TITLE,
    up_name: normalizeText(item?.up_name) || DEFAULT_UP_NAME,
    cover_url: normalizeCoverUrl(item?.cover_url),
    expression: normalizeText(item?.expression) || relevanceReason || DEFAULT_EXPRESSION,
    topic_label: normalizeText(item?.topic_label),
    presented: Boolean(item?.presented),
  };
}

export function buildFeedbackPayload(recommendationId, feedbackType, note = "") {
  return {
    recommendation_id: Number(recommendationId),
    feedback_type: normalizeText(feedbackType),
    note: normalizeText(note),
  };
}

export function normalizeCognitionUpdateCard(item) {
  const fallbackContextLine = "基于最近几条相关内容";
  if (typeof item === "string") {
    return {
      summary: normalizeText(item),
      contextLine: fallbackContextLine,
      impact: "",
      reasoning: "",
      evidence: "",
      source: "",
      sourceLabel: "",
      expandHint: "summary_only",
      expandLabel: "仅结论",
      created_at: "",
      expandable: false,
    };
  }
  const impact = normalizeText(item?.impact);
  const reasoning = normalizeText(item?.reasoning);
  const evidence = normalizeText(item?.evidence);
  const expandHint = (() => {
    const explicitHint = normalizeText(item?.expand_hint);
    if (explicitHint === "expandable" || explicitHint === "summary_only") {
      return explicitHint;
    }
    return impact || reasoning || evidence ? "expandable" : "summary_only";
  })();
  return {
    summary: normalizeText(item?.summary),
    contextLine: normalizeText(item?.context_line) || fallbackContextLine,
    impact,
    reasoning,
    evidence,
    source: normalizeText(item?.source),
    sourceLabel: normalizeText(item?.source_label),
    expandHint,
    expandLabel: expandHint === "expandable" ? "展开" : "仅结论",
    created_at: normalizeText(item?.created_at),
    expandable: expandHint === "expandable",
  };
}

export function getNextExpandedCognitionIndex(currentIndex, clickedIndex) {
  return currentIndex === clickedIndex ? null : clickedIndex;
}

export function normalizeProfileSummary(summary) {
  return {
    initialized: Boolean(summary?.initialized),
    personality_portrait: normalizeText(summary?.personality_portrait) || DEFAULT_PORTRAIT,
    core_traits: Array.isArray(summary?.core_traits)
      ? summary.core_traits.map(normalizeText).filter(Boolean)
      : [],
    deep_needs: Array.isArray(summary?.deep_needs)
      ? summary.deep_needs.map(normalizeText).filter(Boolean)
      : [],
    top_interests: Array.isArray(summary?.top_interests)
      ? summary.top_interests.map(normalizeText).filter(Boolean)
      : [],
    disliked_topics: Array.isArray(summary?.disliked_topics)
      ? summary.disliked_topics.map(normalizeText).filter(Boolean)
      : [],
    recent_cognition_updates: Array.isArray(summary?.recent_cognition_updates)
      ? summary.recent_cognition_updates
          .map(normalizeCognitionUpdateCard)
          .filter((item) => item.summary)
      : [],
    has_more_cognition_updates: Boolean(summary?.has_more_cognition_updates),
    next_cognition_cursor: normalizeText(summary?.next_cognition_cursor),
  };
}

function normalizeCognitionHistoryItems(items) {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .map((item) => {
      if (item?.summary && Object.hasOwn(item, "expandable")) {
        return {
          summary: normalizeText(item.summary),
          contextLine: normalizeText(item.contextLine),
          impact: normalizeText(item.impact),
          reasoning: normalizeText(item.reasoning),
          evidence: normalizeText(item.evidence),
          source: normalizeText(item.source),
          sourceLabel: normalizeText(item.sourceLabel),
          expandHint: normalizeText(item.expandHint) || "summary_only",
          expandLabel: normalizeText(item.expandLabel) || "仅结论",
          created_at: normalizeText(item.created_at),
          expandable: Boolean(item.expandable),
        };
      }
      return normalizeCognitionUpdateCard(item);
    })
    .filter((item) => item.summary);
}

export function buildNextCognitionHistoryState(currentState, nextSummaryPage) {
  const existingItems = normalizeCognitionHistoryItems(
    Array.isArray(currentState?.items)
      ? currentState.items
      : currentState?.recent_cognition_updates,
  );
  const nextItems = normalizeCognitionHistoryItems(nextSummaryPage?.recent_cognition_updates);

  return {
    items: [...existingItems, ...nextItems],
    hasMore: Boolean(nextSummaryPage?.has_more_cognition_updates),
    nextCursor: normalizeText(nextSummaryPage?.next_cognition_cursor),
    loadingMore: false,
    loadMoreError: "",
  };
}

export function getCognitionHistoryUiState(historyState) {
  if (historyState?.loadingMore) {
    return {
      canLoadMore: false,
      loadingLabel: "正在加载更多变化…",
      actionLabel: "加载更多",
      statusMessage: "正在往下翻阿B 最近记下的变化。",
    };
  }

  if (normalizeText(historyState?.loadMoreError)) {
    return {
      canLoadMore: true,
      loadingLabel: "",
      actionLabel: "重试加载",
      statusMessage: "这段历史还没拉下来，可以再试一次。",
    };
  }

  if (!historyState?.hasMore) {
    return {
      canLoadMore: false,
      loadingLabel: "",
      actionLabel: "加载更多",
      statusMessage: "已经看到最近这段时间的变化了。",
    };
  }

  return {
    canLoadMore: true,
    loadingLabel: "",
    actionLabel: "加载更多",
    statusMessage: "",
  };
}

export function shouldFetchProfileSummary({ online, profileLoaded, force = false }) {
  if (!online) {
    return false;
  }
  if (force) {
    return true;
  }
  return !profileLoaded;
}

export function normalizeRuntimeStatus(status) {
  return {
    initialized: Boolean(status?.initialized),
    recommendation_count: Number(status?.recommendation_count ?? 0),
    pending_signal_events: Number(status?.pending_signal_events ?? 0),
    last_refresh_at: normalizeText(status?.last_refresh_at),
    last_notification_at: normalizeText(status?.last_notification_at),
    unread_count: Number(status?.unread_count ?? 0),
    pool_available_count: Number(status?.pool_available_count ?? 0),
    pool_target_count: Number(status?.pool_target_count ?? 0),
    last_replenished_count: Number(status?.last_replenished_count ?? 0),
    recent_pool_topics: Array.isArray(status?.recent_pool_topics)
      ? status.recent_pool_topics.map(normalizeText).filter(Boolean)
      : [],
    manual_refresh_state: normalizeText(status?.manual_refresh_state) || "idle",
    manual_refresh_message: normalizeText(status?.manual_refresh_message),
  };
}

export function mergeRuntimeStatusEvent(status, event) {
  const runtime = normalizeRuntimeStatus(status);
  const next = {
    ...runtime,
  };
  if (typeof event?.pool_available_count === "number") {
    next.pool_available_count = Number(event.pool_available_count);
  }
  if (typeof event?.last_replenished_count === "number") {
    next.last_replenished_count = Number(event.last_replenished_count);
  }
  if (Array.isArray(event?.recent_pool_topics)) {
    next.recent_pool_topics = event.recent_pool_topics.map(normalizeText).filter(Boolean);
  }
  return next;
}

export function getPoolStatusSummary(status) {
  const runtime = normalizeRuntimeStatus(status);
  if (!runtime.initialized) {
    return null;
  }
  const poolIsSufficient =
    runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;
  return {
    available: `当前池子里还有 ${runtime.pool_available_count} 条可换`,
    replenished:
      runtime.last_replenished_count > 0
        ? `刚补进 ${runtime.last_replenished_count} 条新的`
        : poolIsSufficient
          ? "这会儿先不补货，池子里已经够你换了"
          : "这轮还没补进新的",
    topics:
      runtime.recent_pool_topics.length > 0
        ? `最近在补：${runtime.recent_pool_topics.join(" / ")}`
        : poolIsSufficient
          ? "最近在补：先把这一池给你慢慢换开"
          : "最近在补：还在继续摸你的口味",
  };
}

export function getRealtimePoolStatusSummary(status, event = null) {
  const summary = getPoolStatusSummary(status);
  if (summary == null) {
    return null;
  }
  const message = normalizeText(event?.message);
  if (!message) {
    return summary;
  }
  return {
    ...summary,
    topics: `现在在忙：${message}`,
  };
}

export function validateCommentInput(note) {
  if (!normalizeText(note)) {
    return {
      valid: false,
      message: "请先写一句你的想法。",
    };
  }
  return {
    valid: true,
    message: "",
  };
}

export function getCommentSubmitUiState(state) {
  const normalized = normalizeText(state) || "idle";
  if (normalized === "submitting") {
    return {
      buttonLabel: "发送中...",
      disabled: true,
      statusMessage: "正在发出去，记一下你的这句。",
    };
  }
  if (normalized === "success") {
    return {
      buttonLabel: "已发出",
      disabled: true,
      statusMessage: "刚刚发出去了，会影响后面的推荐。",
    };
  }
  if (normalized === "error") {
    return {
      buttonLabel: "发出去",
      disabled: false,
      statusMessage: "这句还没发出去，可以再试一次。",
    };
  }
  return {
    buttonLabel: "发出去",
    disabled: false,
    statusMessage: "",
  };
}

export function shouldSubmitChatOnEnter(event) {
  return (
    event?.key === "Enter" &&
    !event?.shiftKey &&
    !event?.ctrlKey &&
    !event?.metaKey &&
    !event?.altKey &&
    !event?.isComposing
  );
}

export function getSubmissionProgressMessage(scope, stage) {
  const normalizedScope = normalizeText(scope);
  const normalizedStage = normalizeText(stage);

  if (normalizedScope === "chat") {
    if (normalizedStage === "waiting_reply") {
      return "消息已发出，正在等阿B回复。";
    }
    if (normalizedStage === "waiting_slow") {
      return "阿B 还在整理这句，可能在调用模型。";
    }
    if (normalizedStage === "refreshing_profile") {
      return "回复到了，正在同步画像。";
    }
    if (normalizedStage === "refreshing_activity") {
      return "画像已同步，正在刷新最近动态。";
    }
    if (normalizedStage === "success") {
      return "这句已经记下，界面也同步好了。";
    }
    if (normalizedStage === "error") {
      return "这句还没发出去，可以再试一次。";
    }
    return "";
  }

  if (normalizedScope === "feedback") {
    if (normalizedStage === "submitting") {
      return "正在提交反馈。";
    }
    if (normalizedStage === "accepted") {
      return "反馈已记下，后台正在更新画像和推荐。";
    }
    if (normalizedStage === "refreshing_profile") {
      return "反馈已记下，正在同步画像。";
    }
    if (normalizedStage === "refreshing_activity") {
      return "画像已同步，正在刷新最近动态。";
    }
    if (normalizedStage === "success") {
      return "这次反馈和界面都同步好了。";
    }
    if (normalizedStage === "error") {
      return "这条反馈没记上，可以再试一次。";
    }
  }

  return "";
}

export function getRuntimeRefreshSubmissionState(event) {
  const type = normalizeText(event?.type);
  const message = normalizeText(event?.message);

  if (type === "refresh.started" || type === "refresh.strategy") {
    return {
      done: false,
      message: message ? `后台正在处理：${message}` : "后台正在处理这次刷新。",
      tone: "info",
    };
  }

  if (type === "refresh.pool_updated") {
    return {
      done: true,
      message: message ? `推荐池已同步：${message}` : "推荐池已经同步好了。",
      tone: "success",
    };
  }

  if (type === "refresh.failed") {
    return {
      done: true,
      message: "反馈已记下，但后台补货这次没跑通。",
      tone: "error",
    };
  }

  return null;
}

export function normalizeActivityFeed(payload) {
  const items = Array.isArray(payload?.items)
    ? payload.items
        .filter((item) => item && typeof item === "object")
        .map((item, index) => ({
          id: normalizeText(item.id) || `activity-${index}`,
          kind: normalizeText(item.kind) || "activity",
          summary: normalizeText(item.summary),
          detail: normalizeText(item.detail),
          created_at: normalizeText(item.created_at),
          tone: getHintBannerState(item.tone).tone,
        }))
        .filter((item) => item.summary)
    : [];

  return {
    live_summary: normalizeText(payload?.live_summary),
    headline: normalizeText(payload?.headline),
    items,
  };
}

export function getActivityCardState({ feed = null, runtimeEvent = null, expanded = false }) {
  const normalizedFeed = normalizeActivityFeed(feed);
  const liveMessage = normalizeText(runtimeEvent?.message) || normalizedFeed.live_summary;
  const headline = normalizedFeed.headline || "最近还没新动静，先多刷一阵。";
  return {
    line1: liveMessage || "阿B 这会儿先替你盯着。",
    line2: headline,
    items: normalizedFeed.items,
    expanded: Boolean(expanded),
  };
}

export function getPopupState({ online, items = [], error = null, runtimeStatus = null }) {
  if (!online) {
    return {
      kind: "offline",
      message: "后端还没开张，先运行 openbiliclaw start",
      items: [],
    };
  }

  if (error) {
    return {
      kind: "error",
      message: "推荐暂时没刷出来，稍后再试",
      items: [],
    };
  }

  const normalizedItems = items.map(normalizeRecommendation);
  const runtime = normalizeRuntimeStatus(runtimeStatus);

  if (normalizedItems.length === 0) {
    if (!runtime.initialized) {
      return {
        kind: "uninitialized",
        message: "还没完成初始化，先运行 openbiliclaw init",
        items: [],
      };
    }

    if (runtime.manual_refresh_state === "running" || runtime.pending_signal_events > 0) {
      return {
        kind: "refreshing",
        message: runtime.manual_refresh_message || "正在根据你最近的新行为补货，再刷一会儿就会更新。",
        items: [],
      };
    }

    return {
      kind: "empty",
      message: "这会儿还没新东西，先运行 init、discover 或 recommend",
      items: [],
    };
  }

  return {
    kind: "ready",
    message: "",
    items: normalizedItems,
    runtime,
  };
}

export function getManualRefreshResultMessage(result, finalStatus = null) {
  if (result?.reason === "not_initialized") {
    return "先执行 openbiliclaw init，再回来刷新。";
  }

  if (finalStatus?.manual_refresh_state === "failed") {
    return finalStatus.manual_refresh_message || "这次补货没跑通，稍后再试。";
  }

  if (
    result?.reason === "already_running" ||
    finalStatus?.manual_refresh_state === "running"
  ) {
    return finalStatus?.manual_refresh_message || "已经在补货了，稍后会自动更新。";
  }

  if (
    result?.state === "running" ||
    finalStatus?.manual_refresh_state === "success"
  ) {
    return finalStatus?.manual_refresh_message || "刚给你补了一批新的。";
  }

  return "这次没接到补货任务，稍后再试。";
}
