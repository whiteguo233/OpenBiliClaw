const DEFAULT_TITLE = "这条标题还没对上号";
const DEFAULT_UP_NAME = "这位 UP 还没认出来";
const DEFAULT_CREATOR_NAME = "这位创作者还没认出来";
const DEFAULT_PORTRAIT = "画像还在慢慢攒，先多看一阵。";
const DEFAULT_DELIGHT_TITLE = "这条惊喜推荐还没起好标题";
const DEFAULT_DELIGHT_REASON = "这条可能会给你一点意外之喜。";

/** Request within the click gesture, but never let an unanswered prompt pin the form. */
export async function requestPermissionWithTimeout(request, { timeoutMs = 30_000 } = {}) {
  if (typeof request !== "function") return "unavailable";
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve("timeout"), Math.max(1, timeoutMs));
  });
  try {
    // Invoke synchronously before the first await: permissions.request needs
    // the original user gesture. A late resolution has no save side effects.
    return await Promise.race([
      Promise.resolve(request()).then((granted) => granted === true ? "granted" : "denied"),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function normalizeText(value) {
  return typeof value === "string" ? value.trim() : "";
}

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

function normalizeSourcePlatform(value, url = "") {
  const key = normalizeText(value).toLowerCase();
  const aliases = {
    bili: "bilibili",
    bilibili: "bilibili",
    xhs: "xiaohongshu",
    xiaohongshu: "xiaohongshu",
    rednote: "xiaohongshu",
    dy: "douyin",
    douyin: "douyin",
    tt: "tiktok",
    tiktok: "tiktok",
    wb: "weibo",
    weibo: "weibo",
    yt: "youtube",
    youtube: "youtube",
    x: "twitter",
    twitter: "twitter",
    gh: "github",
    github: "github",
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
  if (aliases[key]) return aliases[key];
  if (key) return key;
  const lowerUrl = normalizeText(url).toLowerCase();
  if (lowerUrl.includes("bilibili.com") || lowerUrl.includes("b23.tv")) return "bilibili";
  if (lowerUrl.includes("xiaohongshu.com") || lowerUrl.includes("xhslink.com")) return "xiaohongshu";
  if (lowerUrl.includes("douyin.com")) return "douyin";
  if (urlHostMatches(url, ["tiktok.com"])) return "tiktok";
  if (urlHostMatches(url, ["weibo.com", "weibo.cn", "sinaimg.cn", "sinaimg.com"])) return "weibo";
  if (lowerUrl.includes("youtube.com") || lowerUrl.includes("youtu.be")) return "youtube";
  if (urlHostMatches(url, ["x.com", "twitter.com"])) return "twitter";
  if (urlHostMatches(url, ["github.com"])) return "github";
  if (urlHostMatches(url, ["zhihu.com", "zhuanlan.zhihu.com"])) return "zhihu";
  if (urlHostMatches(url, ["reddit.com", "redd.it"])) return "reddit";
  if (urlHostMatches(url, ["bgm.tv", "bangumi.tv"])) return "bangumi";
  if (urlHostMatches(url, ["linux.do"])) return "linuxdo";
  if (urlHostMatches(url, ["v2ex.com"])) return "v2ex";
  if (urlHostMatches(url, ["instagram.com"])) return "instagram";
  return "";
}

export function normalizeProbeType(type) {
  return normalizeText(type) === "avoidance.probe" ? "avoidance.probe" : "interest.probe";
}

export function probeMessageKey(type, domain) {
  const normalizedDomain = normalizeText(domain).toLowerCase();
  if (!normalizedDomain) {
    return "";
  }
  return `${normalizeProbeType(type)}:${normalizedDomain}`;
}

function hasProbeKey(handledProbeKeys, key) {
  return Boolean(key && handledProbeKeys && typeof handledProbeKeys.has === "function" && handledProbeKeys.has(key));
}

export function shouldHydrateProbe(item, type = "interest.probe", handledProbeKeys = new Set()) {
  const domain = normalizeText(item?.domain);
  if (!domain) {
    return false;
  }
  const status = normalizeText(item?.status).toLowerCase() || "active";
  if (status !== "active") {
    return false;
  }
  return !hasProbeKey(handledProbeKeys, probeMessageKey(type, domain));
}

export function shouldDisplayProbeFromWebSocket(
  event,
  type = event?.type || "interest.probe",
  handledProbeKeys = new Set(),
) {
  return shouldHydrateProbe(
    { domain: event?.domain, status: "active" },
    normalizeProbeType(type),
    handledProbeKeys,
  );
}

export function buildStaleProbeResponseState({
  messages = [],
  pendingProbe = null,
  pendingAvoidanceProbe = null,
  domain = "",
  type = "interest.probe",
} = {}) {
  const normalizedType = normalizeProbeType(type);
  const handledKey = probeMessageKey(normalizedType, domain);
  const nextMessages = Array.isArray(messages)
    ? messages.filter((message) => probeMessageKey(message?.type, message?.domain) !== handledKey)
    : [];
  return {
    handledKey,
    messages: nextMessages,
    pendingProbe:
      normalizedType === "interest.probe" && probeMessageKey("interest.probe", pendingProbe?.domain) === handledKey
        ? null
        : pendingProbe,
    pendingAvoidanceProbe:
      normalizedType === "avoidance.probe" && probeMessageKey("avoidance.probe", pendingAvoidanceProbe?.domain) === handledKey
        ? null
        : pendingAvoidanceProbe,
  };
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

const PLATFORM_DISPLAY_NAMES = {
  bilibili: "B 站",
  youtube: "YouTube",
  douyin: "抖音",
  tiktok: "TikTok",
  tt: "TikTok",
  weibo: "微博",
  wb: "微博",
  xiaohongshu: "小红书",
  xhs: "小红书",
  twitter: "X",
  x: "X",
  github: "GitHub",
  gh: "GitHub",
  zhihu: "知乎",
  reddit: "Reddit",
  bgm: "Bangumi",
  bangumi: "Bangumi",
  linuxdo: "Linux.do",
  "linux.do": "Linux.do",
  v2: "V2EX",
  v2ex: "V2EX",
  ig: "Instagram",
  instagram: "Instagram",
};

export function platformDisplayName(value) {
  const key = normalizeText(value).toLowerCase();
  return PLATFORM_DISPLAY_NAMES[key] || normalizeText(value);
}

/**
 * Build the author line shown on a recommendation card.
 *
 * "UP 主" is Bilibili-specific jargon, so the warm "这位 UP：" prefix only
 * applies to Bilibili content. Every other source carries a creator whose
 * role differs per platform (Bangumi ships directors / studios, Zhihu ships
 * answer authors, YouTube ships channels), so prefixing them with "UP" is
 * simply wrong. Those fall back to the bare name — which is what desktop web
 * (`recommendationMetaHtml`) and mobile web (`views/recommend.js`) already
 * render, so this keeps the three surfaces consistent.
 *
 * @param {{ up_name?: string, author_name?: string, source_platform?: string }} [item]
 * @returns {string} display text, or "" when there is no creator to show
 */
export function formatRecommendationAuthorLine(item) {
  const name = normalizeText(item?.up_name) || normalizeText(item?.author_name);
  if (!name) return "";
  const platform = normalizeSourcePlatform(item?.source_platform) || "bilibili";
  return platform === "bilibili" ? `这位 UP：${name}` : name;
}

export function buildVideoUrl(bvid) {
  return `https://www.bilibili.com/video/${normalizeText(bvid)}`;
}

export function buildYouTubeUrl(videoId) {
  return `https://www.youtube.com/watch?v=${normalizeText(videoId)}`;
}

export function buildContentUrl(item) {
  if (item?.content_url) return item.content_url;
  const platform = normalizeSourcePlatform(item?.source_platform, item?.content_url);
  const vid = normalizeText(item?.content_id || item?.bvid);
  if (!vid) return "";
  if (platform === "youtube") return buildYouTubeUrl(vid);
  if (platform === "bangumi") return `https://bgm.tv/subject/${encodeURIComponent(vid)}`;
  if (platform === "linuxdo") {
    const topicId = vid.replace(/^(?:linuxdo:)?topic[:_]/i, "");
    return /^[1-9]\d*$/.test(topicId)
      ? `https://linux.do/t/${encodeURIComponent(topicId)}`
      : "";
  }
  // A GitHub numeric repository id cannot reconstruct owner/name. The backend
  // always supplies the canonical https://github.com/<owner>/<repo> URL; if it
  // is absent, fail closed instead of fabricating a Bilibili link.
  if (platform === "github" || platform === "zhihu" || platform === "reddit") return "";
  if (platform === "v2ex") return `https://www.v2ex.com/t/${encodeURIComponent(vid)}`;
  if (platform === "instagram") return "";
  if (platform === "zhihu" || platform === "reddit" || platform === "weibo") return "";
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

export function getTabButtonState(activeTab, tabName) {
  return {
    selected: activeTab === tabName,
    tabIndex: activeTab === tabName ? 0 : -1,
  };
}

export function shouldAutoLoadRecommendations({
  activeTab = "recommend",
  loadingMore = false,
  hasMoreRecommendations = false,
  userArmed = false,
} = {}) {
  return Boolean(
    userArmed &&
      activeTab === "recommend" &&
      !loadingMore &&
      hasMoreRecommendations,
  );
}

export function getConnectionBadgeState(status) {
  if (status === "online") {
    return {
      tone: "online",
      label: "已连接",
    };
  }

  if (status === "reconnecting") {
    return {
      tone: "reconnecting",
      label: "重连中",
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

// Decide what Bangumi username guided init should send, or null to omit it so
// the backend keeps the configured value (an omitted username means "keep
// existing"). Only a deliberately typed value, or an explicit clear of a value
// a successful /api/config prefill put in the field, is sent — an empty field
// we never prefilled (config fetch pending/failed, or never touched) must NOT
// erase a configured username with "".
export function resolveInitBangumiUsername({ touched, prefilled, value } = {}) {
  const trimmed = String(value ?? "").trim();
  if (!touched) return null;
  if (!trimmed && !prefilled) return null;
  return trimmed;
}

// GitHub uses the same write-only omit-vs-clear rule as Bangumi usernames:
// an untouched field keeps the configured value, while deliberately clearing
// a successfully prefilled username sends an empty string. Keep a named helper
// so guided-init call sites cannot accidentally clear identity on a failed
// config prefill.
export function resolveInitGitHubUsername({ touched, prefilled, value } = {}) {
  return resolveInitBangumiUsername({ touched, prefilled, value });
}

export function normalizeRecommendation(item) {
  const bvid = normalizeText(item?.bvid);
  const sourcePlatform = normalizeSourcePlatform(item?.source_platform, item?.content_url) || "bilibili";
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
    // Engagement counts so the card can render the ▶/👍/💬/⭐ stats row
    // (favorite_count already folds in Xiaohongshu 收藏 backend-side).
    view_count: Number(item?.view_count ?? 0) || 0,
    like_count: Number(item?.like_count ?? 0) || 0,
    comment_count: Number(item?.comment_count ?? 0) || 0,
    share_count: Number(item?.share_count ?? 0) || 0,
    favorite_count: Number(item?.favorite_count ?? 0) || 0,
    danmaku_count: Number(item?.danmaku_count ?? 0) || 0,
    rating_score: Number(item?.rating_score ?? 0) || 0,
    rating_count: Number(item?.rating_count ?? 0) || 0,
    source_rank: Number(item?.source_rank ?? 0) || 0,
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

const TEXT_CARD_CONTENT_TYPES = new Set([
  "tweet",
  "thread",
  "repository",
  "answer",
  "article",
  "question",
  "post",
  "comment",
]);

/**
 * Decide how a recommendation card should render its media slot.
 *
 * Text-first sources (X tweets / threads) ship no cover image — the
 * value is the text. For those (content_type tweet/thread, or simply an
 * empty cover_url) the card shows a "no-cover text card" built from
 * body_text/title instead of an <img> thumbnail, so the popup never
 * paints a broken-image node.
 *
 * @param {object} item - A (normalized or raw) recommendation item.
 * @returns {{kind: "text"|"cover", coverUrl: string, text: string}}
 */
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

export function normalizeSavedItem(item) {
  const bvid = normalizeText(item?.bvid || item?.content_id);
  return {
    ...item,
    bvid,
    title: normalizeText(item?.title) || bvid,
    up_name: normalizeText(item?.up_name || item?.author_name),
    cover_url: normalizeCoverUrl(item?.cover_url),
    content_url: normalizeText(item?.content_url),
    source_platform: normalizeSourcePlatform(item?.source_platform, item?.content_url) || "bilibili",
  };
}

export function normalizeDelightCandidate(item) {
  const normalizedState = normalizeText(item?.state) || "pending";
  return {
    bvid: normalizeText(item?.bvid),
    item_key: normalizeText(item?.item_key),
    content_id: normalizeText(item?.content_id),
    title: normalizeText(item?.title) || DEFAULT_DELIGHT_TITLE,
    delight_reason: normalizeText(item?.delight_reason) || DEFAULT_DELIGHT_REASON,
    delight_score: Number(item?.delight_score ?? 0),
    delight_hook: normalizeText(item?.delight_hook),
    cover_url: normalizeCoverUrl(item?.cover_url),
    content_url: normalizeText(item?.content_url) || "",
    source_platform: normalizeSourcePlatform(item?.source_platform, item?.content_url) || "",
    published_at: normalizeText(item?.published_at),
    published_label: String(item?.published_label ?? "").replace(/\s+/g, " ").trim().slice(0, 64),
    content_type: normalizeText(item?.content_type),
    body_text: normalizeText(item?.body_text),
    state: normalizedState,
    response_message: normalizeText(item?.response_message),
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
    chat_draft: normalizeText(item?.chat_draft),
    chat_turn_id: normalizeText(item?.chat_turn_id),
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
  const currentState = normalizeText(current?.state) || "pending";
  const incomingState = normalizedIncoming.state;
  const currentResponse = normalizeText(current?.response_message);
  let responseMessage = normalizedIncoming.response_message;
  if (incomingState === "pending") {
    responseMessage = currentResponse || responseMessage;
  } else if (incomingState === currentState && !responseMessage) {
    responseMessage = currentResponse;
  }
  return {
    ...normalizedIncoming,
    state: incomingState !== "pending" ? incomingState : currentState,
    response_message: responseMessage,
    chat_reply: normalizeText(current?.chat_reply) || normalizedIncoming.chat_reply,
    composer_open: Boolean(current?.composer_open),
    chat_draft: normalizeText(current?.chat_draft),
    turns: Array.isArray(current?.turns) && current.turns.length > 0
      ? current.turns
      : normalizedIncoming.turns,
    chat_turn_id: normalizeText(current?.chat_turn_id) || normalizedIncoming.chat_turn_id,
  };
}

export function getDelightUiState(delight, { highlightBvid = "" } = {}) {
  const normalized = normalizeDelightCandidate(delight);
  if (!normalized.bvid) {
    return {
      visible: false,
      highlighted: false,
      handled: false,
      show_status: false,
      show_actions: false,
      like_pressed: false,
      like_disabled: false,
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
  const base = {
    visible: true,
    highlighted: highlight,
    handled: false,
    show_status: Boolean(normalized.response_message),
    show_actions: true,
    like_pressed: false,
    like_disabled: false,
    score_label: scoreLabel,
    response_tone: "info",
    response_message: normalized.response_message,
  };

  if (normalized.state === "viewed") {
    return {
      ...base,
      handled: true,
      show_status: true,
      show_actions: false,
      like_disabled: true,
      response_tone: "success",
      response_message:
        normalized.response_message || "已打开，阿B 会把这次点击当成强信号。",
    };
  }

  if (normalized.state === "liked") {
    return {
      ...base,
      show_status: true,
      like_pressed: true,
      like_disabled: true,
      response_tone: "success",
      response_message: normalized.response_message || "好，这类多来点。",
    };
  }

  if (normalized.state === "rejected") {
    return {
      ...base,
      handled: true,
      show_status: true,
      show_actions: false,
      like_disabled: true,
      response_message:
        normalized.response_message || "记下了，这类惊喜先少来点。",
    };
  }

  if (normalized.state === "chatted") {
    return {
      ...base,
      show_status: true,
      response_message:
        normalized.response_message || "这句已经记下，后面会更会试探。",
    };
  }

  return base;
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

function normalizeSpeculativeItems(items) {
  return Array.isArray(items)
    ? items
        .filter((item) => item?.domain)
        .map((item) => {
          const sourceMode = normalizeText(item.source_mode);
          const probeMode = normalizeText(item.probe_mode);
          return {
            domain: normalizeText(item.domain),
            reason: normalizeText(item.reason),
            ...(sourceMode ? { source_mode: sourceMode } : {}),
            ...(probeMode ? { probe_mode: probeMode } : {}),
            ...(item.challenge ? { challenge: true } : {}),
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
          };
        })
    : [];
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
    speculative_interests: normalizeSpeculativeItems(summary?.speculative_interests),
    speculative_avoidances: normalizeSpeculativeItems(summary?.speculative_avoidances),
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
    auto_update_enabled: Boolean(status?.auto_update_enabled),
    current_version: normalizeText(status?.current_version),
    latest_remote_version: normalizeText(status?.latest_remote_version),
    last_update_check_at: normalizeText(status?.last_update_check_at),
    last_update_error: normalizeText(status?.last_update_error),
    backend_update_state: normalizeText(status?.backend_update_state) || "unknown",
    backend_update_reason: normalizeText(status?.backend_update_reason) || "none",
  };
}

export function mergeRuntimeStatusEvent(status, event) {
  const runtime = normalizeRuntimeStatus(status);
  const next = {
    ...runtime,
  };
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
    // A canonical pool snapshot is emitted only after the backend runtime is
    // initialized.  Let this authoritative stream event recover a first-load
    // /api/runtime-status timeout instead of keeping real inventory hidden as
    // an uninitialized zero.  Mobile Web follows the same contract.
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
        ? runtime.recent_pool_topics.join(" / ")
        : runtime.last_discovered_count > 0
          ? "但可立即换的库存还没变"
        : runtime.pool_pending_count > 0
          ? "素材已抓到，会按可换库存缺口整理"
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
  if (summary.replenished === "内容发现未完成") return summary;
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

export function getManualRefreshResultHint({
  itemCount = 0,
  hadAdvertisedInventory = false,
  preservedCurrent = false,
} = {}) {
  const count = Number(itemCount || 0);
  if (count > 0) {
    return {
      message: "先给你换了一批新的，后台还在继续补货。",
      tone: "success",
    };
  }
  if (preservedCurrent) {
    return {
      message: "这次没换出新内容，当前推荐已保留。",
      tone: "info",
    };
  }
  if (hadAdvertisedInventory) {
    return {
      message: "库存还在，但这批暂时没有可用新内容，稍后再试。",
      tone: "info",
    };
  }
  return {
    message: "池子里这会儿还没刷出新的，稍后再试。",
    tone: "error",
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
    // A degraded backend answers every business route with a 503 envelope
    // ({status:"degraded", issues:[...]}) that requestJson preserves on
    // error.details. Lumping it into the generic error copy ("接口这会儿没回")
    // hides the only actionable fact — the LLM config is broken and the
    // settings panel can repair it — so surface it as its own state.
    const details = typeof error === "object" && error !== null ? error.details : null;
    if (details && typeof details === "object" && details.status === "degraded") {
      const issueMessages = (Array.isArray(details.issues) ? details.issues : [])
        .map((issue) => normalizeText(issue?.message))
        .filter(Boolean);
      const degradedMessage =
        issueMessages.join("；") || "后端的 AI 服务配置有问题，修复并保存后会立即恢复。";
      // A degraded backend that was NEVER initialized should still land the
      // user in the guided-init journey — its first step IS configuring the
      // LLM provider, and the init checklist surfaces the degraded blocker
      // from /api/init-status (allow-listed while degraded). Reserve the pure
      // repair state for an initialized backend that degraded later.
      // /api/runtime-status is also allow-listed, so the snapshot is available
      // here; without it we cannot rule out an initialized backend and fall
      // through to the repair state.
      const degradedRuntime = runtimeStatus == null ? null : normalizeRuntimeStatus(runtimeStatus);
      const neverInitialized =
        degradedRuntime !== null &&
        !degradedRuntime.initialized &&
        degradedRuntime.recommendation_count === 0 &&
        degradedRuntime.pool_available_count === 0 &&
        degradedRuntime.pool_pending_count === 0 &&
        degradedRuntime.last_replenished_count === 0 &&
        degradedRuntime.last_discovered_count === 0;
      if (neverInitialized) {
        return {
          kind: "uninitialized",
          degraded: true,
          message: degradedMessage,
          items: [],
        };
      }
      return {
        kind: "degraded",
        message: degradedMessage,
        items: [],
      };
    }
    return {
      kind: "error",
      message: "推荐暂时没刷出来，稍后再试",
      items: [],
    };
  }

  const normalizedItems = items.map(normalizeRecommendation);
  if (normalizedItems.length === 0 && runtimeStatus == null) {
    // Backend online but the runtime snapshot is unavailable: we cannot tell
    // "never initialized" apart from "initialized with a drained pool plus a
    // transient /runtime-status failure". Claiming uninitialized here would
    // flash the init CTA at a healthy backend, so render a transient degraded
    // state instead — pollers / runtime-stream reclassify on the next pass,
    // and a genuinely uninitialized backend still gets the toolbar badge from
    // the service worker's own runtime-status check.
    return {
      kind: "error",
      message: "后端状态暂时没读到，稍后自动重试。",
      items: [],
    };
  }
  const runtime = normalizeRuntimeStatus(runtimeStatus);
  const hasPostInitRuntimeSignals =
    runtime.recommendation_count > 0 ||
    runtime.pool_available_count > 0 ||
    runtime.pool_pending_count > 0 ||
    runtime.last_replenished_count > 0 ||
    runtime.last_discovered_count > 0;

  if (normalizedItems.length === 0) {
    const refreshMessage =
      runtime.manual_refresh_message || "正在根据你最近的新行为补货，再刷一会儿就会更新。";

    // Genuinely uninitialized (no profile, empty pool/recommendations) ALWAYS
    // shows the guided-init entry, regardless of any refresh state. A refresh
    // can't produce anything without a profile (refresh_if_needed returns
    // not_initialized), so manual_refresh_state="running" / pending behavior
    // signals must NOT mask the uninitialized state — that would hide the only
    // in-UI way to start init and leave the popup stuck on "补货" forever
    // (gui-init: live Windows testing). The init panel itself shows the CTA vs.
    // live progress based on /api/init-status, so an in-flight run still renders
    // correctly under this kind.
    if (!runtime.initialized && !hasPostInitRuntimeSignals) {
      return {
        kind: "uninitialized",
        // Button-driven copy, consistent with the rendered card in popup.js:
        // guided init runs from the「开始初始化」button, not a CLI command.
        message: "点「开始初始化」，会先检查前置条件，再依次保存完整画像并基于它生成首轮可用推荐。",
        items: [],
      };
    }

    // Queued behavior is context, not evidence that a worker is running.
    if (runtime.manual_refresh_state === "running") {
      return { kind: "refreshing", message: refreshMessage, items: [] };
    }

    const discoveryFailure = runtime.manual_refresh_state === "failed"
      ? runtime.manual_refresh_message || "这次内容发现未完成，请检查来源连接后重试内容发现。"
      : runtime.discovery_failure_message;
    if (discoveryFailure) {
      return { kind: "discovery_failed", message: discoveryFailure, items: [] };
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
