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
    recent_cognition_updates: Array.isArray(summary?.recent_cognition_updates)
      ? summary.recent_cognition_updates.map(normalizeText).filter(Boolean)
      : [],
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
  return {
    available: `当前池子里还有 ${runtime.pool_available_count} 条可换`,
    replenished:
      runtime.last_replenished_count > 0
        ? `刚补进 ${runtime.last_replenished_count} 条新的`
        : "刚补进 0 条新的",
    topics:
      runtime.recent_pool_topics.length > 0
        ? `最近在补：${runtime.recent_pool_topics.join(" / ")}`
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
