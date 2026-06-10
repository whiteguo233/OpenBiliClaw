/**
 * Pure view-model normalization helpers for mobile web views.
 *
 * Ported from extension/popup/popup-helpers.js where semantics matter,
 * adapted for mobile state model. No DOM, no fetch, no side effects.
 */

// ── Text / Number Primitives ─────────────────────────────────

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeStrList(raw) {
  return Array.isArray(raw) ? raw.map(normalizeText).filter(Boolean) : [];
}

function coerceNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function clamp01(value, fallback = 0.5) {
  const n = coerceNumber(value);
  if (n === null) return fallback;
  return Math.max(0, Math.min(1, n));
}

function round3(value) {
  return Math.round(value * 1000) / 1000;
}

// ── Defaults ─────────────────────────────────────────────────

const DEFAULT_TITLE = "这条标题还没对上号";
const DEFAULT_UP_NAME = "这位 UP 还没认出来";
const DEFAULT_PORTRAIT = "画像还在慢慢攒，先多看一阵。";
const DEFAULT_DELIGHT_TITLE = "这条惊喜推荐还没起好标题";
const DEFAULT_DELIGHT_REASON = "这条可能会给你一点意外之喜。";

// ── Cover URL ────────────────────────────────────────────────

export function normalizeCoverUrl(value) {
  const text = normalizeText(value);
  if (!text) return "";
  let url = text;
  if (url.startsWith("//")) {
    url = `https:${url}`;
  } else if (url.startsWith("http://")) {
    url = `https://${url.slice("http://".length)}`;
  }
  try {
    new URL(url);
  } catch {
    return "";
  }
  return url;
}

export function getCoverImageAttrs(value) {
  const src = normalizeCoverUrl(value);
  if (!src) return null;
  return { src: `/api/image-proxy?url=${encodeURIComponent(src)}` };
}

export function getRecommendationCoverPreloadUrls(items, { start = 0, limit = 12 } = {}) {
  const safeStart = Math.max(0, Math.trunc(coerceNumber(start) ?? 0));
  const safeLimit = Math.max(0, Math.trunc(coerceNumber(limit) ?? 0));
  if (!Array.isArray(items) || safeLimit <= 0) return [];

  const seen = new Set();
  const urls = [];
  for (const item of items.slice(safeStart)) {
    const attrs = getCoverImageAttrs(item?.cover_url ?? item);
    if (!attrs || seen.has(attrs.src)) continue;
    seen.add(attrs.src);
    urls.push(attrs.src);
    if (urls.length >= safeLimit) break;
  }
  return urls;
}

export function getRecommendationImageLoadingAttrs(
  index,
  { eagerCount = Infinity, highPriorityCount = 2 } = {},
) {
  // Default eagerCount is Infinity: every cover loads eagerly so a card slid into
  // view on scroll never shows the white placeholder while a native lazy <img>
  // defers its fetch. Pass a finite eagerCount to opt back into a lazy tail.
  const safeIndex = Math.max(0, Math.trunc(coerceNumber(index) ?? 0));
  const eagerRaw = coerceNumber(eagerCount);
  const safeEagerCount = eagerRaw === null ? Infinity : Math.max(0, Math.trunc(eagerRaw));
  const safeHighPriorityCount = Math.max(0, Math.trunc(coerceNumber(highPriorityCount) ?? 0));
  if (safeIndex < safeEagerCount) {
    return {
      loading: "eager",
      fetchPriority: safeIndex < safeHighPriorityCount ? "high" : "auto",
    };
  }
  return { loading: "lazy", fetchPriority: "auto" };
}

export function shouldAutoAppendRecommendations({
  loading = false,
  autoAppendExhausted = false,
  activeTab = "recommend",
  userArmed = false,
} = {}) {
  return Boolean(
    userArmed &&
      !loading &&
      !autoAppendExhausted &&
      activeTab === "recommend",
  );
}

// ── Source Platform ──────────────────────────────────────────

const SOURCE_LABEL_MAP = {
  bilibili: "Bilibili",
  xiaohongshu: "Xiaohongshu",
  douyin: "Douyin",
  youtube: "YouTube",
  web: "Web",
};

const RUNTIME_TOPIC_LABEL_MAP = {
  search: "站内搜索",
  related_chain: "相关推荐",
  trending: "站内热榜",
  explore: "探索补池",
  "xhs-extension-task": "小红书任务",
  "xhs-extension-search": "小红书搜索",
  "xhs-extension-profile": "小红书画像",
  "xhs-extension-explore": "小红书探索",
  "dy-plugin-search": "抖音搜索",
  "dy-plugin-hot-related": "抖音热点",
  "dy-plugin-feed": "抖音推荐流",
  "douyin-search": "抖音搜索",
  "douyin-hot": "抖音热点",
  "douyin-feed": "抖音推荐流",
  yt_search: "YouTube 搜索",
  yt_trending: "YouTube 热榜",
  yt_channel: "YouTube 频道",
  youtube_search: "YouTube 搜索",
  youtube_trending: "YouTube 热榜",
  youtube_channel: "YouTube 频道",
};

export function normalizeSourcePlatform(item) {
  const explicit = normalizeText(item?.source_platform);
  if (explicit && SOURCE_LABEL_MAP[explicit]) return explicit;
  const url = normalizeText(item?.content_url);
  if (url) {
    if (url.includes("bilibili.com") || url.includes("b23.tv")) return "bilibili";
    if (url.includes("xiaohongshu.com") || url.includes("xhslink.com")) return "xiaohongshu";
    if (url.includes("douyin.com")) return "douyin";
    if (url.includes("youtube.com") || url.includes("youtu.be")) return "youtube";
    return "web";
  }
  if (normalizeText(item?.bvid)) return "bilibili";
  return explicit || "bilibili";
}

export function getSourceLabel(source) {
  return SOURCE_LABEL_MAP[source] || source || "Web";
}

function formatRuntimeTopicLabel(value) {
  const text = normalizeText(value);
  if (!text) return "";
  const key = text.toLowerCase();
  if (RUNTIME_TOPIC_LABEL_MAP[key]) return RUNTIME_TOPIC_LABEL_MAP[key];
  if (key.startsWith("xhs-extension-")) return "小红书";
  if (key.startsWith("dy-plugin-") || key.startsWith("douyin-")) return "抖音";
  if (key.startsWith("yt-") || key.startsWith("youtube-")) return "YouTube";
  return text;
}

function formatRuntimeTopicList(topics) {
  const seen = new Set();
  const labels = [];
  for (const topic of Array.isArray(topics) ? topics : []) {
    const label = formatRuntimeTopicLabel(topic);
    if (!label || seen.has(label)) continue;
    seen.add(label);
    labels.push(label);
  }
  return labels.slice(0, 3).join(" / ");
}

function formatCompactRuntimeTopicList(topics) {
  const full = formatRuntimeTopicList(topics);
  return full
    .replace(/小红书任务 \/ 小红书探索/g, "小红书任务 / 探索")
    .replace(/小红书搜索 \/ 小红书探索/g, "小红书搜索 / 探索")
    .replace(/抖音搜索 \/ 抖音热点/g, "抖音搜索 / 热点")
    .replace(/YouTube 搜索 \/ YouTube 热榜/g, "YouTube 搜索 / 热榜");
}

// ── URL Builders ─────────────────────────────────────────────

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
}

export function buildYouTubeUrl(videoId) {
  return `https://www.youtube.com/watch?v=${normalizeText(videoId)}`;
}

export function buildContentUrl(item) {
  if (item?.content_url) return item.content_url;
  const platform = normalizeText(item?.source_platform);
  const vid = normalizeText(item?.content_id || item?.bvid);
  if (!vid) return "";
  if (platform === "youtube") return buildYouTubeUrl(vid);
  return buildVideoUrl(vid);
}

export function buildRecommendationClickPayload(item, contentUrl = "") {
  const bvid = normalizeText(item?.bvid || item?.content_id);
  const contentId = normalizeText(item?.content_id || item?.bvid);
  return {
    bvid,
    content_id: contentId,
    content_url: normalizeText(contentUrl) || normalizeText(item?.content_url),
    source_platform: normalizeText(item?.source_platform) || "bilibili",
    title: normalizeText(item?.title),
    recommendation_id: typeof item?.id === "number" ? item.id : null,
    topic_label: normalizeText(item?.topic_label),
    up_name: normalizeText(item?.up_name),
  };
}

// ── Recommendation Normalization ─────────────────────────────

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
    source_platform: normalizeSourcePlatform(item),
    content_type: normalizeText(item?.content_type) || "video",
    body_text: normalizeText(item?.body_text),
  };
}

const TEXT_CARD_CONTENT_TYPES = new Set(["tweet", "thread"]);

// Decide the media slot for a recommendation card. Text-first sources
// (X tweet/thread) ship no cover image — render a no-cover text card from
// body_text/title instead of an <img>, so the web UI never paints a
// broken-image node.
export function getRecommendationCardKind(item) {
  const contentType = normalizeText(item?.content_type).toLowerCase();
  const coverUrl = normalizeCoverUrl(item?.cover_url);
  const isText = TEXT_CARD_CONTENT_TYPES.has(contentType) || !coverUrl;
  if (isText) {
    return {
      kind: "text",
      coverUrl: "",
      text: normalizeText(item?.body_text) || normalizeText(item?.title),
    };
  }
  return { kind: "cover", coverUrl, text: "" };
}

// ── Feedback ─────────────────────────────────────────────────

export function buildFeedbackPayload(recommendationId, feedbackType, note = "") {
  return {
    recommendation_id: Number(recommendationId),
    feedback_type: normalizeText(feedbackType),
    note: normalizeText(note),
  };
}

export function validateCommentInput(note) {
  if (!normalizeText(note)) {
    return { valid: false, message: "请先写一句你的想法。" };
  }
  return { valid: true, message: "" };
}

export function getCommentSubmitUiState(state) {
  const normalized = normalizeText(state) || "idle";
  if (normalized === "submitting") {
    return { buttonLabel: "发送中...", disabled: true, statusMessage: "正在发出去，记一下你的这句。" };
  }
  if (normalized === "success") {
    return { buttonLabel: "已发出", disabled: true, statusMessage: "刚刚发出去了，会影响后面的推荐。" };
  }
  if (normalized === "error") {
    return { buttonLabel: "发出去", disabled: false, statusMessage: "这句还没发出去，可以再试一次。" };
  }
  return { buttonLabel: "发出去", disabled: false, statusMessage: "" };
}

// ── Delight ──────────────────────────────────────────────────

export function normalizeDelightCandidate(item) {
  return {
    bvid: normalizeText(item?.bvid),
    title: normalizeText(item?.title) || DEFAULT_DELIGHT_TITLE,
    delight_reason: normalizeText(item?.delight_reason) || DEFAULT_DELIGHT_REASON,
    delight_score: Number(item?.delight_score ?? 0),
    delight_hook: normalizeText(item?.delight_hook),
    cover_url: normalizeCoverUrl(item?.cover_url),
    content_url: normalizeText(item?.content_url),
    source_platform: normalizeSourcePlatform(item),
    state: normalizeText(item?.state) || "pending",
    response_message: normalizeText(item?.response_message),
    chat_reply: normalizeText(item?.chat_reply),
    // Local UI fields preserved across re-normalizations
    turns: Array.isArray(item?.turns) ? item.turns : [],
    composer_open: Boolean(item?.composer_open),
    draft: normalizeText(item?.draft),
    chat_turn_id: normalizeText(item?.chat_turn_id),
  };
}

export function getDelightUiState(delight, { highlightBvid = "" } = {}) {
  const normalized = normalizeDelightCandidate(delight);
  if (!normalized.bvid) {
    return {
      visible: false, highlighted: false, handled: false,
      score_label: "", response_tone: "info", response_message: "",
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
      visible: true, highlighted: highlight, handled: true,
      score_label: scoreLabel, response_tone: "success",
      response_message: normalized.response_message || "已打开，阿B 会把这次点击当成强信号。",
    };
  }
  if (normalized.state === "liked") {
    return {
      visible: true, highlighted: highlight, handled: true,
      score_label: scoreLabel, response_tone: "success",
      response_message: normalized.response_message || "好，这类多来点。",
    };
  }
  if (normalized.state === "rejected") {
    return {
      visible: true, highlighted: highlight, handled: true,
      score_label: scoreLabel, response_tone: "info",
      response_message: normalized.response_message || "记下了，这类惊喜先少来点。",
    };
  }
  if (normalized.state === "chatted" || normalized.state === "chatting") {
    return {
      visible: true, highlighted: highlight, handled: false,
      score_label: scoreLabel, response_tone: "info",
      response_message: normalized.response_message || "这句已经记下，后面会更会试探。",
    };
  }
  return {
    visible: true, highlighted: highlight, handled: false,
    score_label: scoreLabel, response_tone: "info",
    response_message: normalized.response_message,
  };
}

/**
 * Map a UI action string to the backend API token and local UI state.
 * CRITICAL: Never send UI state strings (viewed/rejected/chatted) to /api/delight/respond.
 */
export function getDelightActionState(action) {
  switch (action) {
    case "view":
      return { apiResponse: "view", uiState: "viewed", permanent: false };
    case "like":
      return { apiResponse: "like", uiState: "liked", permanent: false };
    case "reject":
      return { apiResponse: "dislike", uiState: "rejected", permanent: true };
    case "chat":
      return { apiResponse: null, uiState: "chatting", permanent: false };
    default:
      return { apiResponse: null, uiState: "pending", permanent: false };
  }
}

export function getDelightMessageActions() {
  return [
    { label: "看看", action: "view", primary: true },
    { label: "喜欢", action: "like", primary: false },
    { label: "不感兴趣", action: "reject", primary: false },
    { label: "聊一聊", action: "chat", primary: false },
  ];
}

export function getProbeMessageActions() {
  return [
    { label: "喜欢", action: "confirm", primary: true },
    { label: "不喜欢", action: "reject", primary: false },
    { label: "多聊聊", action: "chat", primary: false },
  ];
}

export function getAvoidanceProbeMessageActions() {
  return [
    { label: "确实不喜欢", action: "confirm", primary: true },
    { label: "不是", action: "reject", primary: false },
    { label: "多聊聊", action: "chat", primary: false },
  ];
}

// ── Pool Status (simple — backward compat) ───────────────────

export function normalizePoolStatus(status) {
  const topics = Array.isArray(status?.recent_pool_topics)
    ? status.recent_pool_topics
    : (Array.isArray(status?.pool?.topics) ? status.pool.topics : []);
  return {
    pool_size:
      coerceNumber(status?.pool_available_count)
      ?? coerceNumber(status?.pool_size)
      ?? coerceNumber(status?.pool?.total),
    recent_replenish:
      coerceNumber(status?.last_replenished_count)
      ?? coerceNumber(status?.recent_replenish)
      ?? coerceNumber(status?.last_refresh_added),
    current_topic:
      normalizeText(topics[0])
      || normalizeText(status?.current_topic)
      || normalizeText(status?.pool?.topics?.[0])
      || null,
  };
}

// ── Runtime Status ───────────────────────────────────────────

export function normalizeRuntimeStatus(status) {
  return {
    initialized: Boolean(status?.initialized),
    recommendation_count: Number(status?.recommendation_count ?? 0),
    pending_signal_events: Number(status?.pending_signal_events ?? 0),
    last_refresh_at: normalizeText(status?.last_refresh_at),
    last_notification_at: normalizeText(status?.last_notification_at),
    unread_count: Number(status?.unread_count ?? 0),
    pool_available_count: Number(status?.pool_available_count ?? 0),
    pool_raw_count: Number(status?.pool_raw_count ?? 0),
    pool_pending_count: Number(status?.pool_pending_count ?? 0),
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
  const next = { ...runtime };
  if (typeof event?.pool_available_count === "number") {
    next.pool_available_count = Number(event.pool_available_count);
  }
  if (typeof event?.pool_raw_count === "number") {
    next.pool_raw_count = Number(event.pool_raw_count);
  }
  if (typeof event?.pool_pending_count === "number") {
    next.pool_pending_count = Number(event.pool_pending_count);
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

// ── Pool Status (semantic — primary for mobile) ──────────────

export function getPoolStatusSummary(status) {
  const runtime = normalizeRuntimeStatus(status);
  if (!runtime.initialized) return null;

  const poolIsSufficient =
    runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;

  if (runtime.manual_refresh_state === "running") {
    if (runtime.pool_available_count > 0) {
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: "后台继续在找更多",
        topics: "可以先换一批,新的随时进",
      };
    }
    if (runtime.pool_pending_count > 0) {
      return {
        available: `找到 ${runtime.pool_pending_count} 条素材，正在整理成可换内容`,
        replenished: "正在整理",
        topics: "整理好就能换，不会把素材数当可换数",
      };
    }
    return {
      available: "暂无可换库存",
      replenished: "正在补货",
      topics: "后台还在继续给你找新的",
    };
  }
  if (runtime.pool_available_count === 0 && runtime.pool_pending_count > 0) {
    return {
      available: `找到 ${runtime.pool_pending_count} 条素材，正在整理成可换内容`,
      replenished: "正在整理",
      topics: "整理好就能换，不会把素材数当可换数",
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
        ? formatRuntimeTopicList(runtime.recent_pool_topics)
        : runtime.last_discovered_count > 0
          ? "但可立即换的库存还没变"
          : poolIsSufficient
            ? "先把这一池给你慢慢换开"
            : "还在继续摸你的口味",
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
    return { message: "这池先翻到头了，后台还在继续补新的。", tone: "info" };
  }
  return { message: "这池先翻到头了，等后台再补点新的。", tone: "info" };
}

export function getMobileRecommendationHeaderState({
  runtimeStatus = null,
  activityFeed = null,
  runtimeEvent = null,
  activityExpanded = false,
} = {}) {
  const runtime = normalizeRuntimeStatus(runtimeStatus);
  const poolSummary = getPoolStatusSummary(runtimeStatus);
  const pendingOnly =
    runtime.pool_available_count === 0 && runtime.pool_pending_count > 0;
  const activity = getActivityCardState({
    feed: activityFeed,
    runtimeEvent,
    expanded: activityExpanded,
  });
  return {
    kicker: "For You",
    title: "这几条，你大概会点开",
    primaryActionLabel: "换一批",
    secondaryActionLabel: "加载更多",
    activityLine: activity.line1,
    activityHeadline: activity.line2,
    activityExpanded: activity.expanded,
    activityToggleLabel: activity.expanded ? "收起" : "更多",
    activityItems: activity.items,
    activityHasMore: activity.has_more,
    activityNextCursor: activity.next_cursor,
    poolChips: poolSummary
      ? [
          { value: `${runtime.pool_available_count} 条`, label: "当前可换", tone: "neutral" },
          {
            value: pendingOnly
              ? `${runtime.pool_pending_count} 条`
              : runtime.manual_refresh_state === "running"
                ? (runtime.pool_available_count > 0 ? "继续补" : "正在补")
                : runtime.last_replenished_count > 0
                  ? `补进 ${runtime.last_replenished_count} 条`
                  : runtime.last_discovered_count > 0
                    ? "已发现"
                    : poolSummary.replenished,
            label: pendingOnly ? "素材整理" : "最近补进",
            tone: "brand",
          },
          {
            value: runtime.recent_pool_topics.length > 0
              ? formatCompactRuntimeTopicList(runtime.recent_pool_topics)
              : poolSummary.topics,
            label: "现在在忙",
            tone: "info",
          },
        ]
      : [],
  };
}

// ── Activity Feed ────────────────────────────────────────────

function getHintBannerState(tone) {
  const normalized = normalizeText(tone);
  if (normalized === "success" || normalized === "error") return { tone: normalized };
  return { tone: "info" };
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

// ── MBTI ─────────────────────────────────────────────────────

function normalizeDimensionPair(key) {
  const letters = normalizeText(key).replace(/[^A-Za-z]/g, "").toUpperCase();
  if (letters.length < 2) return null;
  return [letters[0], letters[1]];
}

function normalizeArrayDimension(dim) {
  const left = normalizeText(dim?.left) || normalizeText(dim?.label);
  const right = normalizeText(dim?.right);
  if (!left && !right) return null;
  return { left, right, score: round3(clamp01(dim?.score ?? dim?.value)) };
}

function normalizeObjectDimension(key, dim) {
  const pair = normalizeDimensionPair(key);
  if (!pair) return null;
  const [left, right] = pair;
  const pole = normalizeText(dim?.pole).toUpperCase();
  const strength = clamp01(dim?.strength);
  let score = 0.5;
  if (pole === left) score = 0.5 - strength / 2;
  else if (pole === right) score = 0.5 + strength / 2;
  return { left, right, score: round3(score) };
}

export function normalizeMbtiDimensions(mbti) {
  const raw = mbti?.dimensions;
  if (Array.isArray(raw)) return raw.map(normalizeArrayDimension).filter(Boolean);
  if (!raw || typeof raw !== "object") return [];
  const preferredOrder = ["EI", "SN", "TF", "JP"];
  const keys = [
    ...preferredOrder.filter((key) => Object.hasOwn(raw, key)),
    ...Object.keys(raw).filter((key) => !preferredOrder.includes(key)),
  ];
  return keys.map((key) => normalizeObjectDimension(key, raw[key])).filter(Boolean);
}

// ── Chat Turn ────────────────────────────────────────────────

export function normalizeChatTurn(turn) {
  if (!turn || typeof turn !== "object") {
    return { turn_id: "", message: "", response: "", status: "", error: "" };
  }
  return {
    ...turn,
    turn_id: normalizeText(turn.turn_id),
    message: normalizeText(turn.message),
    response: normalizeText(turn.response) || normalizeText(turn.reply),
    status: normalizeText(turn.status),
    error: normalizeText(turn.error),
  };
}

export function getMobileChatSession(scope = "chat") {
  return {
    session: "popup",
    scope: normalizeText(scope) || "chat",
  };
}

// ── Cognition Updates ────────────────────────────────────────

export function normalizeCognitionUpdateCard(item) {
  const fallbackContextLine = "基于最近几条相关内容";
  if (typeof item === "string") {
    return {
      summary: normalizeText(item),
      contextLine: fallbackContextLine,
      impact: "", reasoning: "", evidence: "",
      source: "", sourceLabel: "",
      expandHint: "summary_only", expandLabel: "仅结论",
      created_at: "", expandable: false,
    };
  }
  const impact = normalizeText(item?.impact);
  const reasoning = normalizeText(item?.reasoning);
  const evidence = normalizeText(item?.evidence);
  const contextLine =
    normalizeText(item?.context_line) ||
    normalizeText(item?.contextLine) ||
    fallbackContextLine;
  const explicitExpandHint =
    normalizeText(item?.expand_hint) ||
    normalizeText(item?.expandHint);
  const expandHint = (() => {
    if (explicitExpandHint === "expandable" || explicitExpandHint === "summary_only") {
      return explicitExpandHint;
    }
    if (typeof item?.expandable === "boolean") {
      return item.expandable ? "expandable" : "summary_only";
    }
    return impact || reasoning || evidence ? "expandable" : "summary_only";
  })();
  return {
    summary: normalizeText(item?.summary),
    contextLine,
    impact, reasoning, evidence,
    source: normalizeText(item?.source),
    sourceLabel: normalizeText(item?.source_label) || normalizeText(item?.sourceLabel),
    expandHint,
    expandLabel: normalizeText(item?.expandLabel) || (expandHint === "expandable" ? "展开" : "仅结论"),
    created_at: normalizeText(item?.created_at),
    expandable: expandHint === "expandable",
  };
}

function normalizeCognitionHistoryItems(items) {
  if (!Array.isArray(items)) return [];
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

// ── Profile Summary ──────────────────────────────────────────

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

export function getMbtiDisplayState(mbti) {
  const normalized = normalizeMBTI(mbti);
  if (!normalized?.type) {
    return { type: "", confidence_label: "", dimensions: [] };
  }
  const confidence = clamp01(normalized.confidence, 0);
  return {
    ...normalized,
    confidence_label: confidence > 0 ? `可信度 ${Math.round(confidence * 100)}%` : "",
    dimensions: normalizeMbtiDimensions(normalized),
  };
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

const DURATION_LABELS = {
  short: "短视频",
  medium: "中等",
  long: "长视频",
};

const PACE_LABELS = {
  fast: "快节奏",
  moderate: "适中",
  slow: "慢节奏",
};

function mappedLabel(map, value) {
  const text = normalizeText(value);
  return map[text] || text;
}

export function getProfileStyleDisplay(style) {
  const normalized = normalizeStyle(style);
  if (!normalized) return null;
  return {
    ...normalized,
    preferred_duration: mappedLabel(DURATION_LABELS, normalized.preferred_duration),
    preferred_pace: mappedLabel(PACE_LABELS, normalized.preferred_pace),
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

export function getContextPatternRows(context) {
  const normalized = normalizeContext(context);
  if (!normalized) return [];
  return [
    { key: "weekday", label: "工作日", value: normalized.weekday_patterns },
    { key: "weekend", label: "周末", value: normalized.weekend_patterns },
    { key: "time", label: "时段", value: normalized.time_of_day_patterns },
    { key: "session", label: "模式", value: normalized.session_type },
  ].filter((row) => row.value);
}

function normalizeProbeMode(value) {
  const mode = normalizeText(value);
  return ["near", "lateral", "bridge", "wildcard"].includes(mode) ? mode : "near";
}

function isChallengeProbe(mode, explicit) {
  return Boolean(explicit) || ["lateral", "bridge", "wildcard"].includes(mode);
}

export function normalizeProfileSummary(summary) {
  return {
    initialized: Boolean(summary?.initialized),
    personality_portrait: normalizeText(summary?.personality_portrait) || DEFAULT_PORTRAIT,
    core_traits: normalizeStrList(summary?.core_traits),
    deep_needs: normalizeStrList(summary?.deep_needs),
    mbti: normalizeMBTI(summary?.mbti),
    values: normalizeStrList(summary?.values),
    motivational_drivers: normalizeStrList(summary?.motivational_drivers),
    likes: normalizeInterestDomains(summary?.likes),
    dislikes: normalizeInterestDomains(summary?.dislikes),
    favorite_up_users: normalizeStrList(summary?.favorite_up_users),
    life_stage: normalizeText(summary?.life_stage),
    current_phase: normalizeText(summary?.current_phase),
    cognitive_style: normalizeStrList(summary?.cognitive_style),
    style: normalizeStyle(summary?.style),
    context: normalizeContext(summary?.context),
    exploration_openness: typeof summary?.exploration_openness === "number"
      ? Math.max(0, Math.min(1, summary.exploration_openness))
      : 0.5,
    speculative_interests: Array.isArray(summary?.speculative_interests)
      ? summary.speculative_interests
          .filter((item) => item?.domain)
          .map((item) => {
            const probeMode = normalizeProbeMode(item.probe_mode);
            return {
              domain: normalizeText(item.domain),
              reason: normalizeText(item.reason),
              confidence: Number(item.confidence ?? 0),
              probe_mode: probeMode,
              challenge: isChallengeProbe(probeMode, item.challenge),
              confirmation_count: Number(item.confirmation_count ?? 0),
              confirmation_threshold: Number(item.confirmation_threshold ?? 3),
              status: normalizeText(item.status) || "active",
              specifics: Array.isArray(item.specifics)
                ? item.specifics
                    .filter((s) => s?.name)
                    .map((s) => ({ name: normalizeText(s.name), confirmation_count: Number(s.confirmation_count ?? 0) }))
                : [],
            };
          })
      : [],
    speculative_avoidances: Array.isArray(summary?.speculative_avoidances)
      ? summary.speculative_avoidances
          .filter((item) => item?.domain)
          .map((item) => ({
            domain: normalizeText(item.domain),
            reason: normalizeText(item.reason),
            confidence: Number(item.confidence ?? 0),
            source_mode: normalizeText(item.source_mode),
            source_signal: normalizeText(item.source_signal),
            confirmation_count: Number(item.confirmation_count ?? 0),
            confirmation_threshold: Number(item.confirmation_threshold ?? 3),
            status: normalizeText(item.status) || "active",
            specifics: Array.isArray(item.specifics)
              ? item.specifics
                  .filter((s) => s?.name)
                  .map((s) => ({ name: normalizeText(s.name), confirmation_count: Number(s.confirmation_count ?? 0) }))
              : [],
          }))
      : [],
    recent_cognition_updates: Array.isArray(summary?.recent_cognition_updates)
      ? summary.recent_cognition_updates.map(normalizeCognitionUpdateCard).filter((item) => item.summary)
      : [],
    has_more_cognition_updates: Boolean(summary?.has_more_cognition_updates),
    next_cognition_cursor: normalizeText(summary?.next_cognition_cursor),
    active_insights: Array.isArray(summary?.active_insights)
      ? summary.active_insights
          .filter((item) => item?.hypothesis)
          .map((item) => ({
            hypothesis: normalizeText(item.hypothesis),
            evidence: Array.isArray(item.evidence)
              ? item.evidence.map((e) => normalizeText(e)).filter(Boolean) : [],
            confidence: typeof item.confidence === "number"
              ? Math.max(0, Math.min(1, item.confidence)) : 0.5,
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

// ── Timestamp ────────────────────────────────────────────────

export function formatRelativeTimestamp(isoString, now = Date.now()) {
  const text = normalizeText(isoString);
  if (!text) return "";
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) return "";
  const diffMs = now - parsed;
  if (diffMs < 60_000) return "刚刚";
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay} 天前`;
  const date = new Date(parsed);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}
