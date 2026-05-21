const DEFAULT_TITLE = "这条标题还没对上号";
const DEFAULT_UP_NAME = "这位 UP 还没认出来";
const DEFAULT_PORTRAIT = "画像还在慢慢攒，先多看一阵。";
const DEFAULT_DELIGHT_TITLE = "这条惊喜推荐还没起好标题";
const DEFAULT_DELIGHT_REASON = "这条可能会给你一点意外之喜。";

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

export function buildImageProxyPath(value) {
  const src = normalizeCoverUrl(value);
  if (!src) {
    return "";
  }
  try {
    new URL(src);
  } catch {
    return "";
  }
  return `/api/image-proxy?url=${encodeURIComponent(src)}`;
}

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
}

export function buildContentUrl(item) {
  if (item?.content_url) return item.content_url;
  if (item?.bvid) return buildVideoUrl(item.bvid);
  return "";
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
  return {
    id: Number(item?.id ?? 0),
    bvid: normalizeText(item?.bvid),
    title: normalizeText(item?.title) || DEFAULT_TITLE,
    up_name: normalizeText(item?.up_name) || DEFAULT_UP_NAME,
    cover_url: normalizeCoverUrl(item?.cover_url),
    expression: normalizeText(item?.expression),
    topic_label: normalizeText(item?.topic_label),
    presented: Boolean(item?.presented),
    content_id: normalizeText(item?.content_id) || normalizeText(item?.bvid),
    content_url: normalizeText(item?.content_url) || "",
    source_platform: normalizeText(item?.source_platform) || "bilibili",
  };
}

export function normalizeDelightCandidate(item) {
  const normalizedState = normalizeText(item?.state) || "pending";
  return {
    bvid: normalizeText(item?.bvid),
    title: normalizeText(item?.title) || DEFAULT_DELIGHT_TITLE,
    delight_reason: normalizeText(item?.delight_reason) || DEFAULT_DELIGHT_REASON,
    delight_score: Number(item?.delight_score ?? 0),
    delight_hook: normalizeText(item?.delight_hook),
    cover_url: normalizeCoverUrl(item?.cover_url),
    state: normalizedState,
    response_message: normalizeText(item?.response_message),
    chat_reply: normalizeText(item?.chat_reply),
  };
}

export function mergeDelightCandidate(current, incoming, dismissedBvids = []) {
  const normalizedIncoming = normalizeDelightCandidate(incoming);
  if (!normalizedIncoming.bvid) {
    return current ?? null;
  }
  if (dismissedBvids.includes(normalizedIncoming.bvid)) {
    return current ?? null;
  }
  if (!current || normalizeText(current?.bvid) !== normalizedIncoming.bvid) {
    return normalizedIncoming;
  }
  return {
    ...normalizedIncoming,
    state: normalizeText(current?.state) || normalizedIncoming.state,
    response_message:
      normalizeText(current?.response_message) || normalizedIncoming.response_message,
    chat_reply: normalizeText(current?.chat_reply) || normalizedIncoming.chat_reply,
    composer_open: Boolean(current?.composer_open),
    chat_draft: normalizeText(current?.chat_draft),
  };
}

export function getDelightUiState(delight, { highlightBvid = "" } = {}) {
  const normalized = normalizeDelightCandidate(delight);
  if (!normalized.bvid) {
    return {
      visible: false,
      highlighted: false,
      handled: false,
      score_label: "",
      response_tone: "info",
      response_message: "",
    };
  }
  const score = normalized.delight_score;
  const scoreLabel =
    score >= 0.85 ? "大概率会戳中你" :
    score >= 0.65 ? "这条可能会拐到你" :
    "有点出其不意";
  const highlight = normalizeText(highlightBvid) === normalized.bvid;

  if (normalized.state === "viewed") {
    return {
      visible: true,
      highlighted: highlight,
      handled: true,
      score_label: scoreLabel,
      response_tone: "success",
      response_message:
        normalized.response_message || "已打开，阿B 会把这次点击当成强信号。",
    };
  }

  if (normalized.state === "rejected") {
    return {
      visible: true,
      highlighted: highlight,
      handled: true,
      score_label: scoreLabel,
      response_tone: "info",
      response_message:
        normalized.response_message || "记下了，这类惊喜先少来点。",
    };
  }

  if (normalized.state === "chatted") {
    return {
      visible: true,
      highlighted: highlight,
      handled: true,
      score_label: scoreLabel,
      response_tone: "info",
      response_message:
        normalized.response_message || "这句已经记下，后面会更会试探。",
    };
  }

  return {
    visible: true,
    highlighted: highlight,
    handled: false,
    score_label: scoreLabel,
    response_tone: "info",
    response_message: normalized.response_message,
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

/**
 * Format an ISO timestamp into a friendly relative label (Chinese).
 * @param {string} isoString - The timestamp to format.
 * @param {number} [now=Date.now()] - Current time, injectable for testing.
 * @returns {string} Relative label (e.g. "刚刚", "12 分钟前", "03-14 22:30") or "" if invalid.
 */
export function formatRelativeTimestamp(isoString, now = Date.now()) {
  const text = normalizeText(isoString);
  if (!text) {
    return "";
  }
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) {
    return "";
  }
  const diffMs = now - parsed;
  if (diffMs < 60_000) {
    return "刚刚";
  }
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 60) {
    return `${diffMin} 分钟前`;
  }
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) {
    return `${diffHour} 小时前`;
  }
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) {
    return `${diffDay} 天前`;
  }
  const date = new Date(parsed);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

function normalizeStrList(raw) {
  return Array.isArray(raw) ? raw.map(normalizeText).filter(Boolean) : [];
}

function normalizeMBTI(raw) {
  if (!raw || !raw.type) return null;
  const dims = {};
  if (raw.dimensions && typeof raw.dimensions === "object") {
    for (const [k, v] of Object.entries(raw.dimensions)) {
      dims[k] = { pole: normalizeText(v?.pole), strength: Number(v?.strength ?? 0.5) };
    }
  }
  return { type: normalizeText(raw.type), dimensions: dims, confidence: Number(raw.confidence ?? 0) };
}

function normalizeInterestDomains(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((d) => d?.domain)
    .map((d) => ({
      domain: normalizeText(d.domain),
      weight: Number(d.weight ?? 0.5),
      specifics: Array.isArray(d.specifics)
        ? d.specifics
            .filter((s) => s?.name)
            .map((s) => ({ name: normalizeText(s.name), weight: Number(s.weight ?? 0.5) }))
        : [],
    }));
}

function normalizeStyle(raw) {
  if (!raw) return null;
  return {
    preferred_duration: normalizeText(raw.preferred_duration),
    preferred_pace: normalizeText(raw.preferred_pace),
    quality_sensitivity: Number(raw.quality_sensitivity ?? 0.5),
    humor_preference: Number(raw.humor_preference ?? 0.5),
    depth_preference: Number(raw.depth_preference ?? 0.5),
  };
}

function normalizeContext(raw) {
  if (!raw) return null;
  return {
    weekday_patterns: normalizeText(raw.weekday_patterns),
    weekend_patterns: normalizeText(raw.weekend_patterns),
    time_of_day_patterns: normalizeText(raw.time_of_day_patterns),
    session_type: normalizeText(raw.session_type),
  };
}

export function normalizeProfileSummary(summary) {
  return {
    initialized: Boolean(summary?.initialized),
    personality_portrait: normalizeText(summary?.personality_portrait) || DEFAULT_PORTRAIT,
    // Core
    core_traits: normalizeStrList(summary?.core_traits),
    deep_needs: normalizeStrList(summary?.deep_needs),
    mbti: normalizeMBTI(summary?.mbti),
    // Values
    values: normalizeStrList(summary?.values),
    motivational_drivers: normalizeStrList(summary?.motivational_drivers),
    // Interest
    likes: normalizeInterestDomains(summary?.likes),
    dislikes: normalizeInterestDomains(summary?.dislikes),
    favorite_up_users: normalizeStrList(summary?.favorite_up_users),
    // Role
    life_stage: normalizeText(summary?.life_stage),
    current_phase: normalizeText(summary?.current_phase),
    // Surface
    cognitive_style: normalizeStrList(summary?.cognitive_style),
    style: normalizeStyle(summary?.style),
    context: normalizeContext(summary?.context),
    exploration_openness: typeof summary?.exploration_openness === "number"
      ? Math.max(0, Math.min(1, summary.exploration_openness))
      : 0.5,
    // Cross-cutting
    speculative_interests: Array.isArray(summary?.speculative_interests)
      ? summary.speculative_interests
          .filter((item) => item?.domain)
          .map((item) => ({
            domain: normalizeText(item.domain),
            reason: normalizeText(item.reason),
            confidence: Number(item.confidence ?? 0),
            confirmation_count: Number(item.confirmation_count ?? 0),
            confirmation_threshold: Number(item.confirmation_threshold ?? 3),
            status: normalizeText(item.status) || "active",
            specifics: Array.isArray(item.specifics)
              ? item.specifics
                  .filter((s) => s?.name)
                  .map((s) => ({
                    name: normalizeText(s.name),
                    confirmation_count: Number(s.confirmation_count ?? 0),
                  }))
              : [],
          }))
      : [],
    recent_cognition_updates: Array.isArray(summary?.recent_cognition_updates)
      ? summary.recent_cognition_updates
          .map(normalizeCognitionUpdateCard)
          .filter((item) => item.summary)
      : [],
    has_more_cognition_updates: Boolean(summary?.has_more_cognition_updates),
    next_cognition_cursor: normalizeText(summary?.next_cognition_cursor),
    active_insights: Array.isArray(summary?.active_insights)
      ? summary.active_insights
          .filter((item) => item?.hypothesis)
          .map((item) => ({
            hypothesis: normalizeText(item.hypothesis),
            evidence: Array.isArray(item.evidence)
              ? item.evidence.map((e) => normalizeText(e)).filter(Boolean)
              : [],
            confidence: typeof item.confidence === "number"
              ? Math.max(0, Math.min(1, item.confidence))
              : 0.5,
            validated: Boolean(item.validated),
            created_at: normalizeText(item.created_at),
          }))
      : [],
    recent_awareness: Array.isArray(summary?.recent_awareness)
      ? summary.recent_awareness
          .filter((item) => item?.observation)
          .map((item) => ({
            date: normalizeText(item.date),
            observation: normalizeText(item.observation),
            trend: normalizeText(item.trend),
            emotion_guess: normalizeText(item.emotion_guess),
          }))
      : [],
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
    last_discovered_count: Number(status?.last_discovered_count ?? 0),
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
  if (typeof event?.last_discovered_count === "number") {
    next.last_discovered_count = Number(event.last_discovered_count);
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
  if (runtime.manual_refresh_state === "running") {
    // v0.3.18+ extension: when pool already has servable items, don't
    // emphasise "正在补货" — that previously misled users on slow B站
    // discovery rounds (v_voucher storms keep refresh "running" for
    // many minutes even though pool is full). User can already swap
    // right now; the background top-up is decorative.
    if (runtime.pool_available_count > 0) {
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: "后台继续在找更多",
        topics: "可以先换一批,新的随时进",
      };
    }
    return {
      available: `还有 ${runtime.pool_available_count} 条可换`,
      replenished: "正在补货",
      topics: "后台还在继续给你找新的",
    };
  }
  return {
    available: `还有 ${runtime.pool_available_count} 条可换`,
    replenished:
      runtime.last_replenished_count > 0
        ? `刚补进 ${runtime.last_replenished_count} 条`
        : runtime.last_discovered_count > 0
          ? "这轮找到了内容"
        : poolIsSufficient
          ? "这会儿先不补货"
          : "这轮还没补进",
    topics:
      runtime.recent_pool_topics.length > 0
        ? runtime.recent_pool_topics.join(" / ")
        : runtime.last_discovered_count > 0
          ? "但可立即换的库存还没变"
        : poolIsSufficient
          ? "先把这一池给你慢慢换开"
          : "还在继续摸你的口味",
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
    topics: message,
  };
}

export function getDisplayedPoolStatusSummary(status, event = null, refreshMessage = "") {
  const summary = getPoolStatusSummary(status);
  if (summary == null) {
    return null;
  }
  const activeMessage = normalizeText(refreshMessage) || normalizeText(event?.message);
  if (!activeMessage) {
    return summary;
  }
  return {
    ...summary,
    topics: activeMessage,
  };
}

export function getReadyRecommendationHint(status) {
  const runtime = normalizeRuntimeStatus(status);
  if (runtime.pool_available_count > 0) {
    return {
      message: `这池里还有 ${runtime.pool_available_count} 条可换，想看就点，不想看就直说。`,
      tone: runtime.last_replenished_count > 0 ? "success" : "info",
    };
  }
  if (runtime.manual_refresh_state === "running") {
    return {
      message: "这池先翻到头了，后台还在继续补新的。",
      tone: "info",
    };
  }
  return {
    message: "这池先翻到头了，等后台再补点新的。",
    tone: "info",
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
    has_more: Boolean(payload?.has_more),
    next_cursor: normalizeText(payload?.next_cursor),
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
    has_more: Boolean(normalizedFeed.has_more),
    next_cursor: normalizedFeed.next_cursor || "",
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
  const refreshInProgress =
    runtime.manual_refresh_state === "running" || runtime.pending_signal_events > 0;
  const hasPostInitRuntimeSignals =
    runtime.recommendation_count > 0 ||
    runtime.pool_available_count > 0 ||
    runtime.last_replenished_count > 0 ||
    runtime.last_discovered_count > 0;

  if (normalizedItems.length === 0) {
    if (refreshInProgress) {
      return {
        kind: "refreshing",
        message: runtime.manual_refresh_message || "正在根据你最近的新行为补货，再刷一会儿就会更新。",
        items: [],
      };
    }

    if (!runtime.initialized && !hasPostInitRuntimeSignals) {
      return {
        kind: "uninitialized",
        message: "还没完成初始化，先运行 openbiliclaw init",
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
