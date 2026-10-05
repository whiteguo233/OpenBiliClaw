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

// Placeholders the LLM emits when it has no signal for a text field. Treated as
// absent so the profile panel falls back to its "still observing" copy.
const UNKNOWNISH_TEXT = new Set(["", "unknown", "none", "n/a", "未知"]);

function stripPlaceholderText(value) {
  const text = normalizeText(value);
  return UNKNOWNISH_TEXT.has(text.toLowerCase()) ? "" : text;
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
const DEFAULT_CREATOR_NAME = "这位创作者还没认出来";
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
  weibo: "微博",
  youtube: "YouTube",
  twitter: "X (Twitter)",
  zhihu: "知乎",
  reddit: "Reddit",
  bangumi: "Bangumi",
  linuxdo: "Linux.do",
  v2ex: "V2EX",
  instagram: "Instagram",
  web: "Web",
};

const SOURCE_ALIAS_MAP = {
  bili: "bilibili",
  bilibili: "bilibili",
  xhs: "xiaohongshu",
  xiaohongshu: "xiaohongshu",
  rednote: "xiaohongshu",
  dy: "douyin",
  douyin: "douyin",
  tiktok: "douyin",
  wb: "weibo",
  weibo: "weibo",
  yt: "youtube",
  youtube: "youtube",
  x: "twitter",
  twitter: "twitter",
  zh: "zhihu",
  zhihu: "zhihu",
  rd: "reddit",
  reddit: "reddit",
  bgm: "bangumi",
  bangumi: "bangumi",
  linuxdo: "linuxdo",
  "linux.do": "linuxdo",
  v2: "v2ex",
  v2ex: "v2ex",
  ig: "instagram",
  instagram: "instagram",
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
  weibo_search: "微博搜索",
  weibo_hot: "微博热榜",
  weibo_creator: "微博作者",
  "weibo-search": "微博搜索",
  "weibo-hot": "微博热榜",
  "weibo-creator": "微博作者",
  yt_search: "YouTube 搜索",
  yt_trending: "YouTube 热榜",
  yt_channel: "YouTube 频道",
  youtube_search: "YouTube 搜索",
  youtube_trending: "YouTube 热榜",
  youtube_channel: "YouTube 频道",
  zhihu_search: "知乎搜索",
  zhihu_hot: "知乎热榜",
  zhihu_feed: "知乎首页",
  zhihu_creator: "知乎作者",
  zhihu_related: "知乎相关",
  "zhihu-search": "知乎搜索",
  "zhihu-hot": "知乎热榜",
  "zhihu-feed": "知乎首页",
  "zhihu-creator": "知乎作者",
  "zhihu-related": "知乎相关",
  reddit_search: "Reddit 搜索",
  reddit_hot: "Reddit 热门",
  reddit_subreddit: "Reddit 社区",
  reddit_related: "Reddit 相关",
  "reddit-search": "Reddit 搜索",
  "reddit-hot": "Reddit 热门",
  "reddit-subreddit": "Reddit 社区",
  "reddit-related": "Reddit 相关",
  bangumi_search: "Bangumi 搜索",
  bangumi_ranked: "Bangumi 排名",
  bangumi_latest: "Bangumi 按日期浏览",
  "bangumi-search": "Bangumi 搜索",
  "bangumi-ranked": "Bangumi 排名",
  "bangumi-latest": "Bangumi 按日期浏览",
  linuxdo_search: "Linux.do 搜索",
  linuxdo_hot: "Linux.do 热门",
  linuxdo_feed: "Linux.do 最新",
  linuxdo_creator: "Linux.do 作者",
  linuxdo_related: "Linux.do 相关",
  "linuxdo-search": "Linux.do 搜索",
  "linuxdo-hot": "Linux.do 热门",
  "linuxdo-feed": "Linux.do 最新",
  "linuxdo-creator": "Linux.do 作者",
  "linuxdo-related": "Linux.do 相关",
  "v2ex-search": "V2EX 搜索",
  "v2ex-node": "V2EX Node",
  "v2ex-tab": "V2EX Tab",
  "v2ex-hot": "V2EX 热门",
  "v2ex-latest": "V2EX 最新",
  "instagram-topic": "Instagram Topic",
  "instagram-creator": "Instagram 作者",
};

function urlHostMatches(url, hostnames) {
  const text = normalizeText(url);
  if (!text) return false;
  try {
    const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`;
    const host = new URL(candidate).hostname.toLowerCase();
    return hostnames.some((hostname) => host === hostname || host.endsWith(`.${hostname}`));
  } catch {
    return false;
  }
}

export function normalizeSourcePlatform(item) {
  const explicit = normalizeText(item?.source_platform).toLowerCase();
  if (explicit && SOURCE_ALIAS_MAP[explicit]) return SOURCE_ALIAS_MAP[explicit];
  const url = normalizeText(item?.content_url);
  if (url) {
    const lowerUrl = url.toLowerCase();
    if (lowerUrl.includes("bilibili.com") || lowerUrl.includes("b23.tv")) return "bilibili";
    if (lowerUrl.includes("xiaohongshu.com") || lowerUrl.includes("xhslink.com")) return "xiaohongshu";
    if (lowerUrl.includes("douyin.com")) return "douyin";
    if (urlHostMatches(url, ["weibo.com", "weibo.cn", "sinaimg.cn", "sinaimg.com"])) return "weibo";
    if (lowerUrl.includes("youtube.com") || lowerUrl.includes("youtu.be")) return "youtube";
    if (urlHostMatches(url, ["x.com", "twitter.com"])) return "twitter";
    if (urlHostMatches(url, ["zhihu.com", "zhuanlan.zhihu.com"])) return "zhihu";
    if (urlHostMatches(url, ["reddit.com", "redd.it"])) return "reddit";
    if (urlHostMatches(url, ["bgm.tv", "bangumi.tv"])) return "bangumi";
    if (urlHostMatches(url, ["linux.do"])) return "linuxdo";
    if (urlHostMatches(url, ["v2ex.com"])) return "v2ex";
    if (urlHostMatches(url, ["instagram.com"])) return "instagram";
    return "web";
  }
  if (normalizeText(item?.bvid)) return "bilibili";
  return explicit || "bilibili";
}

/** Preserve canonical saved identity without treating UI row IDs or namespaced IDs as content IDs. */
export function normalizeSavedIdentity(item = {}) {
  const sourcePlatform = normalizeSourcePlatform(item);
  const legacyId = normalizeText(item?.bvid);
  const contentId = normalizeText(
    item?.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""),
  );
  return {
    ...item,
    item_key: normalizeText(item?.item_key) || (contentId ? `${sourcePlatform}:${contentId}` : ""),
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: normalizeText(item?.content_url || item?.url),
    content_type: normalizeText(item?.content_type)
      || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
  };
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
  if (key.startsWith("weibo-")) return "微博";
  if (key.startsWith("yt-") || key.startsWith("youtube-")) return "YouTube";
  if (key.startsWith("reddit-")) return "Reddit";
  if (key.startsWith("bangumi-")) return "Bangumi";
  if (key.startsWith("linuxdo-")) return "Linux.do";
  if (key.startsWith("v2ex-")) return "V2EX";
  if (key.startsWith("instagram-")) return "Instagram";
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

export function buildTwitterUrl(statusId) {
  return `https://x.com/i/status/${normalizeText(statusId)}`;
}

export function buildContentUrl(item) {
  if (item?.content_url) return item.content_url;
  const platform = normalizeSourcePlatform(item);
  const vid = normalizeText(item?.content_id || item?.bvid);
  if (!vid) return "";
  if (platform === "youtube") return buildYouTubeUrl(vid);
  if (platform === "twitter") return buildTwitterUrl(vid);
  if (platform === "bangumi") return `https://bgm.tv/subject/${encodeURIComponent(vid)}`;
  if (platform === "linuxdo") {
    const topicId = vid.replace(/^(?:linuxdo:)?topic[:_]/i, "");
    return /^[1-9]\d*$/.test(topicId)
      ? `https://linux.do/t/${encodeURIComponent(topicId)}`
      : "";
  }
  if (platform === "zhihu" || platform === "reddit") return "";
  if (platform === "v2ex") return `https://www.v2ex.com/t/${encodeURIComponent(vid)}`;
  if (
    platform === "zhihu"
    || platform === "reddit"
    || platform === "weibo"
    || platform === "instagram"
  ) return "";
  return buildVideoUrl(vid);
}

export function buildRecommendationClickPayload(item, contentUrl = "") {
  const bvid = normalizeText(item?.bvid || item?.content_id);
  const contentId = normalizeText(item?.content_id || item?.bvid);
  return {
    bvid,
    content_id: contentId,
    content_url: normalizeText(contentUrl) || normalizeText(item?.content_url),
    source_platform: normalizeSourcePlatform(item),
    title: normalizeText(item?.title),
    recommendation_id: typeof item?.id === "number" ? item.id : null,
    topic_label: normalizeText(item?.topic_label),
    up_name: normalizeText(item?.up_name),
  };
}

// ── Recommendation Normalization ─────────────────────────────

export function normalizeRecommendation(item) {
  const bvid = normalizeText(item?.bvid);
  const sourcePlatform = normalizeSourcePlatform(item);
  const contentId = normalizeText(item?.content_id)
    || (bvid && !bvid.includes(":") ? bvid : "");
  return {
    id: Number(item?.id ?? 0),
    bvid,
    title: normalizeText(item?.title) || DEFAULT_TITLE,
    up_name: normalizeText(item?.up_name)
      || (sourcePlatform === "bangumi"
        ? ""
        : sourcePlatform === "bilibili"
        ? DEFAULT_UP_NAME
        : DEFAULT_CREATOR_NAME),
    cover_url: normalizeCoverUrl(item?.cover_url),
    expression: normalizeText(item?.expression),
    topic_label: normalizeText(item?.topic_label),
    presented: Boolean(item?.presented),
    item_key: normalizeText(item?.item_key),
    content_id: contentId,
    content_url: normalizeText(item?.content_url) || "",
    source_platform: sourcePlatform,
    content_type: normalizeText(item?.content_type)
      || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
    body_text: normalizeText(item?.body_text),
    published_at: normalizeText(item?.published_at),
    published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
    view_count: Number(item?.view_count ?? 0),
    like_count: Number(item?.like_count ?? 0),
    comment_count: Number(item?.comment_count ?? 0),
    share_count: Number(item?.share_count ?? 0),
    favorite_count: Number(item?.favorite_count ?? 0),
    danmaku_count: Number(item?.danmaku_count ?? 0),
    rating_score: Number(item?.rating_score ?? 0),
    rating_count: Number(item?.rating_count ?? 0),
    source_rank: Number(item?.source_rank ?? 0),
  };
}

export function reconcileRecommendationReplacement(currentItems, incomingItems) {
  const current = Array.isArray(currentItems) ? currentItems : [];
  const incoming = Array.isArray(incomingItems) ? incomingItems : [];
  const preserved = incoming.length === 0 && current.length > 0;
  return {
    items: preserved ? current : incoming,
    preserved,
  };
}

export function formatPublishedTime(item, now = Date.now()) {
  const parsed = Date.parse(String(item?.published_at || ""));
  if (Number.isFinite(parsed)) {
    const diff = now - parsed;
    if (diff >= -300_000 && diff < 60_000) return "刚刚";
    if (diff >= 0 && diff < 86_400_000) {
      return `${Math.max(1, Math.floor(diff / 3_600_000))} 小时前`;
    }
    if (diff >= 0 && diff < 604_800_000) {
      return `${Math.floor(diff / 86_400_000)} 天前`;
    }
    const date = new Date(parsed);
    const current = new Date(now);
    if (date.getFullYear() === current.getFullYear()) {
      return `${date.getMonth() + 1}月${date.getDate()}日`;
    }
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  return String(item?.published_label || "").replace(/\s+/g, " ").trim().slice(0, 64);
}

export function getPublishedTimeDisplay(item, now = Date.now()) {
  const text = formatPublishedTime(item, now);
  if (!text) return null;
  const parsed = Date.parse(String(item?.published_at || ""));
  return {
    text,
    title: Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : "",
  };
}

// ── Engagement stats ─────────────────────────────────────────
// Condense a raw count into Chinese-style 万/亿 units. Empty string for
// non-positive values so callers render nothing.
export function formatCountCn(n) {
  const value = Math.floor(Number(n) || 0);
  if (value <= 0) return "";
  if (value >= 100000000)
    return `${(Math.floor((value / 100000000) * 10) / 10).toFixed(1).replace(/\.0$/, "")}亿`;
  if (value >= 10000)
    return `${(Math.floor((value / 10000) * 10) / 10).toFixed(1).replace(/\.0$/, "")}万`;
  return String(value);
}

// Build the "▶ … · 👍 … · 💬 … · ⭐ … · 弹幕 …" stats line. Only counts
// > 0 appear; when nothing qualifies the result is "" (render nothing).
export function recommendationStats(item) {
  const segments = [];
  const sourceRank = Math.trunc(Number(item?.source_rank) || 0);
  if (item?.view_count > 0) segments.push(`▶ ${formatCountCn(item.view_count)}`);
  if (item?.like_count > 0) segments.push(`👍 ${formatCountCn(item.like_count)}`);
  if (item?.comment_count > 0) segments.push(`💬 ${formatCountCn(item.comment_count)}`);
  if (item?.share_count > 0) segments.push(`🔁 ${formatCountCn(item.share_count)}`);
  if (item?.favorite_count > 0) segments.push(`⭐ ${formatCountCn(item.favorite_count)}`);
  if (item?.danmaku_count > 0) segments.push(`弹幕 ${formatCountCn(item.danmaku_count)}`);
  if (item?.rating_score > 0) segments.push(`评分 ${Number(item.rating_score).toFixed(1)}`);
  if (item?.rating_count > 0) segments.push(`${formatCountCn(item.rating_count)} 人评分`);
  if (sourceRank > 0) segments.push(`排名 #${sourceRank}`);
  return segments.join(" · ");
}

const TEXT_CARD_CONTENT_TYPES = new Set([
  "tweet",
  "thread",
  "answer",
  "article",
  "question",
  "post",
  "comment",
]);

// Decide the media slot for a recommendation card. Text-first sources
// (X tweet/thread, Zhihu answer/article/question) render a no-cover text card from
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
    item_key: normalizeText(item?.item_key),
    content_id: normalizeText(item?.content_id),
    title: normalizeText(item?.title) || DEFAULT_DELIGHT_TITLE,
    delight_reason: normalizeText(item?.delight_reason) || DEFAULT_DELIGHT_REASON,
    delight_score: Number(item?.delight_score ?? 0),
    delight_hook: normalizeText(item?.delight_hook),
    cover_url: normalizeCoverUrl(item?.cover_url),
    content_url: normalizeText(item?.content_url),
    source_platform: normalizeSourcePlatform(item),
    published_at: normalizeText(item?.published_at),
    published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
    content_type: normalizeText(item?.content_type),
    body_text: normalizeText(item?.body_text),
    state: normalizeText(item?.state) || "pending",
    response_message: normalizeText(item?.response_message),
    response_tone: normalizeText(item?.response_tone) || "info",
    chat_reply: normalizeText(item?.chat_reply),
    view_count: Number(item?.view_count ?? 0),
    like_count: Number(item?.like_count ?? 0),
    comment_count: Number(item?.comment_count ?? 0),
    share_count: Number(item?.share_count ?? 0),
    favorite_count: Number(item?.favorite_count ?? 0),
    danmaku_count: Number(item?.danmaku_count ?? 0),
    rating_score: Number(item?.rating_score ?? 0),
    rating_count: Number(item?.rating_count ?? 0),
    source_rank: Number(item?.source_rank ?? 0),
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
      show_status: false, show_actions: false,
      like_pressed: false, like_disabled: false,
      score_label: "", response_tone: "info", response_message: "",
    };
  }
  const score = normalized.delight_score;
  const scoreLabel =
    score >= 0.85 ? "大概率会戳中你" :
    score >= 0.65 ? "这条可能会拐到你" :
    "有点出其不意";
  const highlight = normalizeText(highlightBvid) === normalized.bvid;
  const base = {
    visible: true,
    highlighted: highlight,
    handled: false,
    show_status: Boolean(normalized.response_message),
    show_actions: true,
    like_pressed: false,
    like_disabled: false,
    score_label: scoreLabel,
    response_tone: normalized.response_tone || "info",
    response_message: normalized.response_message,
  };

  if (normalized.state === "viewed") {
    return {
      ...base, handled: true, show_status: true, show_actions: false,
      like_disabled: true, response_tone: "success",
      response_message: normalized.response_message || "已打开，阿B 会把这次点击当成强信号。",
    };
  }
  if (normalized.state === "liked") {
    return {
      ...base, show_status: true, show_actions: true,
      like_pressed: true, like_disabled: true, response_tone: "success",
      response_message: normalized.response_message || "好，这类多来点。",
    };
  }
  if (normalized.state === "rejected") {
    return {
      ...base, handled: true, show_status: true, show_actions: false,
      like_disabled: true,
      response_message: normalized.response_message || "记下了，这类惊喜先少来点。",
    };
  }
  if (normalized.state === "chatted" || normalized.state === "chatting") {
    const responseMessage = normalized.response_message
      || (normalized.state === "chatted" ? "这句已经记下，后面会更会试探。" : "");
    return {
      ...base,
      show_status: Boolean(responseMessage),
      response_message: responseMessage,
    };
  }
  return base;
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
    { label: "确认喜欢", action: "confirm", primary: true },
    { label: "暂时搁置", action: "defer", primary: false },
    { label: "确认不喜欢", action: "reject", primary: false },
    { label: "多聊聊", action: "chat", primary: false },
  ];
}

export function getAvoidanceProbeMessageActions() {
  return [
    { label: "确认避雷", action: "confirm", primary: true },
    { label: "搁置避雷", action: "defer", primary: false },
    { label: "不是雷点", action: "reject", primary: false },
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
    discovery_failure_message: normalizeText(status?.discovery_failure_message),
  };
}

export function mergeRuntimeStatusEvent(status, event) {
  const runtime = normalizeRuntimeStatus(status);
  const next = { ...runtime };
  if (event?.type === "refresh.started" || event?.type === "refresh.strategy") {
    next.manual_refresh_state = "running";
    next.manual_refresh_message = normalizeText(event?.message);
  } else if (event?.type === "refresh.pool_updated") {
    next.manual_refresh_state = "success";
  } else if (event?.type === "refresh.failed") {
    next.manual_refresh_state = "failed";
    next.manual_refresh_message = normalizeText(event?.message);
  }
  if (typeof event?.discovery_failure_message === "string") {
    next.discovery_failure_message = normalizeText(event.discovery_failure_message);
  } else if (Number(event?.pool_available_count) > 0) {
    next.discovery_failure_message = "";
  }
  if (typeof event?.pool_available_count === "number") {
    // A pool snapshot can only be emitted by a running, initialized backend.
    // Promote the partial stream payload so first-load HTTP timeouts do not
    // hide otherwise authoritative inventory from the mobile header.
    next.initialized = true;
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
  if (runtime.pool_available_count === 0
      && (runtime.discovery_failure_message || runtime.manual_refresh_state === "failed")) {
    return {
      available: "暂无可换库存",
      replenished: "内容发现未完成",
      topics: runtime.manual_refresh_state === "failed"
        ? runtime.manual_refresh_message || "请检查来源连接后重试内容发现"
        : runtime.discovery_failure_message,
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
        : runtime.pool_pending_count > 0
          ? `另有 ${runtime.pool_pending_count} 条素材`
        : poolIsSufficient
          ? "这会儿先不补货"
          : "这轮还没补进",
    topics:
      runtime.recent_pool_topics.length > 0
        ? formatRuntimeTopicList(runtime.recent_pool_topics)
        : runtime.last_discovered_count > 0
          ? "但可立即换的库存还没变"
          : runtime.pool_pending_count > 0
            ? "素材已抓到，会按可换库存缺口整理"
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
  if (runtime.manual_refresh_state === "failed") {
    return { message: runtime.manual_refresh_message || "内容发现未完成，请检查来源连接后重试内容发现。", tone: "error" };
  }
  if (runtime.discovery_failure_message) {
    return { message: runtime.discovery_failure_message, tone: "error" };
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
            label: pendingOnly ? "素材整理" : "补货进展",
            tone: "brand",
          },
          {
            value: runtime.recent_pool_topics.length > 0
              ? formatCompactRuntimeTopicList(runtime.recent_pool_topics)
              : poolSummary.topics,
            label: poolSummary.replenished === "内容发现未完成" ? "补货状态" : "现在在忙",
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
    preferred_duration: mappedLabel(DURATION_LABELS, stripPlaceholderText(normalized.preferred_duration)),
    preferred_pace: mappedLabel(PACE_LABELS, stripPlaceholderText(normalized.preferred_pace)),
  };
}

function normalizeContext(raw) {
  if (!raw) return null;
  return {
    weekday_patterns: stripPlaceholderText(raw.weekday_patterns),
    weekend_patterns: stripPlaceholderText(raw.weekend_patterns),
    time_of_day_patterns: stripPlaceholderText(raw.time_of_day_patterns),
    session_type: stripPlaceholderText(raw.session_type),
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
