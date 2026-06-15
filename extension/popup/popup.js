import {
  buildStaleProbeResponseState,
  buildImageProxyPath,
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildContentUrl,
  buildRecommendationClickPayload,
  buildVideoUrl,
  formatRelativeTimestamp,
  getCommentSubmitUiState,
  getCognitionHistoryUiState,
  getConnectionBadgeState,
  getDelightUiState,
  getDisplayedPoolStatusSummary,
  getNextExpandedCognitionIndex,
  getManualRefreshResultHint,
  getReadyRecommendationHint,
  getRecommendationCardKind,
  getHintBannerState,
  getRuntimeRefreshSubmissionState,
  getPopupState,
  getSubmissionProgressMessage,
  getTabButtonState,
  mergeRuntimeStatusEvent,
  mergeDelightCandidate,
  normalizeActivityFeed,
  normalizeProbeType,
  normalizeRuntimeStatus,
  normalizeProfileSummary,
  probeMessageKey,
  shouldDisplayProbeFromWebSocket,
  shouldHydrateProbe,
  shouldAutoLoadRecommendations,
  shouldFetchProfileSummary,
  shouldSubmitChatOnEnter,
  validateCommentInput,
} from "./popup-helpers.js";
import { createRuntimeStreamClient } from "./popup-stream.js";
import {
  buildInitChecklist,
  describeInitReason,
  describeInitStartError,
  initProgressView,
  INIT_SOURCE_OPTIONS,
  INIT_SOURCE_LOGIN_HINT,
  initSourceLabels,
  initSelectedSourcesNeedingEnable,
} from "./popup-init-control.js";
import {
  getBackendBaseUrl,
  getBackendEndpointConfig,
  getBackendOrigin,
  isValidBackendHost,
  isValidBackendPort,
  updateBackendEndpoint,
} from "./popup-backend-config.js";
import { initAuthControl } from "./popup-auth-control.js";
import { initAutostartControl } from "./popup-autostart-control.js";
import {
  createQrSvgMarkup,
  getMobileQrViewState,
  isLoopbackMobileHost,
} from "./popup-qr.js";
import { createSavedToggleRegistry } from "./popup-saved-sync.js";
import {
  installEmbeddingBannerAutoRefresh,
  shouldShowEmbeddingBanner,
} from "./popup-embedding-banner.js";
import {
  appendRecommendations,
  checkBackendStatus,
  fetchActivityFeed,
  fetchUpdateStatus,
  checkBackendUpdate,
  applyBackendUpdate,
  fetchChatTurn,
  fetchChatTurns,
  fetchConfig,
  fetchHealth,
  fetchInitStatus,
  fetchPendingDelight,
  fetchPendingDelightBatch,
  fetchProfileSummary,
  fetchRecommendations,
  fetchRuntimeStatus,
  fetchSourceShareSuggestion,
  fetchSourcesStatus,
  markDelightSent,
  probeConfigService,
  startInit,
  readCachedConfigSnapshot,
  reportRecommendationClick,
  reshuffleRecommendations,
  refreshRecommendations,
  respondToAvoidanceProbe,
  respondToDelight,
  respondToInterestProbe,
  fetchEditState,
  submitProfileEdit,
  startChatTurn,
  submitFeedback,
  submitInsightFeedback,
  updateConfig,
  addToWatchLater,
  removeFromWatchLater,
  watchLaterStatus,
  fetchWatchLater,
  addToFavorite,
  removeFromFavorite,
  favoriteStatus,
  fetchFavorites,
} from "./popup-api.js";

const state = {
  activeTab: "recommend",
  online: false,
  recommendations: [],
  loadingMore: false,
  hasMoreRecommendations: true,
  profile: null,
  profileLoaded: false,
  profileCognitionHistory: {
    items: [],
    hasMore: false,
    nextCursor: "",
    loadingMore: false,
    loadMoreError: "",
  },
  expandedCognitionIndex: null,
  runtimeStatus: null,
  runtimeEvent: null,
  runtimeConfig: null,
  backendUpdateStatus: null,
  activityFeed: null,
  activityExpanded: false,
  activityLoadingMore: false,
  // Queue of pending delight recommendations. Banner shows
  // queue[delightCurrentIndex] with ‹/› navigation between siblings.
  // User actions (看看 / 不感兴趣 / × / 聊一聊 完成) remove the
  // current item; the index then clamps to the new length.
  // ``activeDelight`` is kept as a synced alias of the current item for
  // helpers like mergeDelightCandidate.
  activeDelights: [],
  delightCurrentIndex: 0,
  activeDelight: null,
  delightHighlightBvid: "",
  dismissedDelightBvids: [],
  activeFeedbackProgress: null,
  refreshStatusMessage: "",
  pendingProbe: null,
  pendingAvoidanceProbe: null,
  handledProbeKeys: new Set(),
  messages: [],
};

let backendUpdateStatusRefresh = null;

const RUNTIME_REFRESH_DEBOUNCE_MS = 1000;
let recommendationsRefreshTimer = null;
let recommendationsRefreshInFlight = false;
let recommendationsRefreshPending = false;
let manualRefreshInFlight = false;
let activityFeedRefreshTimer = null;
let activityFeedRefreshInFlight = false;
let activityFeedRefreshPending = false;
let hasRuntimeStreamConnected = false;

const elements = {
  content: document.querySelector(".content"),
  statusBadge: document.getElementById("statusBadge"),
  statusDot: document.getElementById("statusDot"),
  statusLabel: document.getElementById("statusLabel"),
  footer: document.getElementById("footerHintBar"),
  hintText: document.getElementById("hintText"),
  headlineText: document.getElementById("headlineText"),
  activityToggleButton: document.getElementById("activityToggleButton"),
  activityHistory: document.getElementById("activityHistory"),
  emptyState: document.getElementById("emptyState"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyText: document.getElementById("emptyText"),
  initPanel: document.getElementById("initPanel"),
  initSources: document.getElementById("initSources"),
  initChecklist: document.getElementById("initChecklist"),
  initProgress: document.getElementById("initProgress"),
  initProgressBar: document.getElementById("initProgressBar"),
  initProgressLabel: document.getElementById("initProgressLabel"),
  initStartBtn: document.getElementById("initStartBtn"),
  initStartReason: document.getElementById("initStartReason"),
  list: document.getElementById("recommendationList"),
  refreshRecommendationsButton: document.getElementById("refreshRecommendationsButton"),
  poolStatus: document.getElementById("poolStatus"),
  poolAvailable: document.getElementById("poolAvailable"),
  poolReplenished: document.getElementById("poolReplenished"),
  poolTopics: document.getElementById("poolTopics"),
  delightSlot: document.getElementById("delightSlot"),
  tabRecommend: document.getElementById("tabRecommend"),
  tabWatchLater: document.getElementById("tabWatchLater"),
  tabFavorites: document.getElementById("tabFavorites"),
  tabProfile: document.getElementById("tabProfile"),
  tabChat: document.getElementById("tabChat"),
  viewRecommend: document.getElementById("viewRecommend"),
  viewWatchLater: document.getElementById("viewWatchLater"),
  viewFavorites: document.getElementById("viewFavorites"),
  viewProfile: document.getElementById("viewProfile"),
  viewChat: document.getElementById("viewChat"),
  watchLaterList: document.getElementById("watchLaterList"),
  watchLaterEmpty: document.getElementById("watchLaterEmpty"),
  favoritesList: document.getElementById("favoritesList"),
  favoritesEmpty: document.getElementById("favoritesEmpty"),
  profileEmpty: document.getElementById("profileEmpty"),
  profileEmptyTitle: document.getElementById("profileEmptyTitle"),
  profileEmptyText: document.getElementById("profileEmptyText"),
  profileCard: document.getElementById("profileCard"),
  profileEditBar: document.getElementById("profileEditBar"),
  profileEditToggle: document.getElementById("profileEditToggle"),
  profileEditHint: document.getElementById("profileEditHint"),
  profileEditPanel: document.getElementById("profileEditPanel"),
  profilePortrait: document.getElementById("profilePortrait"),
  profileTraits: document.getElementById("profileTraits"),
  profileNeeds: document.getElementById("profileNeeds"),
  profileMBTI: document.getElementById("profileMBTI"),
  profileValues: document.getElementById("profileValues"),
  profileMotivationalDrivers: document.getElementById("profileMotivationalDrivers"),
  profileLikes: document.getElementById("profileLikes"),
  profileDislikes: document.getElementById("profileDislikes"),
  profileFavoriteUps: document.getElementById("profileFavoriteUps"),
  profileLifeStage: document.getElementById("profileLifeStage"),
  profileCurrentPhase: document.getElementById("profileCurrentPhase"),
  profileCognitiveStyle: document.getElementById("profileCognitiveStyle"),
  profileStyle: document.getElementById("profileStyle"),
  profileContext: document.getElementById("profileContext"),
  profileExplorationOpenness: document.getElementById("profileExplorationOpenness"),
  profileSpeculativeInterests: document.getElementById("profileSpeculativeInterests"),
  profileSpeculativeAvoidances: document.getElementById("profileSpeculativeAvoidances"),
  profileRecentMemory: document.getElementById("profileRecentMemory"),
  profileRecentMemoryStatus: document.getElementById("profileRecentMemoryStatus"),
  profileRecentMemoryMore: document.getElementById("profileRecentMemoryMore"),
  profileActiveInsights: document.getElementById("profileActiveInsights"),
  profileRecentAwareness: document.getElementById("profileRecentAwareness"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  chatSendButton: document.getElementById("chatSendButton"),
  chatStatus: document.getElementById("chatStatus"),
  openWebButton: document.getElementById("openWebButton"),
  starButton: document.getElementById("starButton"),
  starCount: document.getElementById("starCount"),
  mobileQrButton: document.getElementById("mobileQrButton"),
  mobileQrOverlay: document.getElementById("mobileQrOverlay"),
  mobileQrBack: document.getElementById("mobileQrBack"),
  mobileQrCode: document.getElementById("mobileQrCode"),
  mobileQrUrl: document.getElementById("mobileQrUrl"),
  mobileQrHint: document.getElementById("mobileQrHint"),
  mobileQrCopy: document.getElementById("mobileQrCopy"),
  mobileQrOpen: document.getElementById("mobileQrOpen"),
  messagesButton: document.getElementById("messagesButton"),
  messageBadge: document.getElementById("messageBadge"),
  messagesOverlay: document.getElementById("messagesOverlay"),
  messagesBack: document.getElementById("messagesBack"),
  messagesList: document.getElementById("messagesList"),
};

async function setProxyImageSrc(image, coverUrl) {
  const path = buildImageProxyPath(coverUrl);
  if (!path) return false;
  const origin = await getBackendOrigin();
  image.src = `${origin}${path}`;
  return true;
}

// Warm the browser cache for a batch of cover images BEFORE their cards are
// inserted into the DOM. Without this, appended (load-more) cards paint their
// near-white gradient placeholder while the cover is still downloading — the
// "白一下再出来" flash. Pre-decoding here means the <img> in each card hits a
// warm cache and paints on the first frame. Resolves on a timeout so one slow
// cover can't stall the whole batch (the rest keep warming in the background).
async function preloadCoverImages(items, { timeoutMs = 4000 } = {}) {
  const origin = await getBackendOrigin();
  const loaders = (Array.isArray(items) ? items : [])
    .map((item) => {
      const path = item?.cover_url ? buildImageProxyPath(item.cover_url) : null;
      if (!path) return null;
      return new Promise((resolve) => {
        const img = new Image();
        img.decoding = "async";
        img.addEventListener("load", () => resolve(), { once: true });
        img.addEventListener("error", () => resolve(), { once: true });
        img.src = `${origin}${path}`;
      });
    })
    .filter(Boolean);
  if (loaders.length === 0) return;
  const timeout = new Promise((resolve) => setTimeout(resolve, timeoutMs));
  await Promise.race([Promise.allSettled(loaders), timeout]);
}

let recommendationLoadCheckTimer = null;
let recommendationAutoLoadUserArmed = false;
let recommendationAutoLoadTouchY = null;
let recommendationAutoLoadIntentInitialized = false;
let runtimeStreamClient = null;
const CHAT_SESSION = "popup";
const CHAT_POLL_INTERVAL_MS = 1200;
const CHAT_POLL_DEADLINE_MS = 180_000;
const activeChatPolls = new Map();
const watchLaterToggles = createSavedToggleRegistry({
  labels: {
    checkedTitle: "取消稍后再看",
    uncheckedTitle: "稍后再看",
    checkedAriaLabel: "取消稍后再看",
    uncheckedAriaLabel: "稍后再看",
  },
});
const favoriteToggles = createSavedToggleRegistry({
  labels: {
    checkedTitle: "取消收藏",
    uncheckedTitle: "收藏",
    checkedAriaLabel: "取消收藏",
    uncheckedAriaLabel: "收藏",
  },
});

const WATCH_LATER_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
const FAVORITE_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';

const CHAT_PLACEHOLDERS = [
  // 想法与内容判断类
  "比如：我喜欢慢慢讲清楚的长视频，讨厌标题党；最近总想看能帮我理清问题的内容。",
  "说说你怎么看内容：我想看有观点、有证据的分析，不太想刷纯情绪输出。",
  "说说你怎么看内容：我喜欢创作者把过程讲明白，哪怕节奏慢一点也没关系。",
  // 观看行为类
  "比如：我最近老点开国际新闻和商业分析，想知道自己到底在找什么。",
  "比如：最近迷上了做饭视频，但每次都只看不动手。",
  "比如：一到深夜就开始刷纪录片，越冷门越上头。",
  "比如：我连着看了十几个测评视频，但最后什么也没买。",
  "比如：最近总是搜同一个UP主，可能是因为声音好听？",
  "比如：这周突然开始看健身视频了，也不知道能坚持多久。",
  "比如：我经常刷到一半就退出去了，好像注意力很难集中。",
  "比如：最近看了好多怀旧动画剪辑，可能是想回到小时候吧。",
  // 自我描述类
  "聊聊你自己：我是个容易三分钟热度的人，什么都想试但很难坚持。",
  "聊聊你自己：我算是个i人，喜欢一个人安静看东西，不太爱凑热闹。",
  "聊聊你自己：我对画面和音乐特别敏感，好看的封面就忍不住点进去。",
  // 喜好与厌恶类
  "聊聊喜好：我喜欢有深度的长视频，受不了标题党和故意搞悬念的。",
  "聊聊喜好：我讨厌那种假装真实的摆拍日常，一眼就能看出来。",
  "聊聊喜好：我偏爱小众冷门内容，热门排行榜上的反而不太想看。",
  // 近期状态类
  "最近在想：换工作的事情想了很久，刷视频可能就是在逃避。",
  "最近在想：马上要考试了，但就是控制不住打开B站。",
  "最近的状态：这阵子心情一般，老看一些治愈系的东西。",
  "最近在做：在学一门新技能，想看看有没有靠谱的教程。",
];
let chatPlaceholderIndex = 0;
let chatPlaceholderTimer = null;
let currentMobileWebUrl = "";

function setRefreshButtonState(loading, message = "") {
  state.refreshStatusMessage = message;
  if (elements.refreshRecommendationsButton instanceof HTMLButtonElement) {
    elements.refreshRecommendationsButton.disabled = loading;
    elements.refreshRecommendationsButton.textContent = loading ? "正在换一批…" : "换一批";
  }
  renderPoolStatus(state.runtimeStatus);
}

function setHint(message, tone = "info") {
  if (state.activityFeed == null) {
    state.activityFeed = normalizeActivityFeed({
      live_summary: message,
      headline: "",
      items: [],
    });
  } else {
    state.activityFeed.live_summary = message;
  }
  if (elements.footer instanceof HTMLElement) {
    elements.footer.dataset.tone = getHintBannerState(tone).tone;
  }
  renderActivityCard();
}

function setStatus(online) {
  if (
    !(elements.statusBadge instanceof HTMLElement) ||
    !(elements.statusDot instanceof HTMLElement) ||
    !(elements.statusLabel instanceof HTMLElement)
  ) {
    return;
  }
  const badgeState = getConnectionBadgeState(online);
  elements.statusBadge.dataset.tone = badgeState.tone;
  elements.statusDot.classList.toggle("offline", badgeState.tone === "offline");
  elements.statusLabel.textContent = badgeState.label;
}

function renderRuntimeToggles(config = state.runtimeConfig) {
  const scheduler = config?.scheduler || {};
  const pauseLlm = scheduler.enabled === false;
  const pauseOnDisconnect = scheduler.pause_on_extension_disconnect === true;

  const schedEnabled = document.getElementById("cfgSchedulerEnabled");
  if (schedEnabled instanceof HTMLInputElement) {
    schedEnabled.checked = pauseLlm;
  }
  const pauseDisconnect = document.getElementById("cfgPauseOnDisconnect");
  if (pauseDisconnect instanceof HTMLInputElement) {
    pauseDisconnect.checked = pauseOnDisconnect;
  }
}

function applyRuntimeConfig(config) {
  if (!config) return;
  state.runtimeConfig = config;
  renderRuntimeToggles(config);
}


function queueRecommendationLoadCheck() {
  if (recommendationLoadCheckTimer !== null) {
    return;
  }
  recommendationLoadCheckTimer = window.setTimeout(() => {
    recommendationLoadCheckTimer = null;
    maybeLoadMoreRecommendations();
  }, 0);
}

function resetRecommendationAutoLoadIntent() {
  recommendationAutoLoadUserArmed = false;
  recommendationAutoLoadTouchY = null;
}

function armRecommendationAutoLoadIntent() {
  if (state.activeTab === "recommend") {
    recommendationAutoLoadUserArmed = true;
  }
}

function initRecommendationAutoLoadIntent() {
  if (recommendationAutoLoadIntentInitialized) {
    return;
  }
  recommendationAutoLoadIntentInitialized = true;

  if (elements.content instanceof HTMLElement) {
    elements.content.addEventListener(
      "wheel",
      (event) => {
        if (event.deltaY > 0) {
          armRecommendationAutoLoadIntent();
        }
      },
      { passive: true },
    );
    elements.content.addEventListener(
      "touchstart",
      (event) => {
        recommendationAutoLoadTouchY = event.touches?.[0]?.clientY ?? null;
      },
      { passive: true },
    );
    elements.content.addEventListener(
      "touchmove",
      (event) => {
        const nextY = event.touches?.[0]?.clientY ?? null;
        if (
          recommendationAutoLoadTouchY !== null &&
          nextY !== null &&
          recommendationAutoLoadTouchY - nextY > 12
        ) {
          armRecommendationAutoLoadIntent();
        }
        recommendationAutoLoadTouchY = nextY;
      },
      { passive: true },
    );
  }

  window.addEventListener("keydown", (event) => {
    if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
      armRecommendationAutoLoadIntent();
    }
  });
}

function setActiveTab(tabName) {
  state.activeTab = tabName;

  const tabs = [
    ["recommend", elements.tabRecommend, elements.viewRecommend],
    ["watchLater", elements.tabWatchLater, elements.viewWatchLater],
    ["favorites", elements.tabFavorites, elements.viewFavorites],
    ["profile", elements.tabProfile, elements.viewProfile],
    ["chat", elements.tabChat, elements.viewChat],
  ];

  for (const [name, tabButton, view] of tabs) {
    if (!(tabButton instanceof HTMLButtonElement) || !(view instanceof HTMLElement)) {
      continue;
    }
    const tabState = getTabButtonState(tabName, name);
    tabButton.classList.toggle("is-active", tabState.selected);
    tabButton.setAttribute("aria-selected", String(tabState.selected));
    tabButton.tabIndex = tabState.tabIndex;
    view.hidden = !tabState.selected;
  }

  if (tabName === "profile") {
    void loadProfileSummary();
  }
  if (tabName === "recommend") {
    queueRecommendationLoadCheck();
  }
  if (tabName === "watchLater") {
    void loadWatchLater();
  }
  if (tabName === "favorites") {
    void loadFavorites();
  }
}

async function toggleWatchLaterSaved(bvid) {
  return watchLaterToggles.toggle(bvid, {
    add: addToWatchLater,
    remove: removeFromWatchLater,
  });
}

async function toggleFavoriteSaved(bvid) {
  return favoriteToggles.toggle(bvid, {
    add: addToFavorite,
    remove: removeFromFavorite,
  });
}

function bindWatchLaterToggle(button, bvid, labels = {}) {
  watchLaterToggles.registerButton(bvid, button, labels);
  void watchLaterToggles.hydrateStatus(bvid, watchLaterStatus);
  return button;
}

function bindFavoriteToggle(button, bvid, labels = {}) {
  favoriteToggles.registerButton(bvid, button, labels);
  void favoriteToggles.hydrateStatus(bvid, favoriteStatus);
  return button;
}

// ── Watch-later view (稍后再看) ──────────────────────────────────
async function loadWatchLater() {
  const list = elements.watchLaterList;
  const empty = elements.watchLaterEmpty;
  if (!(list instanceof HTMLElement)) return;
  let data = null;
  try {
    data = await fetchWatchLater(100, 0);
  } catch {
    data = null;
  }
  const items = Array.isArray(data?.items) ? data.items : [];
  list.replaceChildren();
  if (!items.length) {
    if (empty instanceof HTMLElement) empty.hidden = false;
    return;
  }
  if (empty instanceof HTMLElement) empty.hidden = true;
  for (const item of items) {
    watchLaterToggles.setSaved(item.bvid, true);
    list.appendChild(buildWatchLaterCard(item));
  }
}

// Optimistic saved-card removal shared by the watch-later and favorites
// views. The card disappears on click; if the DELETE then fails the card is
// restored in place and the button flips to "重试". The previous code waited
// for the response before touching the DOM and swallowed errors silently —
// whenever the DELETE queued behind slow same-origin requests (covers via
// image-proxy compete for Chrome's 6-connection limit) or failed, clicking
// looked like it did nothing.
function bindSavedCardRemove(card, remove, { bvid, requestRemove, toggles, list, empty }) {
  remove.addEventListener("click", async () => {
    if (remove.disabled) return;
    remove.disabled = true;
    const anchor = card.nextElementSibling;
    card.remove();
    if (empty instanceof HTMLElement && !list?.children.length) {
      empty.hidden = false;
    }
    try {
      await requestRemove(bvid);
      toggles.setSaved(bvid, false);
    } catch (error) {
      console.error("saved-card remove failed:", bvid, error);
      if (list instanceof HTMLElement) {
        list.insertBefore(card, anchor?.parentElement === list ? anchor : null);
      }
      if (empty instanceof HTMLElement) empty.hidden = true;
      remove.disabled = false;
      remove.textContent = "重试";
      remove.title = "刚才没移除成功，点一下重试";
    }
  });
}

function buildWatchLaterCard(item) {
  const card = document.createElement("article");
  card.className = "saved-card";
  card.dataset.bvid = item.bvid;

  const body = document.createElement("button");
  body.type = "button";
  body.className = "saved-card-open";
  const media = buildSavedCardMedia(item);
  const copy = document.createElement("span");
  copy.className = "saved-card-copy";
  const title = document.createElement("p");
  title.className = "saved-card-title";
  title.textContent = item.title || item.bvid;
  const up = document.createElement("p");
  up.className = "saved-card-up";
  up.textContent = item.up_name || "";
  copy.append(title, up);
  body.append(copy);
  body.prepend(media);
  body.addEventListener("click", () => {
    const url = buildContentUrl(item);
    if (url) window.open(url, "_blank");
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "saved-card-remove";
  remove.textContent = "移除";
  remove.title = "移出稍后再看";
  bindSavedCardRemove(card, remove, {
    bvid: item.bvid,
    requestRemove: removeFromWatchLater,
    toggles: watchLaterToggles,
    list: elements.watchLaterList,
    empty: elements.watchLaterEmpty,
  });

  card.append(body, remove);
  return card;
}

function buildSavedCardMedia(item) {
  const media = document.createElement("span");
  media.className = "saved-card-cover";
  if (item.cover_url) {
    const image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    media.append(image);
    void setProxyImageSrc(image, item.cover_url);
  } else {
    media.classList.add("is-fallback");
  }
  return media;
}

// ── Favorites view (收藏夹) ─────────────────────────────────────
async function loadFavorites() {
  const list = elements.favoritesList;
  const empty = elements.favoritesEmpty;
  if (!(list instanceof HTMLElement)) return;
  let data = null;
  try {
    data = await fetchFavorites(100, 0);
  } catch {
    data = null;
  }
  const items = Array.isArray(data?.items) ? data.items : [];
  list.replaceChildren();
  if (!items.length) {
    if (empty instanceof HTMLElement) empty.hidden = false;
    return;
  }
  if (empty instanceof HTMLElement) empty.hidden = true;
  for (const item of items) {
    favoriteToggles.setSaved(item.bvid, true);
    list.appendChild(buildFavoriteCard(item));
  }
}

function buildFavoriteCard(item) {
  const card = document.createElement("article");
  card.className = "saved-card";
  card.dataset.bvid = item.bvid;

  const body = document.createElement("button");
  body.type = "button";
  body.className = "saved-card-open";
  const media = buildSavedCardMedia(item);
  const copy = document.createElement("span");
  copy.className = "saved-card-copy";
  const title = document.createElement("p");
  title.className = "saved-card-title";
  title.textContent = item.title || item.bvid;
  const up = document.createElement("p");
  up.className = "saved-card-up";
  up.textContent = item.up_name || "";
  copy.append(title, up);
  body.append(copy);
  body.prepend(media);
  body.addEventListener("click", () => {
    const url = buildContentUrl(item);
    if (url) window.open(url, "_blank");
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "saved-card-remove";
  remove.textContent = "移除";
  remove.title = "取消收藏";
  bindSavedCardRemove(card, remove, {
    bvid: item.bvid,
    requestRemove: removeFromFavorite,
    toggles: favoriteToggles,
    list: elements.favoritesList,
    empty: elements.favoritesEmpty,
  });

  card.append(body, remove);
  return card;
}

function showRecommendationEmptyState(title, message) {
  if (
    !(elements.emptyState instanceof HTMLElement) ||
    !(elements.emptyTitle instanceof HTMLElement) ||
    !(elements.emptyText instanceof HTMLElement)
  ) {
    return;
  }
  elements.emptyState.hidden = false;
  elements.emptyTitle.textContent = title;
  elements.emptyText.textContent = message;
  // The guided-init panel is only for the uninitialized state; the
  // uninitialized branch re-shows it via renderInitPanelIdle().
  if (elements.initPanel instanceof HTMLElement) {
    elements.initPanel.hidden = true;
  }
}

function hideRecommendationEmptyState() {
  if (elements.emptyState instanceof HTMLElement) {
    elements.emptyState.hidden = true;
  }
  if (elements.initPanel instanceof HTMLElement) {
    elements.initPanel.hidden = true;
  }
  clearInitPolling();
}

// ── Guided init (gui-init F1) ──────────────────────────────────────────────
let initPollTimer = null;

function clearInitPolling() {
  if (initPollTimer != null) {
    clearTimeout(initPollTimer);
    initPollTimer = null;
  }
}

function _setInitStartButton(label, enabled) {
  if (!(elements.initStartBtn instanceof HTMLButtonElement)) {
    return;
  }
  elements.initStartBtn.textContent = label;
  elements.initStartBtn.disabled = !enabled;
  if (!elements.initStartBtn.dataset.bound) {
    elements.initStartBtn.dataset.bound = "1";
    elements.initStartBtn.addEventListener("click", () => {
      void handleStartInitClick();
    });
  }
}

function _setInitReason(text) {
  if (elements.initStartReason instanceof HTMLElement) {
    elements.initStartReason.textContent = text || "";
    elements.initStartReason.hidden = !text;
  }
}

function _renderInitChecklist(status, selected = null) {
  // Show the prereq checklist (red ✗ / green ✓ / soft •) — only surfaced AFTER a
  // click whose check failed, so the user sees exactly what to fix.
  if (!(elements.initChecklist instanceof HTMLElement)) {
    return;
  }
  elements.initChecklist.replaceChildren();
  for (const row of buildInitChecklist(status, selected)) {
    const li = document.createElement("li");
    li.className = `${row.ok ? "init-ok" : "init-missing"} ${row.hard ? "init-hard" : "init-soft"}`;
    const head = document.createElement("div");
    head.className = "init-row";
    const mark = document.createElement("span");
    mark.className = "init-mark";
    mark.textContent = row.ok ? "✓" : row.hard ? "✗" : "•";
    const label = document.createElement("span");
    label.textContent = row.label;
    head.append(mark, label);
    li.append(head);
    if (!row.ok && row.hint) {
      const hint = document.createElement("p");
      hint.className = "init-hint";
      hint.textContent = row.hint;
      li.append(hint);
    }
    elements.initChecklist.append(li);
  }
}

// Render the platform-source checkboxes (gui-init: per-run source selection).
// Bilibili is default-checked (recommended) but deselectable like the rest
// (v0.3.118+) — at least one source must stay checked. The list is static so
// the idle panel paints instantly — eligibility (config enabled + logged in)
// is validated on click, not via a slow upfront probe.
function _renderInitSources() {
  if (!(elements.initSources instanceof HTMLElement)) {
    return;
  }
  elements.initSources.replaceChildren();
  const title = document.createElement("p");
  title.className = "init-sources-title";
  title.textContent = "选择初始化数据来源（至少一个）";
  elements.initSources.append(title);
  for (const opt of INIT_SOURCE_OPTIONS) {
    const row = document.createElement("label");
    row.className = "init-source-row";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = opt.key;
    box.dataset.initSource = opt.key;
    box.checked = Boolean(opt.defaultChecked);
    const span = document.createElement("span");
    span.textContent = opt.defaultChecked ? `${opt.label}（推荐）` : opt.label;
    row.append(box, span);
    elements.initSources.append(row);
  }
  const hint = document.createElement("p");
  hint.className = "init-sources-hint";
  hint.textContent = INIT_SOURCE_LOGIN_HINT;
  elements.initSources.append(hint);
  elements.initSources.hidden = false;
}

// Read the currently-checked source keys.
function _readSelectedInitSources() {
  const selected = [];
  if (elements.initSources instanceof HTMLElement) {
    for (const box of elements.initSources.querySelectorAll("input[data-init-source]")) {
      if (box.checked) {
        selected.push(box.value);
      }
    }
  }
  return selected;
}

// Idle entry: source checkboxes + the actionable button + a one-line note.
// Conditions are checked ON CLICK (no slow upfront probe / blank panel);
// failures are surfaced only after a click that doesn't pass.
function renderInitPanelIdle() {
  if (!(elements.initPanel instanceof HTMLElement)) {
    return;
  }
  elements.initPanel.hidden = false;
  _renderInitSources();
  if (elements.initChecklist instanceof HTMLElement) {
    elements.initChecklist.replaceChildren();
    const li = document.createElement("li");
    li.className = "init-hint-row";
    li.textContent = "点「开始初始化」会先检查 AI 服务 / 向量模型，以及所选平台的登录状态，通过才开始。";
    elements.initChecklist.append(li);
  }
  if (elements.initProgress instanceof HTMLElement) {
    elements.initProgress.hidden = true;
  }
  _setInitStartButton("开始初始化", true);
  _setInitReason("");
}

function renderInitProgress(status) {
  if (!(elements.initPanel instanceof HTMLElement)) {
    return;
  }
  elements.initPanel.hidden = false;
  // Source selection is an idle-only affordance; hide it once a run is shown.
  if (elements.initSources instanceof HTMLElement) {
    elements.initSources.hidden = true;
  }
  if (elements.initChecklist instanceof HTMLElement) {
    elements.initChecklist.replaceChildren();
  }
  const progress = initProgressView(status);
  if (elements.initProgress instanceof HTMLElement) {
    elements.initProgress.hidden = false;
    if (elements.initProgressBar instanceof HTMLElement) {
      elements.initProgressBar.style.width = `${progress.pct}%`;
    }
    if (elements.initProgressLabel instanceof HTMLElement) {
      elements.initProgressLabel.textContent = progress.failed
        ? `初始化未完成：${describeInitReason(status && status.reason) || progress.failedReason || "请稍后重试"}`
        : progress.active
          ? `${progress.stageLabel || "正在初始化"}（${progress.pct}%）`
          : "初始化完成！";
    }
  }
  if (progress.active) {
    _setInitStartButton("初始化进行中…", false);
    _setInitReason("");
  } else if (progress.failed) {
    _setInitStartButton("重试初始化", true);
    _setInitReason("");
  } else {
    _setInitStartButton("已初始化", false);
    _setInitReason("");
  }
}

// Poll init-status while a run is in progress; on terminal, reload (success) or
// leave the failure reason on screen with the button re-enabled for a retry.
async function pollInitProgress() {
  let status = null;
  try {
    status = await fetchInitStatus();
  } catch {
    clearInitPolling();
    initPollTimer = setTimeout(() => {
      void pollInitProgress();
    }, 3000);
    return;
  }
  renderInitProgress(status);
  if (status.running) {
    clearInitPolling();
    initPollTimer = setTimeout(() => {
      void pollInitProgress();
    }, 3000);
    return;
  }
  clearInitPolling();
  if (status.initialized) {
    state.profileLoaded = false;
    setHint("初始化完成！正在加载画像和推荐…", "success");
    scheduleRecommendationsRefresh();
    void loadProfileSummary({ force: true });
  }
}

function _startInitProgressPoll() {
  clearInitPolling();
  initPollTimer = setTimeout(() => {
    void pollInitProgress();
  }, 1200);
}

// THE click handler: run the condition checks on demand. If anything fails,
// surface the checklist + reason and do NOT initialize; only start init when
// every condition passes (gui-init: user-requested click-driven gating).
async function handleStartInitClick() {
  // Snapshot the source selection BEFORE we replace the panel contents.
  const selectedSources = _readSelectedInitSources();
  if (selectedSources.length === 0) {
    _setInitStartButton("开始初始化", true);
    _setInitReason("至少勾选一个数据来源。");
    return;
  }
  _setInitStartButton("检查中…", false);
  _setInitReason("");
  if (elements.initChecklist instanceof HTMLElement) {
    elements.initChecklist.replaceChildren();
    const li = document.createElement("li");
    li.className = "init-checking";
    li.textContent = "正在检查 AI 服务 / 向量模型与所选平台登录（实时请求测试，可能要十几秒）…";
    elements.initChecklist.append(li);
  }

  let status = null;
  try {
    status = await fetchInitStatus();
  } catch {
    renderInitPanelIdle();
    _setInitReason("前置检查没拉到（后端可能在忙），稍后再点「开始初始化」。");
    return;
  }

  // Already running (double-click / a run started elsewhere) → show progress.
  if (status.running) {
    renderInitProgress(status);
    _startInitProgressPoll();
    return;
  }

  // The user checked a platform that isn't enabled in settings — the backend
  // would silently drop it, so guide them to enable it (or uncheck) instead.
  const needEnable = initSelectedSourcesNeedingEnable(selectedSources, status);
  if (needEnable.length > 0) {
    _renderInitChecklist(status, selectedSources);
    _setInitStartButton("开始初始化", true);
    _setInitReason(
      `你勾选了 ${initSourceLabels(needEnable).join("、")}，但还没在设置里开启；到设置开启对应平台，或取消勾选后再点一次。`,
    );
    return;
  }

  // B 站登录只在勾选了 B 站时才拦截（v0.3.118+：可取消勾选跳过 B 站）。
  if (
    selectedSources.includes("bilibili") &&
    !status?.prerequisites?.bilibili_logged_in
  ) {
    _renderInitChecklist(status, selectedSources);
    _setInitStartButton("开始初始化", true);
    _setInitReason("还没检测到 B 站登录。先登录 bilibili.com，或取消勾选 B 站再开始。");
    return;
  }

  // Conditions not met → show exactly what failed; do NOT initialize.
  if (!status.can_start) {
    _renderInitChecklist(status, selectedSources);
    _setInitStartButton("开始初始化", true);
    _setInitReason(
      describeInitReason(status.reason) || "以下条件未满足，无法开始初始化，补齐后再点一次。",
    );
    return;
  }

  // All conditions pass → start with the chosen sources. The backend
  // re-validates in its critical section, so a race can still 409 — surface
  // that and let the user retry.
  try {
    await startInit({ force: false, sources: selectedSources });
  } catch (error) {
    _renderInitChecklist(status, selectedSources);
    _setInitStartButton("开始初始化", true);
    _setInitReason(describeInitStartError(error));
    return;
  }
  setHint("初始化已开始，正在拉取数据…", "info");
  renderInitProgress({ running: true, current_stage: 1, total_stages: 4, stages: [] });
  _startInitProgressPoll();
}

function renderPoolStatus(runtimeStatus) {
  if (
    !(elements.poolStatus instanceof HTMLElement) ||
    !(elements.poolAvailable instanceof HTMLElement) ||
    !(elements.poolReplenished instanceof HTMLElement) ||
    !(elements.poolTopics instanceof HTMLElement)
  ) {
    return;
  }

  const summary = getDisplayedPoolStatusSummary(
    runtimeStatus,
    state.runtimeEvent,
    state.refreshStatusMessage,
  );
  if (summary == null) {
    elements.poolStatus.hidden = true;
    return;
  }

  elements.poolStatus.hidden = false;
  elements.poolAvailable.textContent = summary.available;
  elements.poolReplenished.textContent = summary.replenished;
  elements.poolTopics.textContent = summary.topics;
}

function rememberDismissedDelight(bvid) {
  if (!bvid) {
    return;
  }
  if (!state.dismissedDelightBvids.includes(bvid)) {
    state.dismissedDelightBvids = [...state.dismissedDelightBvids, bvid];
  }
  // Persist on the backend so popup reloads + future
  // /api/delight/pending-batch fetches honour the dismissal too.
  // Otherwise an in-memory dismiss is lost the moment the popup
  // closes, and the same bvid pops back up next time.
  markDelightSent(bvid).catch(() => {
    // Silent fail — the in-memory dismissal still works for this
    // session even if the network ack doesn't go through.
  });
}

// ── Delight queue helpers ──────────────────────────────────────────
// state.activeDelights is the queue, state.delightCurrentIndex is the
// pointer into it. state.activeDelight is a synced alias of the
// currently-shown item for helpers that operate on a single item.

function clampDelightIndex() {
  const len = state.activeDelights.length;
  if (len === 0) {
    state.delightCurrentIndex = 0;
    return;
  }
  if (state.delightCurrentIndex < 0) state.delightCurrentIndex = 0;
  if (state.delightCurrentIndex >= len) state.delightCurrentIndex = len - 1;
}

function syncDelightHead() {
  clampDelightIndex();
  state.activeDelight = state.activeDelights[state.delightCurrentIndex] ?? null;
}

function pushDelightCandidate(candidate) {
  if (!candidate || !candidate.bvid) return;
  if (state.dismissedDelightBvids.includes(candidate.bvid)) return;
  const existingIdx = state.activeDelights.findIndex(
    (d) => d?.bvid === candidate.bvid,
  );
  if (existingIdx >= 0) {
    state.activeDelights[existingIdx] = mergeDelightCandidate(
      state.activeDelights[existingIdx],
      candidate,
      state.dismissedDelightBvids,
    );
  } else {
    const merged = mergeDelightCandidate(
      null,
      candidate,
      state.dismissedDelightBvids,
    );
    if (merged) {
      state.activeDelights.push(merged);
    }
  }
  syncDelightHead();
}

// Remove the currently-shown delight from the queue. If user wasn't on
// the head, drop the item at the current index; the next item slides
// into its place. After a removal the index points to whatever now
// occupies that slot (or to length-1 if we just removed the last).
//
// Preserve the expanded state across removal so that × / 看看 / 喜欢
// / 不感兴趣 don't collapse the next item's body — once the user
// is in "browse with detail" mode, every queued item should keep
// showing its full reason+actions until the user explicitly collapses.
function removeCurrentDelight() {
  if (state.activeDelights.length === 0) return;
  const wasExpanded = Boolean(
    state.activeDelights[state.delightCurrentIndex]?.expanded,
  );
  state.activeDelights.splice(state.delightCurrentIndex, 1);
  // Keep the same index — it now points to the next item, or
  // clampDelightIndex() will pin it to the last when we removed the tail.
  if (wasExpanded && state.activeDelights[state.delightCurrentIndex]) {
    state.activeDelights[state.delightCurrentIndex] = {
      ...state.activeDelights[state.delightCurrentIndex],
      expanded: true,
    };
  }
  syncDelightHead();
}

// Backwards-compatible name used by some action handlers.
const shiftDelightQueue = removeCurrentDelight;

function navigateDelight(delta) {
  if (state.activeDelights.length <= 1) return;
  // Preserve the expand state across navigation: if the user had the
  // current banner expanded, the next one slides in already expanded
  // so they don't have to click open every card.
  const wasExpanded = Boolean(
    state.activeDelights[state.delightCurrentIndex]?.expanded,
  );
  state.delightCurrentIndex += delta;
  clampDelightIndex();
  if (wasExpanded && state.activeDelights[state.delightCurrentIndex]) {
    state.activeDelights[state.delightCurrentIndex] = {
      ...state.activeDelights[state.delightCurrentIndex],
      expanded: true,
    };
  }
  syncDelightHead();
}

function updateDelightHead(updates) {
  const idx = state.delightCurrentIndex;
  if (state.activeDelights.length === 0) return;
  state.activeDelights[idx] = { ...state.activeDelights[idx], ...updates };
  syncDelightHead();
  const bvid = state.activeDelights[idx]?.bvid;
  if (bvid) persistDelightLocalState(bvid, updates);
}

function clearDelightQueue() {
  state.activeDelights = [];
  state.delightCurrentIndex = 0;
  syncDelightHead();
}

function mergeIncomingDelight(candidate) {
  pushDelightCandidate(candidate);
  renderDelightSlot();
}

function getRuntimeEventTone(event) {
  const type = String(event?.type ?? "");
  if (type === "refresh.failed") {
    return "error";
  }
  if (type === "refresh.pool_updated" || type === "recommendation.reshuffled") {
    return "success";
  }
  return "info";
}

function scheduleRecommendationsRefresh({ delayMs = RUNTIME_REFRESH_DEBOUNCE_MS } = {}) {
  if (recommendationsRefreshTimer !== null) {
    window.clearTimeout(recommendationsRefreshTimer);
  }
  recommendationsRefreshTimer = window.setTimeout(() => {
    recommendationsRefreshTimer = null;
    void runScheduledRecommendationsRefresh();
  }, Math.max(0, delayMs));
}

async function runScheduledRecommendationsRefresh() {
  if (recommendationsRefreshInFlight) {
    recommendationsRefreshPending = true;
    return;
  }
  recommendationsRefreshInFlight = true;
  try {
    await initializeRecommendations();
  } finally {
    recommendationsRefreshInFlight = false;
    if (recommendationsRefreshPending) {
      recommendationsRefreshPending = false;
      scheduleRecommendationsRefresh();
    }
  }
}

function scheduleActivityFeedRefresh({ delayMs = RUNTIME_REFRESH_DEBOUNCE_MS } = {}) {
  if (activityFeedRefreshTimer !== null) {
    window.clearTimeout(activityFeedRefreshTimer);
  }
  activityFeedRefreshTimer = window.setTimeout(() => {
    activityFeedRefreshTimer = null;
    void runScheduledActivityFeedRefresh();
  }, Math.max(0, delayMs));
}

async function runScheduledActivityFeedRefresh() {
  if (activityFeedRefreshInFlight) {
    activityFeedRefreshPending = true;
    return;
  }
  activityFeedRefreshInFlight = true;
  try {
    await loadActivityFeed();
  } finally {
    activityFeedRefreshInFlight = false;
    if (activityFeedRefreshPending) {
      activityFeedRefreshPending = false;
      scheduleActivityFeedRefresh();
    }
  }
}

function isAvoidanceProbeType(type) {
  return normalizeProbeType(type) === "avoidance.probe";
}

function isChallengeProbe(probe) {
  const mode = String(probe?.probe_mode || "").toLowerCase();
  return Boolean(probe?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
}

function rememberHandledProbe(domain, type = "interest.probe") {
  const key = probeMessageKey(type, domain);
  if (key) {
    state.handledProbeKeys.add(key);
  }
  return key;
}

function forgetHandledProbe(domain, type = "interest.probe") {
  const key = probeMessageKey(type, domain);
  if (key) {
    state.handledProbeKeys.delete(key);
  }
}

function applyStaleProbeResponse(domain, type = "interest.probe") {
  const nextState = buildStaleProbeResponseState({
    messages: state.messages,
    pendingProbe: state.pendingProbe,
    pendingAvoidanceProbe: state.pendingAvoidanceProbe,
    domain,
    type,
  });
  if (nextState.handledKey) {
    state.handledProbeKeys.add(nextState.handledKey);
  }
  state.messages = nextState.messages;
  state.pendingProbe = nextState.pendingProbe;
  state.pendingAvoidanceProbe = nextState.pendingAvoidanceProbe;
  updateMessageBadge();
}

function addProbeMessage(event, type = event?.type) {
  if (!event?.domain) return;
  const normalizedType = normalizeProbeType(type);
  const key = probeMessageKey(normalizedType, event.domain);
  if (state.handledProbeKeys.has(key)) return;
  if (state.messages.some((m) => probeMessageKey(m?.type, m?.domain) === key)) return;
  state.messages.push({ ...event, type: normalizedType });
  updateMessageBadge();
}

function connectRuntimeStream() {
  // Disconnect any previous client first so a settings-page port change
  // doesn't leave a zombie WebSocket against the old origin.
  runtimeStreamClient?.disconnect?.();
  const client = createRuntimeStreamClient({
    onEvent(event) {
      state.runtimeEvent = event;
      state.runtimeStatus = mergeRuntimeStatusEvent(state.runtimeStatus, event);
      renderPoolStatus(state.runtimeStatus);
      if (event.type === "delight.candidate" && event.bvid) {
        mergeIncomingDelight(event);
      }
      state.activeFeedbackProgress?.handle?.(event);
      if (elements.footer instanceof HTMLElement) {
        elements.footer.dataset.tone = getHintBannerState(getRuntimeEventTone(event)).tone;
      }
      renderActivityCard();
      // Hot-reload: re-fetch all data when backend config is reloaded
      if (event.type === "config_reloaded") {
        setHint("后端配置已热重载，正在刷新数据…", "success");
        scheduleRecommendationsRefresh();
      }
      if (
        event.type === "backend_update_available" ||
        event.type === "backend_update_failed" ||
        event.type === "backend_restart_pending"
      ) {
        if (typeof backendUpdateStatusRefresh === "function") {
          void backendUpdateStatusRefresh();
        }
      }
      // Pool updates are already merged into runtimeStatus above. Keep the
      // current recommendation list intact so appended history is not replaced
      // by the latest top window from /api/recommendations.
      // Activity log got a new behavior event — refresh the activity feed
      // so the popup's "刚刚看了..." panel stays current without polling.
      if (event.type === "activity.added") {
        scheduleActivityFeedRefresh();
      }
      // Interest confirmed/rejected: refresh profile and show hint
      if (
        event.type === "interest.confirmed" ||
        event.type === "interest.rejected" ||
        event.type === "interest.chat" ||
        event.type === "avoidance.confirmed" ||
        event.type === "avoidance.rejected" ||
        event.type === "avoidance.chat"
      ) {
        setHint(String(event.message || ""), "success");
        void loadProfileSummary({ force: true });
      }
      // Probe events: add to messages inbox
      if (
        event.type === "interest.probe" &&
        shouldDisplayProbeFromWebSocket(event, "interest.probe", state.handledProbeKeys)
      ) {
        state.pendingProbe = event;
        addProbeMessage(event, "interest.probe");
        renderProbeCard();
      }
      if (
        event.type === "avoidance.probe" &&
        shouldDisplayProbeFromWebSocket(event, "avoidance.probe", state.handledProbeKeys)
      ) {
        state.pendingAvoidanceProbe = event;
        addProbeMessage(event, "avoidance.probe");
        void loadProfileSummary({ force: true });
      }
      // Delight candidates are shown in the delight tray, not in messages.
      // Delight refreshed: backend computed N new above-threshold delights
      // — re-fetch the full queue (no per-item chrome notification, no
      // banner pop). Just keeps popup in sync with backend without forcing
      // the user to reload the extension.
      if (event.type === "delight.refreshed") {
        void (async () => {
          try {
            const items = await fetchPendingDelightBatch();
            if (!Array.isArray(items)) return;
            clearDelightQueue();
            for (const item of items) {
              pushDelightCandidate(item);
            }
            renderDelightSlot();
          } catch {
            // Silently ignore — next reload or proactive push will heal
          }
        })();
      }
      // Delight feedback: show hint
      if (
        event.type === "delight.disliked" ||
        event.type === "delight.liked" ||
        event.type === "delight.chat"
      ) {
        setHint(String(event.message || ""), "success");
      }
      // Live guided-init progress (gui-init F1): drive the recommend-tab
      // progress bar from the run's stage events.
      if (event.type === "init_progress" || event.type === "init_failed") {
        void pollInitProgress();
      }
      // Init completed: re-fetch everything including profile
      if (event.type === "init_completed") {
        state.profileLoaded = false;
        setHint("初始化完成！正在加载画像和推荐…", "success");
        scheduleRecommendationsRefresh();
        void loadProfileSummary({ force: true });
      }
      // Profile changed elsewhere (cognition cycle, manual rebuild,
      // dialogue insight ingestion, …). Force a refetch so the panel
      // reflects the new portrait/needs/insights without requiring
      // a chat send or full init.
      if (event.type === "profile_updated") {
        state.profileLoaded = false;
        void loadProfileSummary({ force: true });
      }
    },
    onConnect() {
      if (!state.online) {
        state.online = true;
        setStatus(true);
        if (hasRuntimeStreamConnected) {
          setHint("后端重新连上了，正在刷新。", "success");
          scheduleRecommendationsRefresh({ delayMs: 0 });
        }
      }
      hasRuntimeStreamConnected = true;
    },
    onDisconnect() {
      if (state.online) {
        state.online = false;
        setStatus(false);
        setHint("后端连接断了，等重连上会自动恢复。", "error");
      }
    },
  });
  client.connect();
  runtimeStreamClient = client;
}

function renderActivityHistory(items) {
  if (!(elements.activityHistory instanceof HTMLElement)) {
    return;
  }
  elements.activityHistory.replaceChildren();
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "footer-item";

    const meta = document.createElement("div");
    meta.className = "footer-item-meta";

    const kind = document.createElement("span");
    kind.className = "footer-item-kind";
    kind.textContent = item.kind;

    const time = document.createElement("span");
    time.textContent = item.created_at || "刚刚";

    meta.append(kind, time);

    const summary = document.createElement("p");
    summary.className = "footer-item-summary";
    summary.textContent = item.summary;
    row.append(meta, summary);

    if (item.detail) {
      const detail = document.createElement("p");
      detail.className = "footer-item-detail";
      detail.textContent = item.detail;
      row.append(detail);
    }

    elements.activityHistory.append(row);
  }

  // Load-more affordance — only render when the backend says there
  // are older items beyond what we already have. Click appends the
  // next page in place; we re-render on completion so the button
  // either disappears or stays for further pages.
  if (state.activityFeed?.has_more && state.activityFeed?.next_cursor) {
    const loadMore = document.createElement("button");
    loadMore.type = "button";
    loadMore.className = "activity-load-more";
    loadMore.textContent = state.activityLoadingMore
      ? "加载中…"
      : "加载更早的动态";
    loadMore.disabled = Boolean(state.activityLoadingMore);
    loadMore.addEventListener("click", () => {
      void loadMoreActivity();
    });
    elements.activityHistory.append(loadMore);
  }
}

function renderActivityCard() {
  if (
    !(elements.hintText instanceof HTMLElement) ||
    !(elements.headlineText instanceof HTMLElement) ||
    !(elements.activityToggleButton instanceof HTMLButtonElement) ||
    !(elements.activityHistory instanceof HTMLElement)
  ) {
    return;
  }
  const card = getActivityCardState({
    feed: state.activityFeed,
    runtimeEvent: state.runtimeEvent,
    expanded: state.activityExpanded,
  });
  elements.hintText.textContent = card.line1;
  elements.headlineText.textContent = card.line2;
  elements.activityToggleButton.textContent = card.expanded ? "收起" : "更多";
  elements.activityToggleButton.setAttribute("aria-expanded", String(card.expanded));
  elements.activityHistory.hidden = !card.expanded;
  renderActivityHistory(card.items);
}

async function loadActivityFeed() {
  if (!state.online) {
    return;
  }
  try {
    state.activityFeed = normalizeActivityFeed(await fetchActivityFeed({ limit: 10 }));
  } catch {
    state.activityFeed = normalizeActivityFeed({
      live_summary: "阿B 这会儿先替你盯着。",
      headline: "最近还没新动静，先多刷一阵。",
      items: [],
    });
  }
  renderActivityCard();
}

async function loadMoreActivity() {
  if (
    !state.online ||
    !state.activityFeed ||
    !state.activityFeed.has_more ||
    !state.activityFeed.next_cursor ||
    state.activityLoadingMore
  ) {
    return;
  }
  state.activityLoadingMore = true;
  renderActivityCard();
  try {
    const nextPage = normalizeActivityFeed(
      await fetchActivityFeed({
        limit: 10,
        before: state.activityFeed.next_cursor,
      }),
    );
    // Append items, keep the existing live_summary / headline (they
    // describe "current" state, not the appended history).
    state.activityFeed = {
      ...state.activityFeed,
      items: [...state.activityFeed.items, ...nextPage.items],
      has_more: nextPage.has_more,
      next_cursor: nextPage.next_cursor,
    };
  } catch {
    // Leave existing items in place; user can retry by clicking again.
  } finally {
    state.activityLoadingMore = false;
    renderActivityCard();
  }
}

function renderChipList(container, items, fallback) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  const isFallback = items.length === 0;
  const values = isFallback ? [fallback] : items;
  for (const item of values) {
    const chip = document.createElement("span");
    chip.className = `chip${isFallback ? " is-fallback" : ""}`;
    chip.textContent = item;
    container.append(chip);
  }
}

function renderExplorationBar(container, openness) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  const fill = container.querySelector(".exploration-bar-fill");
  const label = container.querySelector(".exploration-bar-label");
  if (fill instanceof HTMLElement) {
    fill.style.width = `${Math.round(openness * 100)}%`;
  }
  if (label instanceof HTMLElement) {
    const pct = Math.round(openness * 100);
    const desc =
      pct >= 80 ? "很愿意看新东西" :
      pct >= 60 ? "偶尔探索新领域" :
      pct >= 40 ? "偏好熟悉的内容" :
      "基本只看自己那几个方向";
    label.textContent = `${pct}% — ${desc}`;
  }
}

function renderSpeculativeInterests(container, items, { kind = "interest" } = {}) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  const isAvoidance = kind === "avoidance";
  const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
  const visibleItems = Array.isArray(items)
    ? items.filter((item) => shouldHydrateProbe(item, probeType, state.handledProbeKeys))
    : [];
  container.replaceChildren();
  if (visibleItems.length === 0) {
    const fallback = document.createElement("p");
    fallback.className = "is-fallback";
    fallback.textContent = isAvoidance ? "暂时没有待确认避雷方向。" : "暂时没有在试探的方向，过一阵会有的。";
    container.append(fallback);
    return;
  }
  const statusLabels = {
    active: "",
    pending: "待观察",
    confirmed: "已确认",
    deprecated: "已弃",
    rejected: "已排除",
  };
  for (const item of visibleItems) {
    const row = document.createElement("div");
    row.className = `speculative-item is-status-${item.status || "active"}`;
    if (item.status) {
      row.dataset.status = item.status;
    }

    const header = document.createElement("div");
    header.className = "spec-header";

    const domain = document.createElement("span");
    domain.className = "spec-domain";
    domain.textContent = item.domain;
    header.append(domain);

    const statusText = statusLabels[item.status] ?? "";
    if (statusText) {
      const status = document.createElement("span");
      status.className = "spec-status";
      status.textContent = statusText;
      header.append(status);
    }

    const progress = document.createElement("span");
    progress.className = "spec-progress";
    progress.textContent = `${item.confirmation_count}/${item.confirmation_threshold} 次确认`;
    header.append(progress);

    row.append(header);

    if (typeof item.confidence === "number" && item.confidence > 0) {
      const confRow = document.createElement("div");
      confRow.className = "spec-confidence-row";
      const bar = document.createElement("div");
      bar.className = "spec-confidence-bar";
      const fill = document.createElement("div");
      fill.className = "spec-confidence-fill";
      fill.style.width = `${Math.round(item.confidence * 100)}%`;
      bar.append(fill);
      confRow.append(bar);
      const label = document.createElement("span");
      label.className = "spec-confidence-label";
      label.textContent = `置信度 ${Math.round(item.confidence * 100)}%`;
      confRow.append(label);
      row.append(confRow);
    }

    if (item.reason) {
      const reason = document.createElement("p");
      reason.className = "spec-reason";
      reason.textContent = item.reason;
      row.append(reason);
    }

    if (item.specifics && item.specifics.length > 0) {
      const specs = document.createElement("div");
      specs.className = "spec-specifics";
      for (const spec of item.specifics) {
        const chip = document.createElement("span");
        chip.className = "spec-specific-chip";
        chip.textContent = spec.name;
        if (spec.confirmation_count > 0) {
          const badge = document.createElement("span");
          badge.className = "spec-specific-count";
          badge.textContent = `${spec.confirmation_count}`;
          chip.append(badge);
        }
        specs.append(chip);
      }
      row.append(specs);
    }

    // Inline action buttons on active speculations so the user can give
    // feedback directly from the profile section without waiting for a
    // WebSocket push or opening the messages inbox.
    if ((item.status || "active") === "active" && item.domain) {
      const actions = document.createElement("div");
      actions.className = "spec-actions";

      const confirmBtn = document.createElement("button");
      confirmBtn.className = "probe-btn is-confirm";
      confirmBtn.textContent = isAvoidance ? "确实不喜欢" : "喜欢";
      confirmBtn.addEventListener("click", () =>
        handleSpecResponse(item.domain, "confirm", row, isAvoidance ? "avoidance.probe" : "interest.probe"),
      );

      const rejectBtn = document.createElement("button");
      rejectBtn.className = "probe-btn is-reject";
      rejectBtn.textContent = isAvoidance ? "不是" : "不喜欢";
      rejectBtn.addEventListener("click", () =>
        handleSpecResponse(item.domain, "reject", row, isAvoidance ? "avoidance.probe" : "interest.probe"),
      );

      actions.append(confirmBtn, rejectBtn);
      row.append(actions);
    }

    container.append(row);
  }
}

async function handleSpecResponse(domain, responseType, rowEl, type = "interest.probe") {
  if (!domain) return;
  const isAvoidance = isAvoidanceProbeType(type);
  rememberHandledProbe(domain, type);
  // Disable buttons immediately so double-click can't fire twice.
  if (rowEl instanceof HTMLElement) {
    rowEl.querySelectorAll(".probe-btn").forEach((b) => {
      if (b instanceof HTMLButtonElement) b.disabled = true;
    });
  }
  try {
    const respond = isAvoidance ? respondToAvoidanceProbe : respondToInterestProbe;
    const apiResp = await respond(domain, responseType);
    if (apiResp && apiResp.ok === false) {
      if (rowEl instanceof HTMLElement) {
        rowEl.remove();
      }
      applyStaleProbeResponse(domain, type);
      await loadProfileSummary({ force: true });
      return;
    }
    if (rowEl instanceof HTMLElement) {
      rowEl.replaceChildren();
      const msg = document.createElement("p");
      msg.className = "spec-result";
      msg.textContent = isAvoidance
        ? (responseType === "confirm" ? `好，「${domain}」会作为避雷方向处理。` : `好，「${domain}」不记成避雷。`)
        : (responseType === "confirm" ? `好，「${domain}」记住了。` : `好，「${domain}」先不看了。`);
      rowEl.append(msg);
      setTimeout(() => rowEl.remove(), 2500);
    }
    // Drop matching message-card from inbox state too, so the badge is in sync.
    removeMessageFromState(domain, type);
    updateMessageBadge();
    // Delay the profile re-fetch so the "好，记住了" message stays
    // visible long enough to be perceived. Without this delay,
    // renderSpeculativeInterests' container.replaceChildren() clobbers
    // the success UI within ~10ms (both endpoints respond in ~5ms),
    // making clicks look like no-ops.
    setTimeout(() => {
      void loadProfileSummary({ force: true });
    }, 2200);
  } catch (err) {
    console.error("spec response failed:", err);
    forgetHandledProbe(domain, type);
    if (rowEl instanceof HTMLElement) {
      rowEl.querySelectorAll(".probe-btn").forEach((b) => {
        if (b instanceof HTMLButtonElement) b.disabled = false;
      });
    }
  }
}

function renderProbeCard() {
  const container = elements.profileSpeculativeInterests;
  if (!(container instanceof HTMLElement) || !state.pendingProbe) return;

  const probe = state.pendingProbe;

  // Remove any existing probe card
  const existing = container.querySelector(".probe-card");
  if (existing) existing.remove();

  const challenge = isChallengeProbe(probe);
  const card = document.createElement("div");
  card.className = `probe-card ${challenge ? "is-challenge" : "is-interest"}`;

  const kicker = document.createElement("div");
  kicker.className = "probe-kicker";
  kicker.textContent = challenge ? "挑战探针" : "兴趣确认";
  card.append(kicker);

  const question = document.createElement("p");
  question.className = "probe-question";
  question.textContent = probe.question || `\u6211\u4ece\u4f60\u6700\u8fd1\u7684\u8f68\u8ff9\u91cc\u55c5\u5230\u4f60\u53ef\u80fd\u5bf9\u300c${probe.domain}\u300d\u611f\u5174\u8da3\u2014\u2014\u4f60\u81ea\u5df1\u8ba4\u4e0d\u8ba4\uff1f`;
  card.append(question);

  const prompt = document.createElement("p");
  prompt.className = "message-kind-copy";
  prompt.textContent = challenge
    ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就直接否掉。"
    : "想继续试探这个方向就点喜欢，不准就点不喜欢。";
  card.append(prompt);

  if (probe.specifics && probe.specifics.length > 0) {
    const chips = document.createElement("div");
    chips.className = "probe-specifics";
    for (const s of probe.specifics.slice(0, 5)) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = typeof s === "string" ? s : s.name || s;
      chips.append(chip);
    }
    card.append(chips);
  }

  const actions = document.createElement("div");
  actions.className = "probe-actions";

  const confirmBtn = document.createElement("button");
  confirmBtn.className = "probe-btn is-confirm";
  confirmBtn.textContent = "\u559C\u6B22";
  confirmBtn.addEventListener("click", () => handleProbeResponse("confirm"));

  const rejectBtn = document.createElement("button");
  rejectBtn.className = "probe-btn is-reject";
  rejectBtn.textContent = "\u4E0D\u559C\u6B22";
  rejectBtn.addEventListener("click", () => handleProbeResponse("reject"));

  const chatBtn = document.createElement("button");
  chatBtn.className = "probe-btn is-chat";
  chatBtn.textContent = "\u591a\u804a\u804a";
  chatBtn.addEventListener("click", () => handleProbeResponse("chat"));

  actions.append(confirmBtn, rejectBtn, chatBtn);
  card.append(actions);

  // Insert at the top of the speculative interests container
  container.prepend(card);
}

async function handleProbeResponse(responseType) {
  const probe = state.pendingProbe;
  if (!probe) return;

  const domain = probe.domain;
  const probeCard = document.querySelector(".probe-card");

  if (responseType === "chat") {
    // Expand inline chat directly on the probe card
    if (probeCard) {
      expandInlineChat(probeCard, domain);
    }
    return;
  }

  rememberHandledProbe(domain, "interest.probe");
  try {
    const apiResp = await respondToInterestProbe(domain, responseType);
    if (apiResp && apiResp.ok === false) {
      if (probeCard) {
        probeCard.remove();
      }
      applyStaleProbeResponse(domain, "interest.probe");
      await loadProfileSummary({ force: true });
      return;
    }

    // Show feedback
    if (probeCard) {
      probeCard.replaceChildren();
      const msg = document.createElement("p");
      msg.className = "probe-result";
      msg.textContent = responseType === "confirm"
        ? `\u597D\uFF0C\u300C${domain}\u300D\u8BB0\u4F4F\u4E86\u3002`
        : `\u597D\uFF0C\u300C${domain}\u300D\u5148\u4E0D\u770B\u4E86\u3002`;
      probeCard.append(msg);
      setTimeout(() => probeCard.remove(), 3000);
    }

    state.pendingProbe = null;
    // Also remove from messages inbox
    removeMessageFromState(domain, "interest.probe");

    // Delay the profile re-fetch so the success message stays visible.
    // Re-rendering speculative-list immediately would clobber the probe
    // card's "好，记住了" text within ~10ms (see handleSpecResponse).
    setTimeout(() => {
      void loadProfileSummary({ force: true });
    }, 2700);
  } catch (err) {
    console.error("Failed to respond to probe:", err);
    forgetHandledProbe(domain, "interest.probe");
  }
}

// ── Messages inbox ─────────────────────────────────────────────

function updateMessageBadge() {
  const badge = elements.messageBadge;
  if (!(badge instanceof HTMLElement)) return;
  const count = state.messages.length;
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

async function openMessagesPanel() {
  const overlay = elements.messagesOverlay;
  if (!(overlay instanceof HTMLElement)) return;
  overlay.hidden = false;
  // Render whatever we have synchronously so the panel doesn't open
  // empty while we refetch.
  renderMessagesList();
  // Then force-refresh the profile so the inbox shows the *current*
  // active speculations.  Without this, probes that the speculator
  // rotated out (TTL, replacement, manual force_tick) can sit stale
  // in the inbox and clicking them returns ``ok: false`` because the
  // backend no longer recognises the domain.
  try {
    await loadProfileSummary({ force: true });
  } catch {
    // Already-rendered stale list is acceptable on refresh failure.
    return;
  }
  renderMessagesList();
}

function closeMessagesPanel() {
  const overlay = elements.messagesOverlay;
  if (overlay instanceof HTMLElement) overlay.hidden = true;
}

// ── Mobile QR panel ───────────────────────────────────────────

async function writeClipboardText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.append(textarea);
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } finally {
    textarea.remove();
  }
  return ok;
}

function openMobileWebUrl(url) {
  if (!url) return;
  try {
    if (globalThis.chrome?.tabs?.create) {
      void globalThis.chrome.tabs.create({ url });
      return;
    }
  } catch {
    // Fall back to window.open below.
  }
  window.open(url, "_blank", "noopener");
}

const STAR_REPO_URL = "https://github.com/whiteguo233/OpenBiliClaw";

// Wire the persistent header Star button: always present, opens the repo so the
// user can give a GitHub Star.
const STAR_REPO_SLUG = "whiteguo233/OpenBiliClaw";
const STAR_COUNT_CACHE_KEY = "obc:starCount";
const STAR_COUNT_TTL_MS = 12 * 60 * 60 * 1000;

function _formatStarCount(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) {
    return "";
  }
  if (n >= 10000) {
    return `${(n / 1000).toFixed(0)}k`;
  }
  if (n >= 1000) {
    return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return String(n);
}

function _showStarCount(n) {
  const el = elements.starCount;
  const text = _formatStarCount(n);
  if (el instanceof HTMLElement && text) {
    el.textContent = text;
    el.hidden = false;
  }
}

// Fetch + cache the GitHub stargazers count for the count box (the GitHub-Buttons
// look). api.github.com sends CORS `*`, so no host permission is needed; the
// count is cached in localStorage so we don't hit the unauthenticated rate limit.
async function loadStarCount() {
  if (!(elements.starCount instanceof HTMLElement)) {
    return;
  }
  let cachedTime = 0;
  try {
    const raw = localStorage.getItem(STAR_COUNT_CACHE_KEY);
    if (raw) {
      const { n, t } = JSON.parse(raw);
      if (typeof n === "number") {
        _showStarCount(n);
        cachedTime = typeof t === "number" ? t : 0;
      }
    }
  } catch {
    cachedTime = 0;
  }
  if (Date.now() - cachedTime < STAR_COUNT_TTL_MS) {
    return; // cached value is fresh enough
  }
  try {
    const res = await fetch(`https://api.github.com/repos/${STAR_REPO_SLUG}`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    const n = data?.stargazers_count;
    if (typeof n === "number") {
      _showStarCount(n);
      try {
        localStorage.setItem(STAR_COUNT_CACHE_KEY, JSON.stringify({ n, t: Date.now() }));
      } catch {
        // storage full / unavailable → just skip caching
      }
    }
  } catch {
    // offline / rate-limited → keep the button without a count
  }
}

function bindStarButton() {
  const { starButton } = elements;
  if (!(starButton instanceof HTMLElement)) {
    return;
  }
  starButton.addEventListener("click", () => {
    openMobileWebUrl(STAR_REPO_URL);
  });
  void loadStarCount();
}

async function renderMobileQrPanel() {
  const endpoint = await getBackendEndpointConfig();

  // When the configured host is loopback, try to get the server's
  // detected LAN IP from the health endpoint so the QR code shows
  // an address that mobile devices can actually reach.
  let effectiveEndpoint = endpoint;
  if (isLoopbackMobileHost(endpoint.host)) {
    try {
      const base = `http://${endpoint.host}:${endpoint.port}`;
      const resp = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) });
      if (resp.ok) {
        const data = await resp.json();
        if (data.lan_ip && !isLoopbackMobileHost(data.lan_ip)) {
          effectiveEndpoint = { ...endpoint, host: data.lan_ip };
        }
      }
    } catch {
      // Health fetch failed — fall through with original endpoint.
    }
  }

  const view = getMobileQrViewState(effectiveEndpoint);
  currentMobileWebUrl = view.url;

  if (elements.mobileQrCode instanceof HTMLElement) {
    try {
      elements.mobileQrCode.innerHTML = createQrSvgMarkup(view.url);
    } catch (err) {
      elements.mobileQrCode.textContent = "二维码生成失败";
      console.error("Failed to render mobile QR:", err);
    }
  }
  if (elements.mobileQrUrl instanceof HTMLElement) {
    elements.mobileQrUrl.textContent = view.url;
  }
  if (elements.mobileQrHint instanceof HTMLElement) {
    elements.mobileQrHint.textContent = view.hint;
    elements.mobileQrHint.dataset.tone = view.tone;
  }
}

async function openMobileQrPanel() {
  const overlay = elements.mobileQrOverlay;
  if (!(overlay instanceof HTMLElement)) return;
  overlay.hidden = false;
  await renderMobileQrPanel();
}

function closeMobileQrPanel() {
  const overlay = elements.mobileQrOverlay;
  if (overlay instanceof HTMLElement) overlay.hidden = true;
}

function bindOpenWeb() {
  if (elements.openWebButton instanceof HTMLElement) {
    elements.openWebButton.addEventListener("click", async () => {
      const origin = await getBackendOrigin();
      const url = origin + "/";
      try {
        if (globalThis.chrome?.tabs?.create) {
          void globalThis.chrome.tabs.create({ url });
          return;
        }
      } catch {
        // Fall back to window.open below.
      }
      window.open(url, "_blank", "noopener");
    });
  }
}

function bindMobileQr() {
  if (elements.mobileQrButton instanceof HTMLElement) {
    elements.mobileQrButton.addEventListener("click", () => {
      void openMobileQrPanel();
    });
  }
  if (elements.mobileQrBack instanceof HTMLElement) {
    elements.mobileQrBack.addEventListener("click", closeMobileQrPanel);
  }
  if (elements.mobileQrCopy instanceof HTMLButtonElement) {
    elements.mobileQrCopy.addEventListener("click", async () => {
      if (!currentMobileWebUrl) await renderMobileQrPanel();
      const original = elements.mobileQrCopy.textContent || "复制链接";
      try {
        const ok = await writeClipboardText(currentMobileWebUrl);
        elements.mobileQrCopy.textContent = ok ? "已复制" : "复制失败";
      } catch {
        elements.mobileQrCopy.textContent = "复制失败";
      } finally {
        setTimeout(() => {
          if (elements.mobileQrCopy instanceof HTMLButtonElement) {
            elements.mobileQrCopy.textContent = original;
          }
        }, 1200);
      }
    });
  }
  if (elements.mobileQrOpen instanceof HTMLButtonElement) {
    elements.mobileQrOpen.addEventListener("click", async () => {
      if (!currentMobileWebUrl) await renderMobileQrPanel();
      openMobileWebUrl(currentMobileWebUrl);
    });
  }
}

function renderMessagesList() {
  const container = elements.messagesList;
  if (!(container instanceof HTMLElement)) return;
  container.replaceChildren();

  if (state.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "messages-empty";
    empty.innerHTML = '<div class="messages-empty-icon">\u{1F4EC}</div><p>\u6682\u65F6\u6CA1\u6709\u65B0\u6D88\u606F\u3002<br>\u5174\u8DA3\u786E\u8BA4\u3001\u60CA\u559C\u63A8\u8350\u548C\u901A\u77E5\u90FD\u4F1A\u51FA\u73B0\u5728\u8FD9\u91CC\u3002</p>';
    container.append(empty);
    return;
  }

  for (const msg of state.messages) {
    const type = msg.type || "interest.probe";
    if (type === "delight") continue; // delights shown in delight tray, not messages
    container.append(buildMessageCard(msg));
  }
}

function buildMessageCard(probe) {
  const type = normalizeProbeType(probe?.type);
  const isAvoidance = isAvoidanceProbeType(type);
  const challenge = !isAvoidance && isChallengeProbe(probe);
  const item = document.createElement("div");
  item.className = "message-item";
  item.classList.add(isAvoidance ? "is-avoidance" : challenge ? "is-challenge" : "is-interest");
  item.dataset.domain = probe.domain;
  item.dataset.type = type;

  // Dismiss button (×)
  const dismiss = document.createElement("button");
  dismiss.className = "message-dismiss";
  dismiss.textContent = "\u00D7";
  dismiss.title = "\u5173\u95ED";
  dismiss.addEventListener("click", () => dismissMessage(probe.domain, type));
  item.append(dismiss);

  const eyebrow = document.createElement("div");
  eyebrow.className = "message-reason";
  eyebrow.textContent = isAvoidance ? "避雷确认" : challenge ? "挑战探针" : "兴趣确认";
  item.append(eyebrow);

  const kindCopy = document.createElement("p");
  kindCopy.className = "message-kind-copy";
  kindCopy.textContent = isAvoidance
    ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。"
    : challenge
      ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
    : "想继续试探这个方向，就点喜欢；不准就点不喜欢。";
  item.append(kindCopy);

  const domain = document.createElement("div");
  domain.className = "message-domain";
  domain.textContent = probe.domain;
  item.append(domain);

  if (probe.reason) {
    const reason = document.createElement("p");
    reason.className = "message-reason";
    reason.textContent = probe.reason;
    item.append(reason);
  }

  if (probe.specifics && probe.specifics.length > 0) {
    const chips = document.createElement("div");
    chips.className = "message-specifics";
    for (const s of probe.specifics.slice(0, 5)) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = typeof s === "string" ? s : s.name || s;
      chips.append(chip);
    }
    item.append(chips);
  }

  if (probe.chat_status === "pending") {
    item.append(createChatThinkingPlaceholder("阿B 正在思考这个方向"));
  } else if (probe.chat_reply) {
    const reply = document.createElement("div");
    reply.className = "message-chat-reply";
    reply.textContent = probe.chat_reply;
    item.append(reply);
  }

  const actions = document.createElement("div");
  actions.className = "message-actions";

  const confirmBtn = document.createElement("button");
  confirmBtn.className = "probe-btn is-confirm";
  confirmBtn.textContent = isAvoidance ? "确实不喜欢" : "\u559C\u6B22";
  confirmBtn.addEventListener("click", () => handleMessageResponse(probe.domain, "confirm", type));

  const rejectBtn = document.createElement("button");
  rejectBtn.className = "probe-btn is-reject";
  rejectBtn.textContent = isAvoidance ? "不是" : "\u4E0D\u559C\u6B22";
  rejectBtn.addEventListener("click", () => handleMessageResponse(probe.domain, "reject", type));

  const chatBtn = document.createElement("button");
  chatBtn.className = "probe-btn is-chat";
  chatBtn.textContent = "\u591A\u804A\u804A";
  chatBtn.addEventListener("click", () => expandInlineChat(item, probe.domain, type));

  if (probe.chat_status === "pending") {
    confirmBtn.disabled = true;
    rejectBtn.disabled = true;
    chatBtn.disabled = true;
  }

  actions.append(confirmBtn, rejectBtn, chatBtn);
  item.append(actions);
  return item;
}

// ── Delight (surprise recommendation) card ─────────────────────

function buildDelightCard(delight) {
  const item = document.createElement("div");
  item.className = "message-item is-delight";
  item.dataset.bvid = delight.bvid;

  // Dismiss ×
  const dismiss = document.createElement("button");
  dismiss.className = "message-dismiss";
  dismiss.textContent = "\u00D7";
  dismiss.title = "\u5173\u95ED";
  dismiss.addEventListener("click", () => dismissMessageByBvid(delight.bvid));
  item.append(dismiss);

  // Top row: thumbnail + (hook badge + title)
  const top = document.createElement("div");
  top.className = "message-delight-top";

  const thumb = document.createElement("span");
  thumb.className = "message-delight-thumb";
  if (delight.cover_url) {
    const image = document.createElement("img");
    void setProxyImageSrc(image, delight.cover_url);
    image.alt = "";
    image.addEventListener("error", () => {
      image.remove();
      thumb.classList.add("is-fallback");
      thumb.textContent = "\u2728";
    });
    thumb.append(image);
  } else {
    thumb.classList.add("is-fallback");
    thumb.textContent = "\u2728";
  }
  top.append(thumb);

  const textCol = document.createElement("div");
  textCol.className = "message-delight-text";

  if (delight.delight_hook) {
    const hookBadge = document.createElement("span");
    hookBadge.className = "message-delight-hook";
    hookBadge.textContent = `\u2728 ${delight.delight_hook}`;
    textCol.append(hookBadge);
  }

  const title = document.createElement("div");
  title.className = "message-delight-title";
  title.textContent = delight.title || "";
  textCol.append(title);

  top.append(textCol);
  item.append(top);

  const kindCopy = document.createElement("p");
  kindCopy.className = "message-kind-copy";
  kindCopy.textContent = "这不是口味确认，是一条可能让你意外喜欢的内容。";
  item.append(kindCopy);

  // Reason
  if (delight.delight_reason) {
    const reason = document.createElement("p");
    reason.className = "message-reason";
    reason.textContent = delight.delight_reason;
    item.append(reason);
  }

  if (delight.chat_status === "pending") {
    item.append(createChatThinkingPlaceholder("阿B 正在品你这句话"));
  } else if (delight.chat_reply) {
    const reply = document.createElement("div");
    reply.className = "message-chat-reply";
    reply.textContent = delight.chat_reply;
    item.append(reply);
  }

  // Action buttons
  const actions = document.createElement("div");
  actions.className = "message-actions";

  const viewBtn = document.createElement("button");
  viewBtn.className = "probe-btn is-view";
  viewBtn.textContent = "\u770B\u770B";
  viewBtn.addEventListener("click", () => {
    const url = buildContentUrl(delight);
    window.open(url, "_blank");
    respondToDelight(delight.bvid, "view", delight.title).catch(() => {});
    const status = document.createElement("p");
    status.className = "message-result";
    status.textContent = "已打开，阿B 会把这次点击当成强信号。";
    item.append(status);
  });

  const likeBtn = document.createElement("button");
  likeBtn.className = "probe-btn is-confirm";
  likeBtn.textContent = "\u559C\u6B22";
  likeBtn.addEventListener("click", () => handleDelightResponse(delight, "like"));

  const dislikeBtn = document.createElement("button");
  dislikeBtn.className = "probe-btn is-reject";
  dislikeBtn.textContent = "\u4E0D\u611F\u5174\u8DA3";
  dislikeBtn.addEventListener("click", () => handleDelightResponse(delight, "dislike"));

  const chatBtn = document.createElement("button");
  chatBtn.className = "probe-btn is-chat";
  chatBtn.textContent = "\u804A\u4E00\u804A";
  chatBtn.addEventListener("click", () => expandDelightChat(item, delight));

  if (delight.chat_status === "pending") {
    viewBtn.disabled = true;
    likeBtn.disabled = true;
    dislikeBtn.disabled = true;
    chatBtn.disabled = true;
  }

  actions.append(viewBtn, likeBtn, dislikeBtn, chatBtn);
  item.append(actions);
  return item;
}

async function handleDelightResponse(delight, responseType) {
  try {
    await respondToDelight(delight.bvid, responseType, delight.title);
    const item = elements.messagesList?.querySelector(`[data-bvid="${CSS.escape(delight.bvid)}"]`);
    if (item) {
      if (responseType === "like") {
        const msg = document.createElement("p");
        msg.className = "message-result";
        msg.textContent = "\u597D\uFF0C\u8FD9\u7C7B\u591A\u6765\u70B9\u3002";
        item.append(msg);
      } else {
        item.replaceChildren();
        const msg = document.createElement("p");
        msg.className = "message-result";
        msg.textContent = "\u597D\uFF0C\u8FD9\u7C7B\u5148\u4E0D\u63A8\u4E86\u3002";
        item.append(msg);
        setTimeout(() => { item.remove(); renderMessagesEmptyIfNeeded(); }, 2000);
      }
    }
    if (responseType !== "like") {
      dismissMessageByBvid(delight.bvid, false);
    }
  } catch (err) {
    console.error("Delight response failed:", err);
  }
}

function expandDelightChat(itemEl, delight) {
  if (itemEl.querySelector(".message-chat-area")) return;
  const actions = itemEl.querySelector(".message-actions");
  if (actions) actions.hidden = true;

  const chatArea = document.createElement("div");
  chatArea.className = "message-chat-area";

  const input = document.createElement("textarea");
  input.className = "message-chat-input";
  input.rows = 1;
  input.placeholder = `\u804A\u804A\u4F60\u5BF9\u8FD9\u6761\u63A8\u8350\u7684\u60F3\u6CD5\u2026`;

  const sendBtn = document.createElement("button");
  sendBtn.className = "message-chat-send";
  sendBtn.textContent = "\u53D1\u9001";
  sendBtn.addEventListener("click", async () => {
    const message = input.value.trim();
    if (!message) return;
    sendBtn.disabled = true;
    const turnId = createClientTurnId("delight");
    const thinking = createChatThinkingPlaceholder("\u963fB \u6b63\u5728\u54c1\u4f60\u8fd9\u53e5\u8bdd");
    itemEl.append(thinking);
    try {
      const turn = await startChatTurn({
        turnId,
        session: CHAT_SESSION,
        scope: "delight",
        subjectId: delight.bvid,
        subjectTitle: delight.title || "",
        message,
      });
      const ca = itemEl.querySelector(".message-chat-area");
      if (ca) ca.remove();
      const showReply = (nextTurn) => {
        thinking.remove();
        const replyEl = document.createElement("div");
        replyEl.className = "message-chat-reply";
        replyEl.textContent =
          nextTurn.reply || "\u6536\u5230\u4E86\uFF0C\u6211\u4F1A\u7EE7\u7EED\u89C2\u5BDF\u3002";
        itemEl.append(replyEl);
        applyTurnToMessage(nextTurn);
        applyTurnToDelight(nextTurn);
      };
      if (turn.status === "completed" || turn.status === "failed") {
        showReply(turn);
      } else {
        applyTurnToMessage(turn);
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate(nextTurn) {
            if (nextTurn.status === "completed" || nextTurn.status === "failed") {
              showReply(nextTurn);
            }
          },
        });
      }
    } catch (err) {
      console.error("Delight chat failed:", err);
      thinking.remove();
      sendBtn.disabled = false;
      const errEl = document.createElement("div");
      errEl.className = "message-chat-reply";
      errEl.textContent = "\u540E\u53F0\u6B63\u5FD9\uFF0C\u7B49\u4E00\u4E0B\u518D\u804A\u3002";
      itemEl.append(errEl);
      setTimeout(() => errEl.remove(), 3000);
    }
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }
  });

  chatArea.append(input, sendBtn);
  itemEl.append(chatArea);
  input.focus();
}

function dismissMessageByBvid(bvid, removeFromDom = true) {
  state.messages = state.messages.filter((m) => m.bvid !== bvid);
  updateMessageBadge();
  // Mirror the dismiss on the backend so the same bvid doesn't
  // re-surface via /api/delight/pending-batch on next popup reload.
  rememberDismissedDelight(bvid);
  if (removeFromDom) {
    const item = elements.messagesList?.querySelector(`[data-bvid="${CSS.escape(bvid)}"]`);
    if (item) item.remove();
    renderMessagesEmptyIfNeeded();
  }
}

function expandInlineChat(itemEl, domain, type = "interest.probe") {
  // Don't add twice
  if (itemEl.querySelector(".message-chat-area")) return;
  const isAvoidance = isAvoidanceProbeType(type);

  // Hide the action buttons
  const actions = itemEl.querySelector(".message-actions");
  if (actions) actions.hidden = true;

  const chatArea = document.createElement("div");
  chatArea.className = "message-chat-area";

  const input = document.createElement("textarea");
  input.className = "message-chat-input";
  input.rows = 1;
  input.placeholder = isAvoidance ? `聊聊你为什么想避开「${domain}」…` : `\u804A\u804A\u4F60\u5BF9\u300C${domain}\u300D\u7684\u60F3\u6CD5\u2026`;

  const sendBtn = document.createElement("button");
  sendBtn.className = "message-chat-send";
  sendBtn.textContent = "\u53D1\u9001";
  sendBtn.addEventListener("click", () => sendInlineChat(itemEl, domain, input, sendBtn, type));

  // Allow Enter to send
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendBtn.click();
    }
  });

  chatArea.append(input, sendBtn);
  itemEl.append(chatArea);
  input.focus();
}


function createChatThinkingPlaceholder(label) {
  // Reusable "thinking" indicator for any in-card chat composer.
  // Shows the bouncing-dots animation plus a friendly label so the
  // user knows the request is in flight (default ~30s for delight
  // chat, ~30s for probe chat).
  const wrap = document.createElement("div");
  wrap.className = "message-chat-thinking";
  const text = document.createElement("span");
  text.className = "message-chat-thinking-label";
  text.textContent = label || "\u963fB \u6b63\u5728\u601d\u8003";
  const dots = document.createElement("span");
  dots.className = "chat-thinking-dots";
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement("span");
    dot.className = "chat-thinking-dot";
    dots.append(dot);
  }
  wrap.append(text, dots);
  return wrap;
}

async function sendInlineChat(itemEl, domain, input, sendBtn, type = "interest.probe") {
  const message = input.value.trim();
  if (!message) return;
  const isAvoidance = isAvoidanceProbeType(type);

  sendBtn.disabled = true;
  const turnId = createClientTurnId(isAvoidance ? "avoidance_probe" : "probe");
  rememberHandledProbe(domain, type);

  // Show a thinking placeholder so the user knows we\u2019re waiting
  // on the LLM. The composer\u2019s send button alone going gray
  // wasn\u2019t enough of a signal — many users assumed the click
  // didn\u2019t register.
  const thinking = createChatThinkingPlaceholder(isAvoidance ? "阿B 正在确认这个避雷边界" : "\u963fB \u6b63\u5728\u601d\u8003\u8fd9\u4e2a\u65b9\u5411");
  itemEl.append(thinking);

  try {
    const turn = await startChatTurn({
      turnId,
      session: CHAT_SESSION,
      scope: isAvoidance ? "avoidance_probe" : "probe",
      subjectId: domain,
      subjectTitle: domain,
      message,
    });

    // Remove chat area, show result, then remove card after delay
    const chatArea = itemEl.querySelector(".message-chat-area");
    if (chatArea) chatArea.remove();

    const showReply = (nextTurn) => {
      thinking.remove();
      const replyEl = document.createElement("div");
      replyEl.className = "message-chat-reply";
      replyEl.textContent =
        nextTurn.reply || "\u6536\u5230\u4E86\uFF0C\u6211\u4F1A\u7ED3\u5408\u8FD9\u4E2A\u65B9\u5411\u7EE7\u7EED\u89C2\u5BDF\u3002";
      itemEl.append(replyEl);
      applyTurnToMessage(nextTurn);
      setTimeout(() => {
        removeMessageFromState(domain, type);
        itemEl.remove();
        renderMessagesEmptyIfNeeded();
      }, 4000);
    };

    if (turn.status === "completed" || turn.status === "failed") {
      showReply(turn);
    } else {
      applyTurnToMessage(turn);
      pollChatTurnUntilSettled(turn.turn_id, {
        onUpdate(nextTurn) {
          if (nextTurn.status === "completed" || nextTurn.status === "failed") {
            showReply(nextTurn);
          }
        },
      });
    }
  } catch (err) {
    console.error("Inline chat failed:", err);
    forgetHandledProbe(domain, type);
    thinking.remove();
    sendBtn.disabled = false;
    // Show error hint inline
    const errEl = document.createElement("div");
    errEl.className = "message-chat-reply";
    errEl.textContent = "\u540E\u53F0\u6B63\u5FD9\uFF0C\u7B49\u4E00\u4E0B\u518D\u804A\u3002";
    itemEl.append(errEl);
    setTimeout(() => errEl.remove(), 3000);
  }
}

function dismissMessage(domain, type = "") {
  removeMessageFromState(domain, type);
  const selector = type
    ? `[data-domain="${CSS.escape(domain)}"][data-type="${CSS.escape(normalizeProbeType(type))}"]`
    : `[data-domain="${CSS.escape(domain)}"]`;
  const item = elements.messagesList?.querySelector(selector);
  if (item) item.remove();
  renderMessagesEmptyIfNeeded();
}

async function handleMessageResponse(domain, responseType, type = "interest.probe") {
  const isAvoidance = isAvoidanceProbeType(type);
  rememberHandledProbe(domain, type);
  try {
    const respond = isAvoidance ? respondToAvoidanceProbe : respondToInterestProbe;
    const apiResp = await respond(domain, responseType);

    const item = elements.messagesList?.querySelector(`[data-domain="${CSS.escape(domain)}"][data-type="${CSS.escape(normalizeProbeType(type))}"]`);
    // ok=false means the backend no longer recognises this domain
    // (typical: probe rotated out by TTL or a fresh force_tick while
    // the popup sat open with a stale inbox). Remove it locally and
    // force-refetch so the panel matches reality without showing a
    // misleading success state.
    if (apiResp && apiResp.ok === false) {
      if (item) {
        item.remove();
      }
      applyStaleProbeResponse(domain, type);
      try {
        await loadProfileSummary({ force: true });
      } catch {
        /* fall through */
      }
      renderMessagesList();
      return;
    }

    if (item) {
      item.replaceChildren();
      const msg = document.createElement("p");
      msg.className = "message-result";
      msg.textContent = isAvoidance
        ? (responseType === "confirm" ? `好，「${domain}」会作为避雷方向处理。` : `好，「${domain}」不记成避雷。`)
        : (responseType === "confirm" ? `\u597D\uFF0C\u300C${domain}\u300D\u8BB0\u4F4F\u4E86\u3002` : `\u597D\uFF0C\u300C${domain}\u300D\u5148\u4E0D\u770B\u4E86\u3002`);
      item.append(msg);
      setTimeout(() => {
        item.remove();
        renderMessagesEmptyIfNeeded();
      }, 2000);
    }

    removeMessageFromState(domain, type);
    // Delay the profile re-fetch so the inbox card's success message stays
    // visible. The speculative-list re-render that loadProfileSummary
    // triggers doesn't touch the messages container, but it can still
    // visibly thrash if it lands during the user's reading window.
    setTimeout(() => {
      void loadProfileSummary({ force: true });
    }, 1800);
  } catch (err) {
    console.error("Failed to respond to message:", err);
    forgetHandledProbe(domain, type);
  }
}

function removeMessageFromState(domain, type = "") {
  const normalizedType = type ? normalizeProbeType(type) : "";
  state.messages = state.messages.filter((m) => {
    if (m.domain !== domain) return true;
    return normalizedType && normalizeProbeType(m.type) !== normalizedType;
  });
  if ((!normalizedType || normalizedType === "interest.probe") && state.pendingProbe?.domain === domain) state.pendingProbe = null;
  if ((!normalizedType || normalizedType === "avoidance.probe") && state.pendingAvoidanceProbe?.domain === domain) state.pendingAvoidanceProbe = null;
  updateMessageBadge();
}

function renderMessagesEmptyIfNeeded() {
  const container = elements.messagesList;
  if (!(container instanceof HTMLElement)) return;
  if (state.messages.length === 0 && container.children.length === 0) {
    const empty = document.createElement("div");
    empty.className = "messages-empty";
    empty.innerHTML = '<div class="messages-empty-icon">\u{1F4EC}</div><p>\u6682\u65F6\u6CA1\u6709\u5F85\u786E\u8BA4\u7684\u6D88\u606F\u3002<br>\u5174\u8DA3\u786E\u8BA4\u548C\u907F\u96F7\u786E\u8BA4\u90FD\u4F1A\u51FA\u73B0\u5728\u8FD9\u91CC\u3002</p>';
    container.append(empty);
  }
}

function bindMessages() {
  if (elements.messagesButton instanceof HTMLElement) {
    elements.messagesButton.addEventListener("click", openMessagesPanel);
  }
  if (elements.messagesBack instanceof HTMLElement) {
    elements.messagesBack.addEventListener("click", closeMessagesPanel);
  }
}

function renderActiveInsights(container, items) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!items || items.length === 0) {
    const fallback = document.createElement("p");
    fallback.className = "is-fallback";
    fallback.textContent = "暂时没有活跃的洞察，多看一阵会慢慢积累的。";
    container.append(fallback);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = item.validated ? "insight-item is-validated" : "insight-item";

    const hypothesis = document.createElement("p");
    hypothesis.className = "insight-hypothesis";
    hypothesis.textContent = item.hypothesis;
    row.append(hypothesis);

    const confRow = document.createElement("div");
    confRow.className = "insight-confidence-row";

    const bar = document.createElement("div");
    bar.className = "insight-confidence-bar";
    const fill = document.createElement("div");
    fill.className = "insight-confidence-fill";
    fill.style.width = `${Math.round(item.confidence * 100)}%`;
    bar.append(fill);
    confRow.append(bar);

    const confLabel = document.createElement("span");
    confLabel.className = "insight-confidence-label";
    confLabel.textContent = `${Math.round(item.confidence * 100)}%`;
    confRow.append(confLabel);

    if (item.validated) {
      const badge = document.createElement("span");
      badge.className = "insight-validated-badge";
      badge.textContent = "已确认";
      confRow.append(badge);
    }

    row.append(confRow);

    if (item.evidence && item.evidence.length > 0) {
      const evidenceList = document.createElement("div");
      evidenceList.className = "insight-evidence";
      for (const e of item.evidence) {
        const ev = document.createElement("p");
        ev.className = "insight-evidence-item";
        ev.textContent = e;
        evidenceList.append(ev);
      }
      row.append(evidenceList);
    }

    const createdLabel = formatRelativeTimestamp(item.created_at);
    if (createdLabel) {
      const timestampWrapper = document.createElement("p");
      timestampWrapper.className = "insight-timestamp";
      timestampWrapper.append("记于 ");
      const timestamp = document.createElement("time");
      if (item.created_at) {
        timestamp.dateTime = item.created_at;
      }
      timestamp.textContent = createdLabel;
      timestampWrapper.append(timestamp);
      row.append(timestampWrapper);
    }

    const actions = document.createElement("div");
    actions.className = "insight-actions";
    const status = document.createElement("span");
    status.className = "insight-action-status";
    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "insight-action-btn is-confirm";
    confirmBtn.textContent = "准"; // 准
    confirmBtn.title = "这个猜测准";
    const rejectBtn = document.createElement("button");
    rejectBtn.type = "button";
    rejectBtn.className = "insight-action-btn is-reject";
    rejectBtn.textContent = "不准"; // 不准
    rejectBtn.title = "这个猜测不准";
    confirmBtn.addEventListener("click", () =>
      handleInsightFeedback(item.hypothesis, "confirm", row, [confirmBtn, rejectBtn], status),
    );
    rejectBtn.addEventListener("click", () =>
      handleInsightFeedback(item.hypothesis, "reject", row, [confirmBtn, rejectBtn], status),
    );
    actions.append(confirmBtn, rejectBtn, status);
    row.append(actions);

    container.append(row);
  }
}

async function handleInsightFeedback(hypothesis, signal, row, buttons, statusEl) {
  for (const b of buttons) b.disabled = true;
  try {
    const res = await submitInsightFeedback(hypothesis, signal);
    if (res && res.matched) {
      if (typeof res.confidence === "number") {
        const pct = Math.round(res.confidence * 100);
        const fill = row.querySelector(".insight-confidence-fill");
        const label = row.querySelector(".insight-confidence-label");
        if (fill instanceof HTMLElement) fill.style.width = `${pct}%`;
        if (label) label.textContent = `${pct}%`;
      }
      row.classList.toggle("is-validated", Boolean(res.validated));
    }
    if (statusEl) {
      statusEl.textContent = signal === "confirm" ? "已确认 ✓" : "已记下，会少推这类";
    }
  } catch {
    for (const b of buttons) b.disabled = false;
    if (statusEl) statusEl.textContent = "没存上，稍后再试";
  }
}

function renderRecentAwareness(container, items) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!items || items.length === 0) {
    const fallback = document.createElement("p");
    fallback.className = "is-fallback";
    fallback.textContent = "最近还没有特别的观察，先多看一阵。";
    container.append(fallback);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "awareness-item";

    if (item.date || item.emotion_guess) {
      const header = document.createElement("div");
      header.className = "awareness-header";
      if (item.date) {
        const date = document.createElement("span");
        date.className = "awareness-date";
        date.textContent = item.date;
        header.append(date);
      }
      if (item.emotion_guess) {
        const emotion = document.createElement("span");
        emotion.className = "awareness-emotion";
        emotion.textContent = item.emotion_guess;
        header.append(emotion);
      }
      row.append(header);
    }

    const obs = document.createElement("p");
    obs.className = "awareness-observation";
    obs.textContent = item.observation;
    row.append(obs);

    if (item.trend) {
      const trend = document.createElement("p");
      trend.className = "awareness-trend";
      trend.textContent = item.trend;
      row.append(trend);
    }

    container.append(row);
  }
}

function renderMBTI(container, mbti) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!mbti || !mbti.type) {
    const fb = document.createElement("p");
    fb.className = "mbti-fallback";
    fb.textContent = "MBTI 还没推断出来，再多看一阵。";
    container.append(fb);
    return;
  }
  const typeRow = document.createElement("div");
  typeRow.className = "mbti-type-row";
  const typeLabel = document.createElement("span");
  typeLabel.className = "mbti-type-label";
  typeLabel.textContent = mbti.type;
  typeRow.append(typeLabel);
  if (typeof mbti.confidence === "number" && mbti.confidence > 0) {
    const conf = document.createElement("span");
    conf.className = "mbti-confidence";
    conf.textContent = `可信度 ${Math.round(mbti.confidence * 100)}%`;
    typeRow.append(conf);
  }
  container.append(typeRow);

  const dims = document.createElement("div");
  dims.className = "mbti-dimensions";
  // Dimension keys may be stored as "EI"/"SN"/"TF"/"JP" or "E_I"/"S_N"/"T_F"/"J_P"
  const dimOrder = ["EI", "SN", "TF", "JP"];
  for (const key of dimOrder) {
    const dim = mbti.dimensions?.[key] ?? mbti.dimensions?.[`${key[0]}_${key[1]}`];
    if (!dim) continue;
    const row = document.createElement("div");
    row.className = "mbti-dim-row";
    const pole = document.createElement("span");
    pole.className = "mbti-dim-pole";
    pole.textContent = dim.pole || key;
    const bar = document.createElement("div");
    bar.className = "mbti-dim-bar";
    const fill = document.createElement("div");
    fill.className = "mbti-dim-bar-fill";
    fill.style.width = `${Math.round((dim.strength ?? 0.5) * 100)}%`;
    bar.append(fill);
    const pct = document.createElement("span");
    pct.className = "mbti-dim-pct";
    pct.textContent = `${Math.round((dim.strength ?? 0.5) * 100)}%`;
    row.append(pole, bar, pct);
    dims.append(row);
  }
  container.append(dims);
}

function renderInterestTree(container, domains, fallback) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!domains || domains.length === 0) {
    const fb = document.createElement("p");
    fb.className = "is-fallback";
    fb.textContent = fallback;
    container.append(fb);
    return;
  }
  for (const dom of domains) {
    const block = document.createElement("div");
    block.className = "interest-domain";
    const header = document.createElement("div");
    header.className = "interest-domain-header";
    const name = document.createElement("span");
    name.textContent = dom.domain;
    header.append(name);
    if (dom.weight > 0) {
      const wt = document.createElement("span");
      wt.className = "interest-domain-weight";
      wt.textContent = `${Math.round(dom.weight * 100)}%`;
      header.append(wt);
    }
    block.append(header);
    if (dom.specifics && dom.specifics.length > 0) {
      const specs = document.createElement("div");
      specs.className = "interest-specifics";
      for (const spec of dom.specifics) {
        const chip = document.createElement("span");
        chip.className = "interest-specific-chip";
        chip.textContent = spec.name;
        specs.append(chip);
      }
      block.append(specs);
    }
    container.append(block);
  }
}

function renderStylePreference(container, style) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!style) {
    const fb = document.createElement("p");
    fb.className = "is-fallback";
    fb.textContent = "内容口味还在摸索中。";
    container.append(fb);
    return;
  }
  const durationLabels = { short: "短视频", medium: "中等", long: "长视频" };
  const paceLabels = { fast: "快节奏", moderate: "适中", slow: "慢节奏" };
  const textFields = [
    ["时长偏好", durationLabels[style.preferred_duration] || style.preferred_duration],
    ["节奏偏好", paceLabels[style.preferred_pace] || style.preferred_pace],
  ];
  for (const [label, value] of textFields) {
    if (!value) continue;
    const row = document.createElement("div");
    row.className = "style-text-row";
    const lbl = document.createElement("span");
    lbl.className = "style-text-label";
    lbl.textContent = label + "：";
    const val = document.createElement("span");
    val.className = "style-text-value";
    val.textContent = value;
    row.append(lbl, val);
    container.append(row);
  }
  const barFields = [
    ["深度偏好", style.depth_preference],
    ["画质敏感度", style.quality_sensitivity],
    ["幽默偏好", style.humor_preference],
  ];
  for (const [label, value] of barFields) {
    if (typeof value !== "number") continue;
    const row = document.createElement("div");
    row.className = "style-bar-row";
    const lbl = document.createElement("span");
    lbl.className = "style-bar-label";
    lbl.textContent = label;
    const track = document.createElement("div");
    track.className = "style-bar-track";
    const fill = document.createElement("div");
    fill.className = "style-bar-fill";
    fill.style.width = `${Math.round(value * 100)}%`;
    track.append(fill);
    const pct = document.createElement("span");
    pct.className = "style-bar-value";
    pct.textContent = `${Math.round(value * 100)}%`;
    row.append(lbl, track, pct);
    container.append(row);
  }
}

function renderContextMode(container, ctx) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!ctx) {
    const fb = document.createElement("p");
    fb.className = "is-fallback";
    fb.textContent = "使用场景还在观察中。";
    container.append(fb);
    return;
  }
  const fields = [
    ["工作日", ctx.weekday_patterns],
    ["周末", ctx.weekend_patterns],
    ["时段", ctx.time_of_day_patterns],
    ["模式", ctx.session_type],
  ];
  let hasAny = false;
  for (const [label, value] of fields) {
    if (!value) continue;
    hasAny = true;
    const row = document.createElement("div");
    row.className = "context-row";
    const lbl = document.createElement("span");
    lbl.className = "context-label";
    lbl.textContent = label + "：";
    const val = document.createElement("span");
    val.className = "context-value";
    val.textContent = value;
    row.append(lbl, val);
    container.append(row);
  }
  if (!hasAny) {
    const fb = document.createElement("p");
    fb.className = "is-fallback";
    fb.textContent = "使用场景还在观察中。";
    container.append(fb);
  }
}

function renderCognitionCards(container, items, fallback) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();

  if (items.length === 0) {
    const fallbackCard = document.createElement("div");
    fallbackCard.className = "cognition-card is-fallback";

    const summary = document.createElement("p");
    summary.className = "cognition-summary";
    summary.textContent = fallback;

    fallbackCard.append(summary);
    container.append(fallbackCard);
    return;
  }

  for (const [index, item] of items.entries()) {
    const card = document.createElement("article");
    const isExpanded = state.expandedCognitionIndex === index && item.expandable;
    card.className = `cognition-card${isExpanded ? " is-expanded" : ""}${item.expandable ? " is-expandable" : " is-summary-only"}`;

    const summaryButton = document.createElement(item.expandable ? "button" : "div");
    summaryButton.className = `cognition-toggle${item.expandable ? "" : " is-static"}`;
    if (summaryButton instanceof HTMLButtonElement) {
      summaryButton.type = "button";
      summaryButton.setAttribute("aria-expanded", String(isExpanded));
      summaryButton.addEventListener("click", () => {
        state.expandedCognitionIndex = getNextExpandedCognitionIndex(
          state.expandedCognitionIndex,
          index,
        );
        renderCognitionCards(container, items, fallback);
      });
    }

    const header = document.createElement("div");
    header.className = "cognition-header";

    const summaryText = document.createElement("p");
    summaryText.className = "cognition-summary";
    summaryText.textContent = item.summary;

    const contextLine = document.createElement("p");
    contextLine.className = "cognition-context";
    contextLine.textContent = item.contextLine;

    const meta = document.createElement("div");
    meta.className = "cognition-meta";
    if (item.source) {
      meta.dataset.source = item.source;
    }
    const source = document.createElement("span");
    source.className = item.source
      ? `cognition-source is-source-${item.source}`
      : "cognition-source";
    source.textContent = item.sourceLabel;
    if (item.source) {
      source.dataset.source = item.source;
    }

    const timestampLabel = formatRelativeTimestamp(item.created_at);
    const timestamp = document.createElement("time");
    timestamp.className = "cognition-timestamp";
    timestamp.textContent = timestampLabel;
    if (item.created_at) {
      timestamp.dateTime = item.created_at;
    }

    const stateLabel = document.createElement("span");
    stateLabel.className = "cognition-state";
    stateLabel.textContent = isExpanded ? "收起" : item.expandLabel;

    if (item.sourceLabel) {
      meta.append(source);
    }
    if (timestampLabel) {
      meta.append(timestamp);
    }
    meta.append(stateLabel);

    header.append(summaryText, contextLine, meta);
    summaryButton.append(header);
    card.append(summaryButton);

    if (item.expandable) {
      const details = document.createElement("div");
      details.className = "cognition-details";
      details.hidden = !isExpanded;

      const detailRows = [
        ["这对画像的影响", item.impact],
        ["为什么这么判断", item.reasoning],
        ["这次依据", item.evidence],
      ].filter(([, value]) => value);

      for (const [label, value] of detailRows) {
        const row = document.createElement("div");
        row.className = "cognition-detail";

        const labelEl = document.createElement("h4");
        labelEl.className = "cognition-detail-label";
        labelEl.textContent = label;

        const valueEl = document.createElement("p");
        valueEl.className = "cognition-detail-value";
        valueEl.textContent = value;

        row.append(labelEl, valueEl);
        details.append(row);
      }

      card.append(details);
    }

    container.append(card);
  }
}

function renderCognitionHistoryControls(historyState) {
  if (
    !(elements.profileRecentMemoryStatus instanceof HTMLElement) ||
    !(elements.profileRecentMemoryMore instanceof HTMLButtonElement)
  ) {
    return;
  }

  const uiState = getCognitionHistoryUiState(historyState);
  const hasItems = Array.isArray(historyState?.items) && historyState.items.length > 0;

  elements.profileRecentMemoryStatus.hidden = !uiState.statusMessage || !hasItems;
  elements.profileRecentMemoryStatus.textContent = uiState.loadingLabel || uiState.statusMessage;

  elements.profileRecentMemoryMore.hidden = !hasItems || (!historyState?.hasMore && !historyState?.loadMoreError);
  elements.profileRecentMemoryMore.disabled = !uiState.canLoadMore;
  elements.profileRecentMemoryMore.textContent = uiState.actionLabel;
}

function getProfileCognitionItems(summary) {
  if (Array.isArray(state.profileCognitionHistory.items) && state.profileCognitionHistory.items.length > 0) {
    return state.profileCognitionHistory.items;
  }
  return Array.isArray(summary?.recent_cognition_updates) ? summary.recent_cognition_updates : [];
}

// Split a long prose portrait into reader-friendly paragraphs.
// Old prompt produced 600-1000 char walls that needed aggressive
// splitting on every turn connector ("但"/"最近"/...). The new prompt
// caps portraits around 200-260 chars, where the same aggressive
// splitter chops the text into 5 isolated 1-2-sentence chunks that
// visually read as a list, not a flowing reflection.
//
// Heuristic: short portraits render as a single paragraph; only longer
// ones get sentence-grouped. Target paragraph length scales with total
// length so we don't over-fragment medium portraits either.
function splitPortraitToParagraphs(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return [];
  const totalLen = trimmed.length;

  if (totalLen < 280) return [trimmed];

  const sentences = trimmed
    .split(/(?<=[。！？.!?])\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (sentences.length <= 1) return sentences;

  const TURN_PREFIXES = ["但", "不过", "然而", "最近", "所以", "因此", "另外", "其实", "于是"];
  // Aim for ~3 paragraphs regardless of total length, with a minimum
  // grouping of 180 chars so we never produce <2-sentence stubs.
  const targetLen = Math.max(180, Math.ceil(totalLen / 3));

  const paragraphs = [];
  let buffer = [];
  let bufferLen = 0;

  const flush = () => {
    if (buffer.length === 0) return;
    paragraphs.push(buffer.join(""));
    buffer = [];
    bufferLen = 0;
  };

  for (const sentence of sentences) {
    const isTurn = TURN_PREFIXES.some((p) => sentence.startsWith(p));
    // Only split on turn-connector once the current paragraph already
    // has some weight — otherwise short opening sentences get orphaned.
    if (buffer.length > 0 && (bufferLen >= targetLen || (isTurn && bufferLen >= 100))) {
      flush();
    }
    buffer.push(sentence);
    bufferLen += sentence.length;
  }
  flush();
  return paragraphs;
}

function renderPortraitParagraphs(container, text) {
  if (!(container instanceof HTMLElement)) return;
  container.replaceChildren();
  const paragraphs = splitPortraitToParagraphs(text);
  for (const p of paragraphs) {
    const node = document.createElement("p");
    node.className = "profile-portrait-paragraph";
    node.textContent = p;
    container.append(node);
  }
}

function renderProfileSummary(summary) {
  if (
    !(elements.profileEmpty instanceof HTMLElement) ||
    !(elements.profileCard instanceof HTMLElement) ||
    !(elements.profileEmptyTitle instanceof HTMLElement) ||
    !(elements.profileEmptyText instanceof HTMLElement) ||
    !(elements.profilePortrait instanceof HTMLElement)
  ) {
    return;
  }

  if (!summary.initialized) {
    elements.profileCard.hidden = true;
    elements.profileEmpty.hidden = false;
    elements.profileEmptyTitle.textContent = "画像还没攒起来";
    elements.profileEmptyText.textContent =
      "还没初始化。去「推荐」页点『开始初始化』，攒好画像再回来看。";
    renderCognitionHistoryControls({
      items: [],
      hasMore: false,
      nextCursor: "",
      loadingMore: false,
      loadMoreError: "",
    });
    syncProfileEditChrome(false);
    return;
  }

  elements.profileEmpty.hidden = true;
  elements.profileCard.hidden = false;
  renderPortraitParagraphs(elements.profilePortrait, summary.personality_portrait);
  // Core
  renderChipList(elements.profileTraits, summary.core_traits, "这部分还在慢慢补");
  renderChipList(elements.profileNeeds, summary.deep_needs, "这块还要再多看一点");
  renderMBTI(elements.profileMBTI, summary.mbti);
  // Values
  renderChipList(elements.profileValues, summary.values, "价值偏好还在继续归拢");
  renderChipList(elements.profileMotivationalDrivers, summary.motivational_drivers, "这块还要再多看一点");
  // Interest
  renderInterestTree(elements.profileLikes, summary.likes, "再刷一阵，这里会更准");
  renderInterestTree(elements.profileDislikes, summary.dislikes, "这块还在继续确认，先别急着下死结论");
  renderChipList(elements.profileFavoriteUps, summary.favorite_up_users, "常看的 UP 主还在统计");
  // Role
  if (elements.profileLifeStage instanceof HTMLElement) {
    elements.profileLifeStage.textContent = summary.life_stage || "这块还在观察，先不急着定论。";
  }
  if (elements.profileCurrentPhase instanceof HTMLElement) {
    elements.profileCurrentPhase.textContent = summary.current_phase || "这阵子的变化还在继续看，先不急着下死结论。";
  }
  // Surface
  renderChipList(elements.profileCognitiveStyle, summary.cognitive_style, "这层还在继续归拢");
  renderStylePreference(elements.profileStyle, summary.style);
  renderContextMode(elements.profileContext, summary.context);
  renderExplorationBar(elements.profileExplorationOpenness, summary.exploration_openness);
  // Cross-cutting
  renderSpeculativeInterests(elements.profileSpeculativeInterests, summary.speculative_interests);
  renderSpeculativeInterests(elements.profileSpeculativeAvoidances, summary.speculative_avoidances, { kind: "avoidance" });
  renderCognitionCards(
    elements.profileRecentMemory,
    getProfileCognitionItems(summary),
    "阿B 还在继续观察，过一阵这里会更具体。",
  );
  renderCognitionHistoryControls(state.profileCognitionHistory);
  // Signals
  renderActiveInsights(elements.profileActiveInsights, summary.active_insights);
  renderRecentAwareness(elements.profileRecentAwareness, summary.recent_awareness);
  syncProfileEditChrome(true);
}

// ── Editable profile (Phase 2) ──────────────────────────────────────────
// Inline edit mode: the display card is hidden and an edit panel is rendered
// from GET /api/profile/edit-state (un-truncated). Each control posts one
// deterministic edit to /api/profile/edit and re-renders from the returned
// edit_state. Edits survive profile rebuilds (server-side overrides overlay).

let profileEditing = false;

const EDIT_FIELD_LABELS = {
  personality_portrait: "人格画像",
  "core.core_traits": "核心特质",
  "core.deep_needs": "深层需求",
  "values_layer.values": "价值偏好",
  "values_layer.motivational_drivers": "内在驱动力",
  likes: "感兴趣的方向",
  dislikes: "明显会避开",
  "interest.favorite_up_users": "常看的 UP 主",
  "role.life_stage": "人生阶段",
  "role.current_phase": "当前阶段",
  "surface.cognitive_style": "认知风格",
  "surface.exploration_openness": "探索开放度",
  "surface.style.quality_sensitivity": "质量敏感度",
  "surface.style.humor_preference": "幽默偏好",
  "surface.style.depth_preference": "深度偏好",
};
const EDIT_FIELD_ORDER = [
  "personality_portrait",
  "core.core_traits",
  "core.deep_needs",
  "values_layer.values",
  "values_layer.motivational_drivers",
  "likes",
  "dislikes",
  "interest.favorite_up_users",
  "role.life_stage",
  "role.current_phase",
  "surface.cognitive_style",
  "surface.exploration_openness",
  "surface.style.quality_sensitivity",
  "surface.style.humor_preference",
  "surface.style.depth_preference",
];

function setProfileEditingLayout(editing) {
  if (elements.viewProfile instanceof HTMLElement) {
    elements.viewProfile.classList.toggle("is-profile-editing", editing);
  }
}

function syncProfileEditChrome(initialized) {
  setProfileEditingLayout(profileEditing);
  if (elements.profileEditBar instanceof HTMLElement) {
    elements.profileEditBar.hidden = !initialized;
  }
  if (!initialized && profileEditing) {
    // Profile vanished while editing — bail out of edit mode quietly.
    exitProfileEditMode({ refresh: false });
    return;
  }
  if (initialized && profileEditing) {
    // Stay in edit mode even if a background refresh re-rendered the card.
    if (elements.profileCard instanceof HTMLElement) elements.profileCard.hidden = true;
    if (elements.profileEditPanel instanceof HTMLElement) elements.profileEditPanel.hidden = false;
  }
}

async function refreshEditPanel() {
  try {
    const editState = await fetchEditState();
    renderEditPanel(elements.profileEditPanel, editState);
  } catch (err) {
    console.error("load edit-state failed:", err);
  }
}

async function enterProfileEditMode() {
  profileEditing = true;
  setProfileEditingLayout(true);
  if (elements.profileCard instanceof HTMLElement) elements.profileCard.hidden = true;
  if (elements.profileEditPanel instanceof HTMLElement) elements.profileEditPanel.hidden = false;
  if (elements.profileEditHint instanceof HTMLElement) elements.profileEditHint.hidden = false;
  if (elements.profileEditToggle instanceof HTMLButtonElement) {
    elements.profileEditToggle.textContent = "✓ 完成";
  }
  await refreshEditPanel();
}

function exitProfileEditMode({ refresh = true } = {}) {
  profileEditing = false;
  setProfileEditingLayout(false);
  if (elements.profileEditPanel instanceof HTMLElement) {
    elements.profileEditPanel.hidden = true;
    elements.profileEditPanel.replaceChildren();
  }
  if (elements.profileEditHint instanceof HTMLElement) elements.profileEditHint.hidden = true;
  if (elements.profileEditToggle instanceof HTMLButtonElement) {
    elements.profileEditToggle.textContent = "✏️ 编辑画像";
  }
  if (elements.profileCard instanceof HTMLElement) elements.profileCard.hidden = false;
  if (refresh) void loadProfileSummary({ force: true });
}

function bindProfileEditToggle() {
  if (!(elements.profileEditToggle instanceof HTMLButtonElement)) return;
  elements.profileEditToggle.addEventListener("click", () => {
    if (profileEditing) exitProfileEditMode();
    else void enterProfileEditMode();
  });
}

async function applyProfileEdit(payload) {
  const panel = elements.profileEditPanel;
  if (panel instanceof HTMLElement) {
    panel.querySelectorAll("button, input, textarea").forEach((el) => {
      if (
        el instanceof HTMLButtonElement ||
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement
      ) {
        el.disabled = true;
      }
    });
  }
  try {
    const res = await submitProfileEdit(payload);
    const next =
      res && res.edit_state && res.edit_state.initialized
        ? res.edit_state
        : await fetchEditState();
    renderEditPanel(panel, next);
  } catch (err) {
    console.error("profile edit failed:", err);
    void refreshEditPanel();
  }
}

function makeEditedBadge() {
  const badge = document.createElement("span");
  badge.className = "edit-badge";
  badge.textContent = "已编辑";
  return badge;
}

function makeResetButton(path) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "edit-reset-btn";
  btn.textContent = "恢复 AI 建议";
  btn.addEventListener("click", () => void applyProfileEdit({ target: path, op: "reset" }));
  return btn;
}

function makeRemovableChip(label, onRemove) {
  const chip = document.createElement("span");
  chip.className = "edit-chip";
  const text = document.createElement("span");
  text.textContent = label;
  chip.append(text);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "edit-chip-remove";
  remove.textContent = "✕";
  remove.setAttribute("aria-label", `移除 ${label}`);
  remove.addEventListener("click", onRemove);
  chip.append(remove);
  return chip;
}

function makeAddRow(placeholder, onAdd) {
  const row = document.createElement("div");
  row.className = "edit-add-row";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "edit-add-input";
  input.placeholder = placeholder;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "action-button edit-add-btn";
  btn.textContent = "添加";
  const submit = () => {
    const value = input.value.trim();
    if (!value) return;
    input.value = "";
    void onAdd(value);
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  });
  row.append(input, btn);
  return row;
}

function makeEditFieldBlock(label, edited) {
  const block = document.createElement("div");
  block.className = "edit-field";
  const head = document.createElement("div");
  head.className = "edit-field-head";
  const title = document.createElement("span");
  title.className = "edit-field-label";
  title.textContent = label;
  head.append(title);
  if (edited) head.append(makeEditedBadge());
  block.append(head);
  return block;
}

function renderTextEditField(path, label, field) {
  const block = makeEditFieldBlock(label, Boolean(field.pinned));
  const textarea = document.createElement("textarea");
  textarea.className = "chat-input edit-text-input";
  textarea.rows = path === "personality_portrait" ? 4 : 2;
  textarea.value = typeof field.value === "string" ? field.value : "";
  block.append(textarea);

  if (field.ai_suggestion) {
    const hint = document.createElement("p");
    hint.className = "edit-drift-hint";
    hint.textContent = `AI 当前想更新为：${field.ai_suggestion}`;
    block.append(hint);
  }

  const actions = document.createElement("div");
  actions.className = "edit-field-actions";
  const editSaveBtn = document.createElement("button");
  editSaveBtn.type = "button";
  editSaveBtn.className = "action-button action-primary edit-save-btn";
  editSaveBtn.textContent = "保存";
  editSaveBtn.addEventListener("click", () => {
    const value = textarea.value.trim();
    if (!value) return;
    void applyProfileEdit({ target: path, op: "set", value });
  });
  actions.append(editSaveBtn);
  if (field.pinned) actions.append(makeResetButton(path));
  block.append(actions);
  return block;
}

// Scalar (0..1) fields render as a percent slider. Like text fields they
// commit on an explicit 保存 tap (not per-drag); the live label tracks the
// slider on input so the value is visible while dragging.
function renderScalarEditField(path, label, field) {
  const block = makeEditFieldBlock(label, Boolean(field.pinned));
  const pct = Math.round((Number(field.value) || 0) * 100);

  const row = document.createElement("div");
  row.className = "edit-scalar-row";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = "100";
  slider.step = "1";
  slider.value = String(pct);
  slider.className = "edit-scalar-input";
  const out = document.createElement("span");
  out.className = "edit-scalar-value";
  out.textContent = `${pct}%`;
  slider.addEventListener("input", () => {
    out.textContent = `${slider.value}%`;
  });
  row.append(slider, out);
  block.append(row);

  if (typeof field.ai_suggestion === "number") {
    const hint = document.createElement("p");
    hint.className = "edit-drift-hint";
    hint.textContent = `AI 当前想更新为：${Math.round(field.ai_suggestion * 100)}%`;
    block.append(hint);
  }

  const actions = document.createElement("div");
  actions.className = "edit-field-actions";
  // Named editSaveBtn (not saveBtn) to match renderTextEditField and avoid the
  // settings-test regex that anchors on the lowercase `saveBtn.addEventListener`.
  const editSaveBtn = document.createElement("button");
  editSaveBtn.type = "button";
  editSaveBtn.className = "action-button action-primary edit-save-btn";
  editSaveBtn.textContent = "保存";
  editSaveBtn.addEventListener("click", () => {
    void applyProfileEdit({ target: path, op: "set", value: Number(slider.value) / 100 });
  });
  actions.append(editSaveBtn);
  if (field.pinned) actions.append(makeResetButton(path));
  block.append(actions);
  return block;
}

function renderListEditField(path, label, field) {
  const items = Array.isArray(field.items) ? field.items : [];
  const added = Array.isArray(field.added) ? field.added : [];
  const removed = Array.isArray(field.removed) ? field.removed : [];
  const edited = added.length > 0 || removed.length > 0;
  const block = makeEditFieldBlock(label, edited);

  const chips = document.createElement("div");
  chips.className = "edit-chip-list";
  for (const item of items) {
    chips.append(
      makeRemovableChip(item, () => applyProfileEdit({ target: path, op: "remove", value: item })),
    );
  }
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "edit-empty";
    empty.textContent = "还没有，添加一个吧";
    chips.append(empty);
  }
  block.append(chips);
  block.append(makeAddRow("添加一项", (value) => applyProfileEdit({ target: path, op: "add", value })));
  if (edited) {
    const actions = document.createElement("div");
    actions.className = "edit-field-actions";
    actions.append(makeResetButton(path));
    block.append(actions);
  }
  return block;
}

function renderInterestEditField(path, label, field) {
  const domains = Array.isArray(field.domains) ? field.domains : [];
  const removed = Array.isArray(field.removed_domains) ? field.removed_domains : [];
  const edited = removed.length > 0 || domains.some((d) => d && d.user_added);
  const block = makeEditFieldBlock(label, edited);

  const chips = document.createElement("div");
  chips.className = "edit-chip-list";
  for (const dom of domains) {
    if (!dom || !dom.domain) continue;
    const name = dom.user_added ? `${dom.domain} ＋` : dom.domain;
    chips.append(
      makeRemovableChip(name, () =>
        applyProfileEdit({ target: path, op: "remove", value: dom.domain }),
      ),
    );
  }
  if (domains.length === 0) {
    const empty = document.createElement("p");
    empty.className = "edit-empty";
    empty.textContent = "还没有，添加一个吧";
    chips.append(empty);
  }
  block.append(chips);
  const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
  block.append(makeAddRow(placeholder, (value) => applyProfileEdit({ target: path, op: "add", value })));
  if (edited) {
    const actions = document.createElement("div");
    actions.className = "edit-field-actions";
    actions.append(makeResetButton(path));
    block.append(actions);
  }
  return block;
}

function renderEditPanel(container, editState) {
  if (!(container instanceof HTMLElement)) return;
  container.replaceChildren();
  if (!editState || !editState.initialized || !editState.fields) {
    const note = document.createElement("p");
    note.className = "profile-edit-note";
    note.textContent =
      "还没初始化。去「推荐」页点『开始初始化』，画像攒好后再来编辑。";
    container.append(note);
    return;
  }
  const intro = document.createElement("p");
  intro.className = "profile-edit-note";
  intro.textContent =
    "标签 / 兴趣类增删即时生效；文本与滑杆类改完点「保存」才生效。改动都不会被后续自动重建覆盖，删错了点「恢复 AI 建议」即可。";
  container.append(intro);

  const fields = editState.fields;
  for (const path of EDIT_FIELD_ORDER) {
    const field = fields[path];
    if (!field || typeof field !== "object") continue;
    const label = EDIT_FIELD_LABELS[path] || path;
    let block = null;
    if (field.type === "text") block = renderTextEditField(path, label, field);
    else if (field.type === "scalar") block = renderScalarEditField(path, label, field);
    else if (field.type === "list") block = renderListEditField(path, label, field);
    else if (field.type === "interest") block = renderInterestEditField(path, label, field);
    if (block) container.append(block);
  }
}

function appendChatMessage(role, content, { turnId = "", part = "" } = {}) {
  if (!(elements.chatMessages instanceof HTMLElement)) {
    return null;
  }
  const item = document.createElement("div");
  item.className = `chat-message${role === "你" ? " user" : ""}`;
  if (turnId) item.dataset.turnId = turnId;
  if (part) item.dataset.part = part;

  const label = document.createElement("span");
  label.className = "chat-role";
  label.textContent = role;

  const text = document.createElement("p");
  text.className = "chat-content";
  text.textContent = content;

  item.append(label, text);
  elements.chatMessages.append(item);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  return item;
}

// Render a placeholder "thinking" bubble with animated dots while we
// wait for the dialogue endpoint. Returns the bubble element so the
// submit handler can swap it for the real reply (or an error) once
// the request resolves.
function appendChatThinkingPlaceholder(turnId = "") {
  if (!(elements.chatMessages instanceof HTMLElement)) {
    return null;
  }
  const item = document.createElement("div");
  item.className = "chat-message chat-thinking";
  if (turnId) {
    item.dataset.turnId = turnId;
    item.dataset.part = "assistant";
  }

  const label = document.createElement("span");
  label.className = "chat-role";
  label.textContent = "助手";

  const text = document.createElement("p");
  text.className = "chat-content chat-thinking-content";
  text.innerHTML =
    '<span class="chat-thinking-label">正在想</span>' +
    '<span class="chat-thinking-dots">' +
    '<span class="chat-thinking-dot"></span>' +
    '<span class="chat-thinking-dot"></span>' +
    '<span class="chat-thinking-dot"></span>' +
    "</span>";

  item.append(label, text);
  elements.chatMessages.append(item);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  return item;
}

// Replace a previously-inserted placeholder with the final assistant
// reply text in-place, so the visual position stays stable.
function replaceChatThinkingPlaceholder(placeholder, content) {
  if (!(placeholder instanceof HTMLElement)) {
    return;
  }
  placeholder.classList.remove("chat-thinking");
  const text = placeholder.querySelector(".chat-content");
  if (text instanceof HTMLElement) {
    text.classList.remove("chat-thinking-content");
    text.textContent = content;
  }
  if (elements.chatMessages instanceof HTMLElement) {
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  }
}

const DELIGHT_LOCAL_STATE_KEY = "openbiliclaw_delight_local";
// Fields added locally (not from backend) that must survive a panel reload.
const DELIGHT_PERSIST_FIELDS = [
  "chat_draft",
  "chat_reply",
  "chat_turn_id",
  "state",
  "response_message",
  "expanded",
  "composer_open",
  "turns",
];

function persistDelightLocalState(bvid, updates) {
  const relevant = Object.fromEntries(
    Object.entries(updates).filter(([k]) => DELIGHT_PERSIST_FIELDS.includes(k)),
  );
  if (Object.keys(relevant).length === 0) return;
  try {
    const raw =
      localStorage.getItem(DELIGHT_LOCAL_STATE_KEY) ||
      sessionStorage.getItem(DELIGHT_LOCAL_STATE_KEY);
    const all = raw ? JSON.parse(raw) : {};
    all[bvid] = { ...(all[bvid] ?? {}), ...relevant };
    localStorage.setItem(DELIGHT_LOCAL_STATE_KEY, JSON.stringify(all));
  } catch {
    // silent fallback
  }
}

function createClientTurnId(prefix = "turn") {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  return `${prefix}-${String(random).replace(/[^a-zA-Z0-9_-]/g, "")}`;
}

function findChatTurnElement(turnId, part) {
  if (!(elements.chatMessages instanceof HTMLElement) || !turnId) {
    return null;
  }
  return elements.chatMessages.querySelector(
    `[data-turn-id="${CSS.escape(turnId)}"][data-part="${CSS.escape(part)}"]`,
  );
}

function renderChatTurn(turn) {
  if (!turn?.turn_id || !(elements.chatMessages instanceof HTMLElement)) {
    return;
  }
  const userPart = findChatTurnElement(turn.turn_id, "user");
  if (!userPart) {
    appendChatMessage("你", turn.message || "", {
      turnId: turn.turn_id,
      part: "user",
    });
  }

  const assistantPart = findChatTurnElement(turn.turn_id, "assistant");
  const status = String(turn.status || "pending");
  if (status === "completed") {
    if (assistantPart instanceof HTMLElement) {
      replaceChatThinkingPlaceholder(assistantPart, turn.reply || "");
    } else {
      appendChatMessage("助手", turn.reply || "", {
        turnId: turn.turn_id,
        part: "assistant",
      });
    }
    return;
  }
  if (status === "failed") {
    const message = turn.reply || "刚刚没发出去，换个说法再试试。";
    if (assistantPart instanceof HTMLElement) {
      replaceChatThinkingPlaceholder(assistantPart, message);
    } else {
      appendChatMessage("助手", message, {
        turnId: turn.turn_id,
        part: "assistant",
      });
    }
    return;
  }
  if (!assistantPart) {
    appendChatThinkingPlaceholder(turn.turn_id);
  }
}

function applyTurnToDelight(turn) {
  if (!turn || turn.scope !== "delight" || !turn.subject_id) return;
  const idx = state.activeDelights.findIndex((item) => item?.bvid === turn.subject_id);
  if (idx < 0) return;

  // Maintain per-delight turns array
  const existing = state.activeDelights[idx];
  const prevTurns = Array.isArray(existing.turns) ? existing.turns : [];
  const turnEntry = {
    turn_id: turn.turn_id,
    message: turn.message || "",
    reply: turn.reply || "",
    status: turn.status || "pending",
    error: turn.error || "",
  };
  const turnIdx = prevTurns.findIndex((t) => t.turn_id === turn.turn_id);
  const updatedTurns = turnIdx >= 0
    ? prevTurns.map((t, i) => i === turnIdx ? turnEntry : t)
    : [...prevTurns, turnEntry];

  const updates = {
    chat_turn_id: turn.turn_id,
    expanded: true,
    turns: updatedTurns,
  };
  if (turn.status === "completed") {
    Object.assign(updates, {
      state: "chatted",
      response_message: "这句已经记下，后面会更会试探。",
      chat_reply: turn.reply || "",
      chat_draft: "",
      composer_open: false,
    });
  } else if (turn.status === "failed") {
    Object.assign(updates, {
      state: "pending",
      response_message: "这句还没发出去，稍后再试。",
      composer_open: true,
    });
  } else {
    Object.assign(updates, {
      state: "chatting",
      response_message: "阿B 正在品你这句话。",
      composer_open: false,
    });
  }
  state.activeDelights[idx] = { ...state.activeDelights[idx], ...updates };
  persistDelightLocalState(turn.subject_id, updates);
  syncDelightHead();
}

function applyTurnToMessage(turn) {
  if (!turn || !turn.subject_id) return;
  const type = turn.scope === "delight"
    ? "delight"
    : turn.scope === "avoidance_probe"
      ? "avoidance.probe"
      : "interest.probe";
  const idx = state.messages.findIndex((item) => {
    const itemType = item?.type || "interest.probe";
    return (
      itemType === type &&
      (type === "delight" ? item.bvid === turn.subject_id : item.domain === turn.subject_id)
    );
  });
  if (idx < 0) return;
  state.messages[idx] = {
    ...state.messages[idx],
    chat_turn_id: turn.turn_id,
    chat_status: turn.status,
    chat_reply: turn.status === "completed" ? turn.reply || "" : state.messages[idx].chat_reply || "",
  };
}

function pollChatTurnUntilSettled(turnId, { onUpdate, onDone } = {}) {
  if (!turnId || activeChatPolls.has(turnId)) return;
  const startedAt = Date.now();

  async function tick() {
    try {
      const turn = await fetchChatTurn(turnId);
      onUpdate?.(turn);
      if (turn.status === "completed" || turn.status === "failed") {
        activeChatPolls.delete(turnId);
        await onDone?.(turn);
        return;
      }
    } catch {
      // Keep polling until the deadline; reload recovery is best-effort
      // while the backend or network is temporarily unavailable.
    }
    if (Date.now() - startedAt > CHAT_POLL_DEADLINE_MS) {
      activeChatPolls.delete(turnId);
      return;
    }
    const timeoutId = window.setTimeout(tick, CHAT_POLL_INTERVAL_MS);
    activeChatPolls.set(turnId, timeoutId);
  }

  activeChatPolls.set(turnId, 0);
  void tick();
}

async function refreshAfterChatTurn() {
  await refreshProfileSummaryAfterInteraction({
    onProfileStart() {
      setChatStatus(getSubmissionProgressMessage("chat", "refreshing_profile"), "info");
    },
    onActivityStart() {
      setChatStatus(getSubmissionProgressMessage("chat", "refreshing_activity"), "info");
    },
    onDone() {
      setChatStatus(getSubmissionProgressMessage("chat", "success"), "success");
    },
  });
}

async function hydrateChatHistory() {
  if (!(elements.chatMessages instanceof HTMLElement) || !state.online) {
    return;
  }
  try {
    const payload = await fetchChatTurns({ session: CHAT_SESSION, scope: "chat", limit: 50 });
    elements.chatMessages.replaceChildren();
    for (const turn of payload.items || []) {
      renderChatTurn(turn);
      if (turn.status === "pending") {
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate: renderChatTurn,
          onDone: refreshAfterChatTurn,
        });
      }
    }
  } catch {
    // History is opportunistic; core panel loading should continue offline.
  }
}

async function syncScopedChatTurns() {
  if (!state.online) return;
  try {
    const [delightTurns, probeTurns, avoidanceProbeTurns] = await Promise.all([
      fetchChatTurns({ session: CHAT_SESSION, scope: "delight", limit: 80 }),
      fetchChatTurns({ session: CHAT_SESSION, scope: "probe", limit: 80 }),
      fetchChatTurns({ session: CHAT_SESSION, scope: "avoidance_probe", limit: 80 }),
    ]);
    for (const turn of delightTurns.items || []) {
      applyTurnToDelight(turn);
      applyTurnToMessage(turn);
      if (turn.status === "pending") {
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate(nextTurn) {
            applyTurnToDelight(nextTurn);
            applyTurnToMessage(nextTurn);
            renderDelightSlot();
            renderMessagesList();
          },
        });
      }
    }
    for (const turn of probeTurns.items || []) {
      applyTurnToMessage(turn);
      if (turn.status === "pending") {
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate(nextTurn) {
            applyTurnToMessage(nextTurn);
            renderMessagesList();
          },
        });
      }
    }
    for (const turn of avoidanceProbeTurns.items || []) {
      applyTurnToMessage(turn);
      if (turn.status === "pending") {
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate(nextTurn) {
            applyTurnToMessage(nextTurn);
            renderMessagesList();
          },
        });
      }
    }
  } catch {
    // Scoped turn hydration is best-effort; backend fetches on init heal it.
  }
}

function setFeedbackStatus(statusLine, message) {
  statusLine.textContent = message;
  statusLine.hidden = !message;
  statusLine.dataset.tone = "info";
}

function setFeedbackStatusWithTone(statusLine, message, tone = "info") {
  statusLine.textContent = message;
  statusLine.hidden = !message;
  statusLine.dataset.tone = tone;
}

function setChatStatus(message, tone = "info") {
  if (!(elements.chatStatus instanceof HTMLElement)) {
    return;
  }
  elements.chatStatus.textContent = message;
  elements.chatStatus.dataset.tone = tone;
}

function clearActiveFeedbackProgress() {
  if (state.activeFeedbackProgress?.timeoutId != null) {
    window.clearTimeout(state.activeFeedbackProgress.timeoutId);
  }
  state.activeFeedbackProgress = null;
}

function attachFeedbackRuntimeProgress(statusLine) {
  clearActiveFeedbackProgress();
  const activeFeedbackProgress = {
    timeoutId: window.setTimeout(() => {
      if (state.activeFeedbackProgress === activeFeedbackProgress) {
        state.activeFeedbackProgress = null;
      }
    }, 12000),
    handle(event) {
      const runtimeState = getRuntimeRefreshSubmissionState(event);
      if (runtimeState == null) {
        return;
      }
      setFeedbackStatusWithTone(statusLine, runtimeState.message, runtimeState.tone);
      if (runtimeState.done) {
        clearActiveFeedbackProgress();
      }
    },
  };
  state.activeFeedbackProgress = activeFeedbackProgress;
}

/**
 * Open a recommendation's Bilibili page and report the click-through to
 * the backend as a strong profile signal. The report is best-effort and
 * fires in parallel with tab creation so the user never waits.
 *
 * @param {string} bvid
 * @param {{
 *   id?: number,
 *   title?: string,
 *   topic_label?: string,
 *   up_name?: string,
 *   content_id?: string,
 *   content_url?: string,
 *   source_platform?: string,
 * }} [context]
 */
async function openRecommendation(bvid, context = {}) {
  const url = buildContentUrl(context);
  if (!url) {
    setHint("这条卡片还没挂上链接，稍后再试。", "error");
    return;
  }
  // Fire-and-forget click report (best effort). Runs in parallel with tab.create.
  void reportRecommendationClick(
    buildRecommendationClickPayload(
      { ...context, bvid: bvid || context.bvid || context.content_id || "" },
      url,
    ),
  );
  await chrome.tabs.create({ url });
}

function createActionButton(label, className, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onClick();
  });
  return button;
}

function renderDelightSlot() {
  if (!(elements.delightSlot instanceof HTMLElement)) {
    return;
  }

  const queueLength = state.activeDelights.length;
  const currentIdx = state.delightCurrentIndex;
  const head = state.activeDelights[currentIdx];
  const uiState = getDelightUiState(head, {
    highlightBvid: state.delightHighlightBvid,
  });

  if (!uiState.visible || !head?.bvid) {
    elements.delightSlot.hidden = true;
    elements.delightSlot.replaceChildren();
    return;
  }

  const delight = head;
  const isHandled = uiState.handled;
  const isChatting = delight.state === "chatting";
  const isExpanded = Boolean(delight.expanded);

  // Banner with thumbnail. Collapsed = ~64px row showing thumbnail +
  // hook + truncated title + position counter (when more than one
  // delight is queued). Click the row to expand; × dismisses just
  // the head and the next delight slides in.
  const banner = document.createElement("article");
  banner.className =
    `delight-banner${isExpanded ? " is-expanded" : ""}` +
    `${uiState.highlighted ? " is-highlighted" : ""}`;
  banner.dataset.state = delight.state || "pending";

  // ── Row (always visible) ────────────────────────────────────────
  const row = document.createElement("button");
  row.type = "button";
  row.className = "delight-banner-row";
  row.setAttribute("aria-expanded", isExpanded ? "true" : "false");
  row.addEventListener("click", () => {
    updateDelightHead({ expanded: !isExpanded });
    renderDelightSlot();
  });

  // Thumbnail (left)
  const thumb = document.createElement("span");
  thumb.className = "delight-banner-thumb";
  if (delight.cover_url) {
    const image = document.createElement("img");
    void setProxyImageSrc(image, delight.cover_url);
    image.alt = "";
    image.addEventListener("error", () => {
      image.remove();
      thumb.classList.add("is-fallback");
      thumb.textContent = "✨";
    });
    thumb.append(image);
  } else {
    thumb.classList.add("is-fallback");
    thumb.textContent = "✨";
  }

  // Text column
  const textCol = document.createElement("span");
  textCol.className = "delight-banner-text";

  const kickerLine = document.createElement("span");
  kickerLine.className = "delight-banner-kicker-line";
  const kicker = document.createElement("span");
  kicker.className = "delight-banner-kicker";
  kicker.textContent = `✨ ${delight.delight_hook || "惊喜推荐"}`;
  kickerLine.append(kicker);
  if (queueLength > 1) {
    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "delight-banner-nav";
    prevBtn.textContent = "\u2039";  // ‹
    prevBtn.title = "上一条";
    prevBtn.disabled = currentIdx <= 0;
    prevBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      navigateDelight(-1);
      renderDelightSlot();
    });

    const counter = document.createElement("span");
    counter.className = "delight-banner-counter";
    counter.textContent = `${currentIdx + 1}/${queueLength}`;

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "delight-banner-nav";
    nextBtn.textContent = "\u203A";  // ›
    nextBtn.title = "下一条";
    nextBtn.disabled = currentIdx >= queueLength - 1;
    nextBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      navigateDelight(1);
      renderDelightSlot();
    });

    kickerLine.append(prevBtn, counter, nextBtn);
  }

  const titleText = document.createElement("span");
  titleText.className = "delight-banner-title";
  titleText.textContent = delight.title || "";

  textCol.append(kickerLine, titleText);

  const chevron = document.createElement("span");
  chevron.className = "delight-banner-chevron";
  chevron.textContent = isExpanded ? "▾" : "▸";

  row.append(thumb, textCol, chevron);

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "delight-banner-dismiss";
  dismiss.title = "稍后看";
  dismiss.setAttribute("aria-label", "关闭这条惊喜推荐");
  dismiss.textContent = "×";
  dismiss.addEventListener("click", (event) => {
    event.stopPropagation();
    rememberDismissedDelight(delight.bvid);
    shiftDelightQueue();
    setHint(
      state.activeDelights.length > 0
        ? "这条收起了，下一条上。"
        : "先给你收起来，回头想看再翻。",
      "info",
    );
    renderDelightSlot();
  });

  banner.append(row, dismiss);

  // ── Expanded body ───────────────────────────────────────────────
  if (isExpanded) {
    const body = document.createElement("div");
    body.className = "delight-banner-body";

    if (delight.delight_reason) {
      const reason = document.createElement("p");
      reason.className = "delight-banner-reason";
      reason.textContent = delight.delight_reason;
      body.append(reason);
    }

    if (uiState.response_message) {
      const response = document.createElement("p");
      response.className = "delight-banner-response";
      response.dataset.tone = uiState.response_tone;
      response.textContent = uiState.response_message;
      body.append(response);
    }

    // Multi-turn chat bubbles (turns is the authority; chat_reply is compat)
    const turns = Array.isArray(delight.turns) ? delight.turns : [];
    if (turns.length > 0) {
      const bubbleArea = document.createElement("div");
      bubbleArea.className = "delight-chat-turns";
      for (const t of turns) {
        const userBubble = document.createElement("div");
        userBubble.className = "delight-turn-bubble is-user";
        userBubble.textContent = t.message;
        bubbleArea.append(userBubble);
        const aiBubble = document.createElement("div");
        if (t.status === "pending") {
          aiBubble.className = "delight-turn-bubble is-assistant is-thinking";
          aiBubble.textContent = "阿B 正在品你这句话…";
        } else if (t.status === "failed") {
          aiBubble.className = "delight-turn-bubble is-assistant is-error";
          aiBubble.textContent = t.error || "这句还没发出去，稍后再试。";
        } else {
          aiBubble.className = "delight-turn-bubble is-assistant";
          aiBubble.textContent = t.reply || "";
        }
        bubbleArea.append(aiBubble);
      }
      body.append(bubbleArea);
    } else if (delight.chat_reply) {
      // Fallback: show single chat_reply for backward compat
      const reply = document.createElement("p");
      reply.className = "delight-banner-chat-reply";
      reply.textContent = delight.chat_reply;
      body.append(reply);
    }

    const actions = document.createElement("div");
    actions.className = "delight-banner-actions";

    const openButton = createActionButton(
      "看看",
      "action-button action-primary delight-banner-action",
      async () => {
        await openRecommendation(delight.bvid, delight);
        // 浏览过即已读：上报 view 让后端标记 delight_notified，
        // 下次重灌不再出现。当场卡片保留 viewed 状态。
        respondToDelight(delight.bvid, "view", delight.title).catch(() => {});
        updateDelightHead({
          state: "viewed",
          response_message: "已打开，阿B 会把这次点击当成强信号。",
          composer_open: false,
          expanded: true,
        });
        renderDelightSlot();
      },
    );

    const likeButton = createActionButton(
      "喜欢",
      "action-button action-secondary delight-banner-action is-like",
      async () => {
        try {
          await respondToDelight(delight.bvid, "like", delight.title);
        } catch (err) {
          console.error("Delight like failed:", err);
        }
        setHint("好，这类多来点。", "success");
        updateDelightHead({
          state: "liked",
          response_message: "好，这类多来点。",
          composer_open: false,
          expanded: true,
        });
        renderDelightSlot();
      },
    );

    const rejectButton = createActionButton(
      "不感兴趣",
      "action-button action-secondary delight-banner-action",
      async () => {
        try {
          await respondToDelight(delight.bvid, "dislike", delight.title);
        } catch (err) {
          console.error("Delight dislike failed:", err);
        }
        rememberDismissedDelight(delight.bvid);
        removeCurrentDelight();
        setHint("记下了，这类惊喜先少来点。", "success");
        renderDelightSlot();
      },
    );

    const chatButton = createActionButton(
      "聊一聊",
      "action-button action-secondary delight-banner-action",
      () => {
        updateDelightHead({
          composer_open: !delight.composer_open,
          expanded: true,
        });
        renderDelightSlot();
      },
    );

    // \u7A0D\u540E\u518D\u770B (\u2606) \u2014 ephemeral queue
    // \u7A0D\u540E\u518D\u770B = \u65F6\u949F\u56FE\u6807\uFF08\u72B6\u6001\u8D70 aria-pressed + CSS\uFF0C\u4E0D\u505A\u5B57\u5F62\u66FF\u6362\uFF09
    const delightWatchLaterButton = (() => {
      const btn = createActionButton("", "action-button action-secondary delight-banner-action delight-save-toggle watch-later-btn", async () => {
        try {
          await toggleWatchLaterSaved(delight.bvid);
        } catch {
          // Registry already rolled back the optimistic state.
        }
      });
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
      bindWatchLaterToggle(btn, delight.bvid);
      return btn;
    })();

    // \u6536\u85CF = \u661F\u661F\u56FE\u6807\uFF0C\u4E0E\u7A0D\u540E\u518D\u770B\u76F8\u4E92\u72EC\u7ACB
    const delightFavoriteButton = (() => {
      const btn = createActionButton("", "action-button action-secondary delight-banner-action delight-save-toggle favorite-btn", async () => {
        try {
          await toggleFavoriteSaved(delight.bvid);
        } catch {
          // Registry already rolled back the optimistic state.
        }
      });
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';
      bindFavoriteToggle(btn, delight.bvid);
      return btn;
    })();

    if (isHandled || isChatting) {
      rejectButton.disabled = true;
      likeButton.disabled = true;
    }

    actions.append(
      openButton,
      likeButton,
      delightWatchLaterButton,
      delightFavoriteButton,
      rejectButton,
      chatButton,
    );
    body.append(actions);

    if (delight.composer_open) {
      const composer = document.createElement("div");
      composer.className = "delight-chat-composer";
      let sendInitiated = false;

      const input = document.createElement("textarea");
      input.className = "chat-input";
      input.rows = 3;
      input.placeholder = "说说你为什么想点开，或者哪里还拿不准";
      input.value = delight.chat_draft || "";
      input.addEventListener("input", () => {
        if (state.activeDelights[state.delightCurrentIndex]?.bvid === delight.bvid) {
          updateDelightHead({ chat_draft: input.value });
        }
      });

      // Collapse the composer back to the action buttons when focus leaves it
      // (the user opened 聊一聊 then changed their mind). The draft is kept in
      // chat_draft so reopening restores it; a real send is guarded so tapping
      // 发出去 isn't lost (its blur fires before the click in some browsers).
      input.addEventListener("blur", (event) => {
        if (event.relatedTarget && composer.contains(event.relatedTarget)) return;
        setTimeout(() => {
          if (sendInitiated) return;
          if (composer.contains(document.activeElement)) return;
          const cur = state.activeDelights[state.delightCurrentIndex];
          if (!cur || cur.bvid !== delight.bvid || !cur.composer_open) return;
          updateDelightHead({ composer_open: false, chat_draft: input.value });
          renderDelightSlot();
        }, 120);
      });

      const status = document.createElement("p");
      status.className = "delight-chat-status";

      const submit = createActionButton(
        "发出去",
        "action-button action-primary",
        async () => {
          const draft = input.value.trim();
          if (!draft) {
            status.textContent = "先写一句你的想法。";
            input.focus();
            return;
          }
          sendInitiated = true;
          submit.disabled = true;
          const turnId = createClientTurnId("delight");
          // Optimistically append to turns array
          const prevTurns = Array.isArray(delight.turns) ? delight.turns : [];
          updateDelightHead({
            state: "chatting",
            response_message: "阿B 正在品你这句话。",
            chat_turn_id: turnId,
            chat_draft: draft,
            composer_open: false,
            expanded: true,
            turns: [...prevTurns, { turn_id: turnId, message: draft, reply: "", status: "pending", error: "" }],
          });
          renderDelightSlot();
          status.replaceChildren();
          status.append(
            createChatThinkingPlaceholder("阿B 正在品你这句话"),
          );
          try {
            const turn = await startChatTurn({
              turnId,
              session: CHAT_SESSION,
              scope: "delight",
              subjectId: delight.bvid,
              subjectTitle: delight.title || "",
              message: draft,
            });
            applyTurnToDelight(turn);
            applyTurnToMessage(turn);
            renderDelightSlot();
            if (turn.status === "completed") {
              setHint("这句记下了，后面的惊喜推荐会继续学。", "success");
            } else if (turn.status === "pending") {
              pollChatTurnUntilSettled(turn.turn_id, {
                onUpdate(nextTurn) {
                  applyTurnToDelight(nextTurn);
                  applyTurnToMessage(nextTurn);
                  renderDelightSlot();
                },
                async onDone(doneTurn) {
                  if (doneTurn.status === "completed") {
                    setHint("这句记下了，后面的惊喜推荐会继续学。", "success");
                  }
                  await refreshProfileSummaryAfterInteraction({
                    onProfileStart() {
                      setHint("正在同步画像。", "info");
                    },
                    onActivityStart() {
                      setHint("画像已同步，正在刷新最近动态。", "info");
                    },
                  });
                },
              });
            }
            if (turn.status === "completed" || turn.status === "failed") {
              await refreshProfileSummaryAfterInteraction({
                onProfileStart() {
                  setHint("正在同步画像。", "info");
                },
                onActivityStart() {
                  setHint("画像已同步，正在刷新最近动态。", "info");
                },
              });
            }
          } catch {
            submit.disabled = false;
            updateDelightHead({
              state: "pending",
              response_message: "这句还没发出去，稍后再试。",
              composer_open: true,
              expanded: true,
            });
            renderDelightSlot();
          }
        },
      );

      composer.append(input, submit, status);
      body.append(composer);
    }

    if (queueLength >= 5) {
      const dismissAll = document.createElement("button");
      dismissAll.type = "button";
      dismissAll.className = "delight-banner-dismiss-all";
      dismissAll.textContent = `全部稍后看 (${queueLength})`;
      dismissAll.addEventListener("click", (event) => {
        event.stopPropagation();
        for (const d of state.activeDelights) rememberDismissedDelight(d.bvid);
        clearDelightQueue();
        setHint("都收起来了，需要时去邮箱里翻。", "info");
        renderDelightSlot();
      });
      body.append(dismissAll);
    }

    banner.append(body);
  }

  elements.delightSlot.hidden = false;
  elements.delightSlot.replaceChildren(banner);
  // The previous banner's save toggles are now detached; drop them so the
  // shared registries don't grow across the delight banner's frequent re-renders.
  watchLaterToggles.pruneDetached();
  favoriteToggles.pruneDetached();
}

function createCommentComposer(item, statusLine) {
  const wrapper = document.createElement("div");
  wrapper.className = "comment-composer";
  wrapper.hidden = true;

  const input = document.createElement("textarea");
  input.className = "comment-input";
  input.rows = 3;
  input.placeholder = "写一句你为什么想看，或者为什么不想看";

  let hideTimer = null;

  function clearHideTimer() {
    if (hideTimer !== null) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
  }

  function applySubmitUiState(stateName) {
    const uiState = getCommentSubmitUiState(stateName);
    submit.textContent = uiState.buttonLabel;
    submit.disabled = uiState.disabled;
    input.disabled = stateName === "submitting";
    if (stateName !== "idle") {
      setFeedbackStatus(statusLine, uiState.statusMessage);
    }
  }

  function resetComposerUi() {
    clearHideTimer();
    applySubmitUiState("idle");
    input.disabled = false;
  }

  const submit = createActionButton("发出去", "action-button action-primary", async () => {
    const validation = validateCommentInput(input.value);
    if (!validation.valid) {
      setHint(validation.message, "error");
      input.focus();
      return;
    }
    resetComposerUi();
    applySubmitUiState("submitting");
    setFeedbackStatusWithTone(
      statusLine,
      getSubmissionProgressMessage("feedback", "submitting"),
      "info",
    );
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "comment", input.value));
      applySubmitUiState("success");
      setHint("这句记下了。", "success");
      setFeedbackStatusWithTone(
        statusLine,
        getSubmissionProgressMessage("feedback", "accepted"),
        "info",
      );
      attachFeedbackRuntimeProgress(statusLine);
      input.value = "";
      clearHideTimer();
      hideTimer = window.setTimeout(() => {
        wrapper.hidden = true;
        resetComposerUi();
      }, 600);
      await refreshProfileSummaryAfterInteraction({
        onProfileStart() {
          setFeedbackStatusWithTone(
            statusLine,
            getSubmissionProgressMessage("feedback", "refreshing_profile"),
            "info",
          );
        },
        onActivityStart() {
          setFeedbackStatusWithTone(
            statusLine,
            getSubmissionProgressMessage("feedback", "refreshing_activity"),
            "info",
          );
        },
        onDone() {
          if (state.activeFeedbackProgress == null) {
            setFeedbackStatusWithTone(
              statusLine,
              getSubmissionProgressMessage("feedback", "success"),
              "success",
            );
          }
        },
      });
    } catch {
      applySubmitUiState("error");
      clearActiveFeedbackProgress();
      setFeedbackStatusWithTone(
        statusLine,
        getSubmissionProgressMessage("feedback", "error"),
        "error",
      );
      setHint("这句没发出去，先看看本地后端是不是开着。", "error");
    }
  });

  resetComposerUi();
  wrapper.append(input, submit);
  return { wrapper, input, resetComposerUi };
}

function renderRecommendations(items, { append = false } = {}) {
  if (!(elements.list instanceof HTMLElement)) {
    return;
  }
  if (!append) {
    elements.list.replaceChildren();
    // Cleared cards' toggle buttons are now detached; drop them so the shared
    // registries don't accumulate stale entries across re-renders.
    watchLaterToggles.pruneDetached();
    favoriteToggles.pruneDetached();
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "recommendation-card";

    const preview = document.createElement("button");
    preview.className = "recommendation-preview";
    preview.type = "button";
    preview.addEventListener("click", () => {
      void openRecommendation(item.bvid, item);
    });

    const cover = document.createElement("div");
    cover.className = "recommendation-cover";
    const cardMedia = getRecommendationCardKind(item);
    if (cardMedia.kind === "cover") {
      const image = document.createElement("img");
      void setProxyImageSrc(image, cardMedia.coverUrl);
      image.alt = `${item.title} 的封面`;
      image.addEventListener("error", () => {
        image.remove();
        cover.classList.add("is-fallback");
        const fallbackText = document.createElement("span");
        fallbackText.className = "recommendation-cover-fallback-text";
        fallbackText.textContent = "封面加载慢了一下";
        cover.prepend(fallbackText);
      });
      cover.append(image);
    } else {
      // No-cover text card (X tweet/thread or empty cover): show the
      // body text instead of a thumbnail — never an <img> node.
      cover.classList.add("is-fallback", "is-text-card");
      const textNode = document.createElement("p");
      textNode.className = "recommendation-cover-text";
      textNode.textContent = cardMedia.text || "先看标题也行";
      cover.append(textNode);
    }

    const content = document.createElement("div");
    content.className = "recommendation-content";

    const top = document.createElement("div");
    top.className = "recommendation-top";

    const stateBadge = document.createElement("span");
    stateBadge.className = `recommendation-state${item.presented ? " is-presented" : ""}`;
    stateBadge.textContent = item.presented ? "你应该刷到过" : "刚给你翻出来";

    if (item.topic_label) {
      const badge = document.createElement("span");
      badge.className = "topic-badge";
      badge.textContent = item.topic_label;
      top.append(badge);
    }
    const platformKey = (item.source_platform || "bilibili").toLowerCase();
    const platformLabel =
      { bilibili: "B 站", xiaohongshu: "小红书", douyin: "抖音", youtube: "YouTube", twitter: "X" }[
        platformKey
      ] || item.source_platform;
    const sourceCorner = document.createElement("span");
    sourceCorner.className = `recommendation-source-corner source-platform-${platformKey}`;
    sourceCorner.textContent = platformLabel;
    cover.append(sourceCorner);
    top.append(stateBadge);

    const copyBlock = document.createElement("div");
    copyBlock.className = "recommendation-copy-block";

    const title = document.createElement("h3");
    title.className = "recommendation-title";
    title.textContent = item.title;

    copyBlock.append(title);
    if (item.expression) {
      const expression = document.createElement("p");
      expression.className = "recommendation-expression";
      expression.textContent = item.expression;
      copyBlock.append(expression);
    }

    const metaLine = document.createElement("p");
    metaLine.className = "recommendation-meta-line";
    metaLine.textContent = `这位 UP：${item.up_name}`;

    content.append(top, copyBlock, metaLine);
    preview.append(cover, content);

    const feedbackStatus = document.createElement("p");
    feedbackStatus.className = "feedback-status";
    setFeedbackStatus(feedbackStatus, item.presented ? "这条你应该已经眼熟了。" : "");

    const actions = document.createElement("div");
    actions.className = "recommendation-actions";
    const composer = createCommentComposer(item, feedbackStatus);
    actions.append(
      createActionButton("去看看", "action-button action-primary", () => {
        void openRecommendation(item.bvid, item);
      }),
      createActionButton("多来点", "action-button action-secondary", async () => {
        try {
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "submitting"),
            "info",
          );
          await submitFeedback(buildFeedbackPayload(item.id, "like"));
          setHint("记下了，这类可以多来点。", "success");
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "accepted"),
            "info",
          );
          attachFeedbackRuntimeProgress(feedbackStatus);
          await refreshProfileSummaryAfterInteraction({
            onProfileStart() {
              setFeedbackStatusWithTone(
                feedbackStatus,
                getSubmissionProgressMessage("feedback", "refreshing_profile"),
                "info",
              );
            },
            onActivityStart() {
              setFeedbackStatusWithTone(
                feedbackStatus,
                getSubmissionProgressMessage("feedback", "refreshing_activity"),
                "info",
              );
            },
            onDone() {
              if (state.activeFeedbackProgress == null) {
                setFeedbackStatusWithTone(
                  feedbackStatus,
                  getSubmissionProgressMessage("feedback", "success"),
                  "success",
                );
              }
            },
          });
        } catch {
          clearActiveFeedbackProgress();
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "error"),
            "error",
          );
          setHint("这条反馈没记上，先看看本地后端是不是开着。", "error");
        }
      }),
      (() => {
        const btn = createActionButton("", "action-button action-secondary", async () => {
          try {
            await toggleWatchLaterSaved(item.bvid);
          } catch {
            // Registry already rolled back the optimistic state.
          }
        });
        btn.innerHTML = WATCH_LATER_ICON_SVG;
        btn.classList.add("saved-toggle", "watch-later-btn");
        bindWatchLaterToggle(btn, item.bvid);
        return btn;
      })(),
      (() => {
        const btn = createActionButton("", "action-button action-secondary", async () => {
          try {
            await toggleFavoriteSaved(item.bvid);
          } catch {
            // Registry already rolled back the optimistic state.
          }
        });
        btn.innerHTML = FAVORITE_ICON_SVG;
        btn.classList.add("saved-toggle", "favorite-btn");
        bindFavoriteToggle(btn, item.bvid);
        return btn;
      })(),
      createActionButton("少来点", "action-button action-secondary", async () => {
        try {
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "submitting"),
            "info",
          );
          await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
          setHint("记下了，这路子先少来点。", "success");
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "accepted"),
            "info",
          );
          attachFeedbackRuntimeProgress(feedbackStatus);
          await refreshProfileSummaryAfterInteraction({
            onProfileStart() {
              setFeedbackStatusWithTone(
                feedbackStatus,
                getSubmissionProgressMessage("feedback", "refreshing_profile"),
                "info",
              );
            },
            onActivityStart() {
              setFeedbackStatusWithTone(
                feedbackStatus,
                getSubmissionProgressMessage("feedback", "refreshing_activity"),
                "info",
              );
            },
            onDone() {
              if (state.activeFeedbackProgress == null) {
                setFeedbackStatusWithTone(
                  feedbackStatus,
                  getSubmissionProgressMessage("feedback", "success"),
                  "success",
                );
              }
            },
          });
        } catch {
          clearActiveFeedbackProgress();
          setFeedbackStatusWithTone(
            feedbackStatus,
            getSubmissionProgressMessage("feedback", "error"),
            "error",
          );
          setHint("这条反馈没记上，先看看本地后端是不是开着。", "error");
        }
      }),
      createActionButton("说说原因", "action-button action-secondary", () => {
        composer.wrapper.hidden = !composer.wrapper.hidden;
        if (!composer.wrapper.hidden) {
          composer.resetComposerUi();
          composer.input.focus();
        }
      }),
    );

    card.append(preview, actions, composer.wrapper, feedbackStatus);
    elements.list.append(card);
  }
}

function getDisplayedRecommendationBvids() {
  return state.recommendations
    .map((item) => String(item?.bvid ?? "").trim())
    .filter(Boolean);
}

async function loadMoreRecommendations() {
  if (!state.online || state.loadingMore || !state.hasMoreRecommendations) {
    return;
  }
  state.loadingMore = true;
  setHint("再给你往下捞 10 条。", "info");
  try {
    const result = await appendRecommendations(getDisplayedRecommendationBvids());
    const incoming = Array.isArray(result.items) ? result.items : [];
    const existing = new Set(getDisplayedRecommendationBvids());
    const appended = incoming.filter((item) => {
      const bvid = String(item?.bvid ?? "").trim();
      if (!bvid || existing.has(bvid)) {
        return false;
      }
      existing.add(bvid);
      return true;
    });

    if (appended.length > 0) {
      state.recommendations = [...state.recommendations, ...appended];
      // Preload covers before inserting cards so they paint without the white flash.
      await preloadCoverImages(appended);
      renderRecommendations(appended, { append: true });
      setHint(`又给你续了 ${appended.length} 条，继续往下翻。`, "success");
    } else if (incoming.length === 0) {
      setHint("这池先翻到头了，等后台再补点新的。", "info");
    } else {
      setHint("这轮续页里没有更合适的新条目了。", "info");
    }

    state.hasMoreRecommendations = incoming.length >= 10 && appended.length > 0;
  } catch {
    setHint("这次往下续没成功，稍后再试。", "error");
  } finally {
    state.loadingMore = false;
    queueRecommendationLoadCheck();
  }
}

function maybeLoadMoreRecommendations() {
  if (
    !(elements.content instanceof HTMLElement) ||
    elements.viewRecommend.hidden ||
    !shouldAutoLoadRecommendations({
      activeTab: state.activeTab,
      loadingMore: state.loadingMore,
      hasMoreRecommendations: state.hasMoreRecommendations,
      userArmed: recommendationAutoLoadUserArmed,
    })
  ) {
    return;
  }

  // Trigger well before the bottom (not 96px) so preloadCoverImages has time to
  // warm the next batch's covers before the user actually scrolls onto them —
  // keeps newly revealed content flash-free.
  const remaining = elements.content.scrollHeight - elements.content.scrollTop - elements.content.clientHeight;
  if (remaining <= 600) {
    recommendationAutoLoadUserArmed = false;
    void loadMoreRecommendations();
  }
}

function renderRecommendationState(stateShape) {
  if (stateShape.kind === "ready") {
    hideRecommendationEmptyState();
    renderRecommendations(stateShape.items);
    const hint = getReadyRecommendationHint(stateShape.runtime);
    setHint(hint.message, hint.tone);
    queueRecommendationLoadCheck();
    return;
  }

  if (elements.list instanceof HTMLElement) {
    elements.list.replaceChildren();
    watchLaterToggles.pruneDetached();
    favoriteToggles.pruneDetached();
  }

  if (stateShape.kind === "offline") {
    showRecommendationEmptyState("后端还没开张", stateShape.message);
    setHint("先在项目根目录把 openbiliclaw start 跑起来。", "error");
    return;
  }

  if (stateShape.kind === "error") {
    showRecommendationEmptyState("推荐暂时没刷出来", stateShape.message);
    setHint("后端连上了，但推荐接口这会儿没回。", "error");
    return;
  }

  if (stateShape.kind === "uninitialized") {
    showRecommendationEmptyState(
      "还没完成初始化",
      "点「开始初始化」，会先检查前置条件，通过后就在这里一步步建好画像和首轮内容池。",
    );
    setHint("先完成初始化，把画像和候选池攒起来。");
    renderInitPanelIdle();
    return;
  }

  if (stateShape.kind === "refreshing") {
    showRecommendationEmptyState("阿B 正在补货", stateShape.message);
    setHint("你最近的新行为已经记下了，稍等一下会补进更对味的内容。");
    return;
  }

  showRecommendationEmptyState("这会儿还没新东西", stateShape.message);
  setHint("先跑 init、discover 或 recommend，再回来瞅瞅。");
}

async function loadProfileSummary({ force = false } = {}) {
  if (!shouldFetchProfileSummary({ online: state.online, profileLoaded: state.profileLoaded, force })) {
    if (!state.online) {
      renderProfileSummary(normalizeProfileSummary({ initialized: false }));
    } else if (state.profile) {
      // Cached path: still hydrate inbox so reopening popup with a
      // warm profile cache still surfaces the active speculations.
      hydrateInboxFromProfile(state.profile);
      renderProfileSummary(state.profile);
    }
    return;
  }

  try {
    const summary = normalizeProfileSummary(await fetchProfileSummary({ limit: 3 }));
    state.profile = summary;
    state.profileCognitionHistory = buildNextCognitionHistoryState(null, summary);
    state.expandedCognitionIndex = null;
  } catch {
    state.profile = normalizeProfileSummary({ initialized: false });
    state.profileCognitionHistory = {
      items: [],
      hasMore: false,
      nextCursor: "",
      loadingMore: false,
      loadMoreError: "",
    };
    state.expandedCognitionIndex = null;
  }
  // Hydrate after every successful or fallback profile load so the
  // inbox stays in sync with the speculator state (the backend dedupes
  // its WebSocket pushes via ``probed_domains``, so already-pushed
  // probes won't re-arrive on reconnect).
  hydrateInboxFromProfile(state.profile);
  void syncScopedChatTurns();
  state.profileLoaded = true;
  renderProfileSummary(state.profile);
}

function hydrateInboxFromProfile(profile) {
  hydrateInboxFromSpeculations(profile?.speculative_interests, "interest.probe");
  hydrateInboxFromSpeculations(profile?.speculative_avoidances, "avoidance.probe");
}

function hydrateInboxFromSpeculations(speculations, type = "interest.probe") {
  if (!Array.isArray(speculations)) return;
  const normalizedType = normalizeProbeType(type);
  const activeItems = speculations.filter((item) =>
    shouldHydrateProbe(item, normalizedType, state.handledProbeKeys),
  );
  // Speculator regenerates probes on a runtime cycle; older actives may
  // have rotated to cooldown.  We must REPLACE the interest.probe slice
  // of state.messages with the current active set, otherwise the inbox
  // accumulates stale entries from past cycles and drifts away from
  // what the profile section shows.
  // Delight messages are preserved untouched — they live on a separate
  // lifecycle (delight/pending endpoint).
  const activeKeys = new Set(
    activeItems.map((item) => probeMessageKey(normalizedType, item.domain)),
  );
  // Drop probe entries of the same type no longer in the active set.
  state.messages = state.messages.filter((m) => {
    const itemType = normalizeProbeType(m?.type);
    if (itemType !== normalizedType) return true;
    return activeKeys.has(probeMessageKey(itemType, m?.domain));
  });
  // Add any current active probes not yet in state.messages.
  const existingKeys = new Set(
    state.messages
      .filter((m) => normalizeProbeType(m?.type) === normalizedType && m?.domain)
      .map((m) => probeMessageKey(normalizedType, m.domain)),
  );
  for (const item of activeItems) {
    const itemKey = probeMessageKey(normalizedType, item.domain);
    if (existingKeys.has(itemKey)) {
      const existing = state.messages.find(
        (m) => probeMessageKey(m?.type, m?.domain) === itemKey,
      );
      if (existing) {
        existing.probe_mode = item.probe_mode || "";
        existing.challenge = Boolean(item.challenge);
      }
      continue;
    }
    state.messages.push({
      type: normalizedType,
      domain: item.domain,
      reason: item.reason || "",
      specifics: Array.isArray(item.specifics) ? item.specifics : [],
      probe_mode: item.probe_mode || "",
      challenge: Boolean(item.challenge),
    });
    existingKeys.add(itemKey);
  }
  updateMessageBadge();
}

async function loadMoreCognitionHistory() {
  if (
    !state.online ||
    !state.profileLoaded ||
    state.profile == null ||
    state.profileCognitionHistory.loadingMore ||
    !state.profileCognitionHistory.hasMore ||
    !state.profileCognitionHistory.nextCursor
  ) {
    return;
  }

  state.profileCognitionHistory = {
    ...state.profileCognitionHistory,
    loadingMore: true,
    loadMoreError: "",
  };
  renderProfileSummary(state.profile);

  try {
    const nextPage = normalizeProfileSummary(
      await fetchProfileSummary({
        limit: 3,
        cursor: state.profileCognitionHistory.nextCursor,
      }),
    );
    state.profile = {
      ...state.profile,
      initialized: nextPage.initialized,
      personality_portrait: nextPage.personality_portrait,
      core_traits: nextPage.core_traits,
      cognitive_style: nextPage.cognitive_style,
      motivational_drivers: nextPage.motivational_drivers,
      current_phase: nextPage.current_phase,
      deep_needs: nextPage.deep_needs,
      top_interests: nextPage.top_interests,
    };
    state.profileCognitionHistory = buildNextCognitionHistoryState(
      state.profileCognitionHistory,
      nextPage,
    );
  } catch {
    state.profileCognitionHistory = {
      ...state.profileCognitionHistory,
      loadingMore: false,
      loadMoreError: "retry",
    };
  }

  renderProfileSummary(state.profile);
}

async function refreshProfileSummaryAfterInteraction({
  onProfileStart = null,
  onActivityStart = null,
  onDone = null,
} = {}) {
  if (!state.online) {
    return;
  }
  if (!state.profileLoaded && state.activeTab !== "profile") {
    if (typeof onActivityStart === "function") {
      onActivityStart();
    }
    await loadActivityFeed();
    if (typeof onDone === "function") {
      onDone();
    }
    return;
  }
  if (typeof onProfileStart === "function") {
    onProfileStart();
  }
  await loadProfileSummary({ force: true });
  if (typeof onActivityStart === "function") {
    onActivityStart();
  }
  await loadActivityFeed();
  if (typeof onDone === "function") {
    onDone();
  }
}

async function initializeRecommendations() {
  const online = await checkBackendStatus();
  state.online = online;
  setStatus(online);

  if (!online) {
    state.runtimeStatus = null;
    state.runtimeConfig = null;
    state.recommendations = [];
    clearDelightQueue();
    state.hasMoreRecommendations = false;
    state.loadingMore = false;
    renderRuntimeToggles();
    renderDelightSlot();
    renderRecommendationState(getPopupState({ online, items: [], runtimeStatus: null }));
    renderProfileSummary(normalizeProfileSummary({ initialized: false }));
    return;
  }

  const [runtimeResult, recommendationResult, delightResult, configResult] =
    await Promise.allSettled([
      fetchRuntimeStatus(),
      fetchRecommendations(),
      fetchPendingDelightBatch(),
      fetchConfig(),
    ]);

  state.runtimeStatus = runtimeResult.status === "fulfilled" ? runtimeResult.value : null;
  if (configResult.status === "fulfilled") {
    applyRuntimeConfig(configResult.value);
  }
  if (delightResult.status === "fulfilled" && Array.isArray(delightResult.value)) {
    // Reset queue then re-push all from server so dismissed items in
    // memory are still respected (pushDelightCandidate filters them).
    clearDelightQueue();
    for (const item of delightResult.value) {
      pushDelightCandidate(item);
      // Delights no longer added to messages — shown in delight tray only.
    }
    // Restore local-only delight state (chat_reply, draft, composer, etc.)
    // that survives a Chrome side-panel reload.
    try {
      const raw =
        localStorage.getItem(DELIGHT_LOCAL_STATE_KEY) ||
        sessionStorage.getItem(DELIGHT_LOCAL_STATE_KEY);
      if (raw) {
        const localState = JSON.parse(raw);
        for (let i = 0; i < state.activeDelights.length; i++) {
          const bvid = state.activeDelights[i]?.bvid;
          if (bvid && localState[bvid]) {
            state.activeDelights[i] = { ...state.activeDelights[i], ...localState[bvid] };
          }
        }
        syncDelightHead();
      }
    } catch {
      // Ignore corrupt or inaccessible sessionStorage.
    }
  }
  renderPoolStatus(state.runtimeStatus);
  renderDelightSlot();
  updateMessageBadge();
  await syncScopedChatTurns();
  await loadActivityFeed();

  if (recommendationResult.status === "fulfilled") {
    resetRecommendationAutoLoadIntent();
    state.recommendations = recommendationResult.value;
    state.loadingMore = false;
    state.hasMoreRecommendations = state.recommendations.length >= 10;
    renderRecommendationState(
      getPopupState({
        online,
        items: state.recommendations,
        runtimeStatus: state.runtimeStatus,
      }),
    );
    return;
  }

  state.recommendations = [];
  state.loadingMore = false;
  state.hasMoreRecommendations = false;
  renderRecommendationState(
    getPopupState({
      online,
      items: [],
      error: recommendationResult.reason,
      runtimeStatus: state.runtimeStatus,
    }),
  );
}

async function handleManualRefresh() {
  if (manualRefreshInFlight) {
    return;
  }
  manualRefreshInFlight = true;
  const hadAdvertisedInventory =
    normalizeRuntimeStatus(state.runtimeStatus).pool_available_count > 0;
  setRefreshButtonState(true, "正在给你换一批…");
  try {
    const result = await reshuffleRecommendations();
    if (!Array.isArray(result.items)) {
      setHint("还没初始化好。去「推荐」页点「开始初始化」，完成后再刷新。", "error");
      return;
    }
    resetRecommendationAutoLoadIntent();
    state.recommendations = result.items;
    state.loadingMore = false;
    state.hasMoreRecommendations = result.items.length >= 10;
    state.runtimeStatus = await fetchRuntimeStatus().catch(() => state.runtimeStatus);
    renderPoolStatus(state.runtimeStatus);
    renderRecommendationState(
      getPopupState({
        online: state.online,
        items: state.recommendations,
        runtimeStatus: state.runtimeStatus,
      }),
    );
    const hint = getManualRefreshResultHint({
      itemCount: result.items.length,
      hadAdvertisedInventory,
    });
    setHint(hint.message, hint.tone);
    await loadActivityFeed();
    void refreshRecommendations().catch(() => undefined);
  } catch {
    setHint("这次没换出来新的，稍后再试。", "error");
  } finally {
    manualRefreshInFlight = false;
    setRefreshButtonState(false);
  }
}

function bindTabs() {
  const bindings = [
    [elements.tabRecommend, "recommend"],
    [elements.tabWatchLater, "watchLater"],
    [elements.tabFavorites, "favorites"],
    [elements.tabProfile, "profile"],
    [elements.tabChat, "chat"],
  ];

  for (const [button, tabName] of bindings) {
    if (!(button instanceof HTMLButtonElement)) {
      continue;
    }
    button.addEventListener("click", () => {
      setActiveTab(tabName);
    });
  }
}

function bindProfileHistoryLoading() {
  if (elements.content instanceof HTMLElement) {
    elements.content.addEventListener("scroll", () => {
      maybeLoadMoreRecommendations();
    });
  }

  if (elements.profileRecentMemoryMore instanceof HTMLButtonElement) {
    elements.profileRecentMemoryMore.addEventListener("click", () => {
      void loadMoreCognitionHistory();
    });
  }

  bindProfileEditToggle();
}

function bindRefreshButton() {
  if (!(elements.refreshRecommendationsButton instanceof HTMLButtonElement)) {
    return;
  }
  elements.refreshRecommendationsButton.addEventListener("click", () => {
    void handleManualRefresh();
  });
}

function bindActivityToggle() {
  if (!(elements.activityToggleButton instanceof HTMLButtonElement)) {
    return;
  }
  elements.activityToggleButton.addEventListener("click", () => {
    state.activityExpanded = !state.activityExpanded;
    renderActivityCard();
  });
}

function bindChat() {
  if (
    !(elements.chatForm instanceof HTMLFormElement) ||
    !(elements.chatInput instanceof HTMLTextAreaElement) ||
    !(elements.chatSendButton instanceof HTMLButtonElement)
  ) {
    return;
  }

  // ── Rotating placeholder hints ──
  function rotatePlaceholder() {
    chatPlaceholderIndex = (chatPlaceholderIndex + 1) % CHAT_PLACEHOLDERS.length;
    elements.chatInput.setAttribute("placeholder", CHAT_PLACEHOLDERS[chatPlaceholderIndex]);
  }
  function startPlaceholderRotation() {
    if (!chatPlaceholderTimer) {
      chatPlaceholderTimer = window.setInterval(rotatePlaceholder, 5000);
    }
  }
  function stopPlaceholderRotation() {
    if (chatPlaceholderTimer) {
      clearInterval(chatPlaceholderTimer);
      chatPlaceholderTimer = null;
    }
  }
  // Start rotating when chat tab is visible; pause when user is typing.
  elements.chatInput.addEventListener("focus", stopPlaceholderRotation);
  elements.chatInput.addEventListener("blur", () => {
    if (!elements.chatInput.value.trim()) {
      startPlaceholderRotation();
    }
  });
  startPlaceholderRotation();

  let slowStatusTimer = null;

  function clearSlowStatusTimer() {
    if (slowStatusTimer !== null) {
      window.clearTimeout(slowStatusTimer);
      slowStatusTimer = null;
    }
  }

  elements.chatInput.addEventListener("input", () => {
    if (!elements.chatSendButton.disabled) {
      setChatStatus("");
    }
  });

  elements.chatInput.addEventListener("keydown", (event) => {
    if (!shouldSubmitChatOnEnter(event)) {
      return;
    }
    event.preventDefault();
    elements.chatForm.requestSubmit();
  });

  elements.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = elements.chatInput.value.trim();
    if (!message) {
      setHint("先说一句你的想法、偏好或者最近状态。", "error");
      elements.chatInput.focus();
      return;
    }
    if (!state.online) {
      setHint("后端还没连上，现在还发不出去。", "error");
      return;
    }

    const turnId = createClientTurnId("chat");
    appendChatMessage("你", message, { turnId, part: "user" });
    const thinkingPlaceholder = appendChatThinkingPlaceholder(turnId);
    elements.chatInput.value = "";
    elements.chatSendButton.disabled = true;
    elements.chatSendButton.textContent = "发送中...";
    setChatStatus(getSubmissionProgressMessage("chat", "waiting_reply"), "info");
    clearSlowStatusTimer();
    slowStatusTimer = window.setTimeout(() => {
      if (elements.chatSendButton.disabled) {
        setChatStatus(getSubmissionProgressMessage("chat", "waiting_slow"), "info");
      }
    }, 2500);

    try {
      const turn = await startChatTurn({
        turnId,
        session: CHAT_SESSION,
        scope: "chat",
        message,
      });
      clearSlowStatusTimer();
      renderChatTurn(turn);
      setHint("收到，阿B 正在整理。", "success");
      if (turn.status === "completed" || turn.status === "failed") {
        await refreshAfterChatTurn();
      } else {
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate: renderChatTurn,
          async onDone(doneTurn) {
            if (doneTurn.status === "completed") {
              setHint("这句记下了。", "success");
            }
            await refreshAfterChatTurn();
          },
        });
        setChatStatus(getSubmissionProgressMessage("chat", "waiting_reply"), "info");
      }
    } catch {
      clearSlowStatusTimer();
      if (thinkingPlaceholder) {
        replaceChatThinkingPlaceholder(thinkingPlaceholder, "刚刚没发出去，换个说法再试试。");
      } else {
        appendChatMessage("助手", "刚刚没发出去，换个说法再试试。", {
          turnId,
          part: "assistant",
        });
      }
      setChatStatus(getSubmissionProgressMessage("chat", "error"), "error");
      setHint("聊天接口这会儿没接上，先看看本地后端是不是开着。", "error");
    } finally {
      clearSlowStatusTimer();
      elements.chatSendButton.disabled = false;
      elements.chatSendButton.textContent = "发出去";
    }
  });
}

// ── Settings panel ──────────────────────────────────────────

function bindSettings() {
  const gearBtn = document.getElementById("settingsGear");
  const overlay = document.getElementById("settingsOverlay");
  const backBtn = document.getElementById("settingsBack");
  const saveBtn = document.getElementById("settingsSave");
  const toast = document.getElementById("settingsToast");
  const issuesContainer = document.getElementById("settingsIssues");
  const providerSelect = document.getElementById("cfgLlmProvider");
  const backendHostInput = document.getElementById("cfgBackendHost");
  const backendPortInput = document.getElementById("cfgBackendPort");
  const bannerOffline = document.getElementById("cfgBannerOffline");
  const bannerDegraded = document.getElementById("cfgBannerDegraded");
  const bannerNoCache = document.getElementById("cfgBannerNoCache");

  if (!gearBtn || !overlay || !backBtn || !saveBtn) return;

  // LAN password-gate toggle (local-only; the extension is a trusted-local
  // client so it can manage the gate without being able to lock itself out).
  const authControl = initAuthControl(
    {
      checkbox: document.getElementById("cfgAuthEnabled"),
      password: document.getElementById("cfgAuthPassword"),
      saveBtn: document.getElementById("cfgAuthSave"),
      hint: document.getElementById("cfgAuthHint"),
    },
    { getBaseUrl: getBackendBaseUrl },
  );

  const autostartControl = initAutostartControl(
    {
      checkbox: document.getElementById("cfgAutostartEnabled"),
      hint: document.getElementById("cfgAutostartHint"),
    },
    { getBaseUrl: getBackendBaseUrl },
  );

  const settingsTabs = [
    ["models", document.getElementById("settingsTabModels")],
    ["sources", document.getElementById("settingsTabSources")],
    ["scheduler", document.getElementById("settingsTabScheduler")],
    ["general", document.getElementById("settingsTabGeneral")],
    ["logging", document.getElementById("settingsTabLogging")],
  ];

  function setActiveSettingsPanel(activePanel = "models") {
    for (const [name, tab] of settingsTabs) {
      const isActive = name === activePanel;
      if (tab instanceof HTMLButtonElement) {
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      }
      const panel = overlay.querySelector(`[data-settings-panel="${name}"]`);
      if (panel instanceof HTMLElement) {
        panel.hidden = !isActive;
      }
    }
  }

  for (const [name, tab] of settingsTabs) {
    if (tab instanceof HTMLButtonElement) {
      tab.addEventListener("click", () => setActiveSettingsPanel(name));
    }
  }

  async function populateBackendEndpoint() {
    try {
      const endpoint = await getBackendEndpointConfig();
      if (backendHostInput instanceof HTMLInputElement) {
        backendHostInput.value = endpoint.host || "";
      }
      if (backendPortInput instanceof HTMLInputElement) {
        backendPortInput.value = String(endpoint.port);
      }
    } catch {
      // Fall back to the placeholder default if storage is unavailable.
    }
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value || "—";
  }

  function getExtensionVersionLabel() {
    try {
      const manifest = globalThis.chrome?.runtime?.getManifest?.();
      return manifest?.version || "—";
    } catch {
      return "—";
    }
  }

  function renderBackendUpdateStatus(payload) {
    const backend = {
      ...(state.backendUpdateStatus || {}),
      ...(payload?.backend || payload || {}),
    };
    state.backendUpdateStatus = backend;
    setText("backendUpdateCurrent", backend.current_version || "—");
    setText("backendUpdateLatest", backend.latest_version || backend.latest_tag || "—");
    setText("backendUpdateState", backend.state || "unknown");
    setText("backendUpdateLastCheck", backend.last_check_at || "—");
    const backendErrorText =
      backend.last_error || (backend.reason && backend.reason !== "none" ? backend.reason : "—");
    setText("backendUpdateError", backendErrorText);
    setText("extensionVersionValue", getExtensionVersionLabel());

    const applyBtn = document.getElementById("backendUpdateApply");
    if (applyBtn instanceof HTMLButtonElement) {
      const canApply = backend.state === "update_available" && Boolean(backend.latest_tag);
      applyBtn.hidden = !canApply;
      applyBtn.dataset.tag = backend.latest_tag || "";
    }
  }

  async function loadBackendUpdateStatus() {
    try {
      const payload = await fetchUpdateStatus();
      renderBackendUpdateStatus(payload);
      return payload;
    } catch {
      renderBackendUpdateStatus({
        state: "unknown",
        current_version: "—",
        latest_version: "—",
        latest_tag: "",
        last_check_at: "",
        last_error: "后端不可达",
        reason: "github_unreachable",
      });
      return null;
    }
  }

  backendUpdateStatusRefresh = loadBackendUpdateStatus;

  function showProviderFields(provider) {
    for (const el of overlay.querySelectorAll(".settings-provider-fields")) {
      el.classList.toggle("is-active", el.dataset.provider === provider);
    }
  }

  providerSelect.addEventListener("change", () => {
    showProviderFields(providerSelect.value);
  });

  // ── Embedding section: dynamic visibility + placeholder ──
  // Mirrors the backend resolution order in
  // src/openbiliclaw/llm/registry.py:_build_dedicated_embedding_provider.
  const EMBEDDING_DEFAULT_MODEL = {
    "": "留空 = 自动选择",
    openai: "text-embedding-3-small",
    gemini: "gemini-embedding-001",
    ollama: "bge-m3",
    openai_compatible: "bge-large-en-v1.5",
  };
  const EMBEDDING_BASE_URL_HINT = {
    "": "留空使用默认",
    openai: "留空 = https://api.openai.com/v1",
    gemini: "(Gemini SDK 不需要 base_url)",
    ollama: "http://localhost:11434/v1",
    openai_compatible: "https://api.together.xyz/v1 / http://localhost:8000/v1",
  };

  function applyEmbeddingProviderUI() {
    const select = document.getElementById("cfgEmbeddingProvider");
    if (!select) return;
    const provider = select.value;
    const modelInput = document.getElementById("cfgEmbeddingModel");
    if (modelInput) {
      modelInput.placeholder =
        EMBEDDING_DEFAULT_MODEL[provider] ?? "留空 = 自动选择";
    }
    const baseUrlInput = document.getElementById("cfgEmbeddingBaseUrl");
    if (baseUrlInput) {
      baseUrlInput.placeholder =
        EMBEDDING_BASE_URL_HINT[provider] ?? "留空使用默认";
    }
    // Field visibility: ollama doesn't need an api_key; gemini doesn't
    // use base_url. openai_compatible needs both (it's the whole point).
    for (const el of overlay.querySelectorAll("[data-embedding-field]")) {
      const field = el.dataset.embeddingField;
      let visible = true;
      if (provider === "ollama") {
        visible = field !== "api_key";
      } else if (provider === "gemini") {
        visible = field !== "base_url";
      }
      el.style.display = visible ? "" : "none";
    }
  }

  const embeddingProviderSelect = document.getElementById("cfgEmbeddingProvider");
  if (embeddingProviderSelect) {
    embeddingProviderSelect.addEventListener("change", applyEmbeddingProviderUI);
  }

  function showToast(message, tone = "success") {
    toast.textContent = message;
    toast.dataset.tone = tone;
    toast.hidden = false;
    setTimeout(() => { toast.hidden = true; }, 4000);
  }

  function setSaveButtonMode(mode = "") {
    saveBtn.dataset.tone = mode === "warning" ? "warning" : "";
    saveBtn.textContent = mode === "degraded" ? "保存并提示重启" : "保存配置";
  }

  function hideConfigBanners() {
    for (const banner of [bannerOffline, bannerDegraded, bannerNoCache]) {
      if (banner instanceof HTMLElement) {
        banner.hidden = true;
        banner.textContent = "";
      }
    }
  }

  function showConfigBanner(banner, message, tone = "warning") {
    if (!(banner instanceof HTMLElement)) return;
    banner.textContent = message;
    banner.dataset.tone = tone;
    banner.hidden = false;
  }

  function formatCachedAt(cachedAt) {
    if (!cachedAt) return "未知时间";
    const parsed = new Date(cachedAt);
    if (Number.isNaN(parsed.getTime())) return String(cachedAt);
    return parsed.toLocaleString("zh-CN", { hour12: false });
  }

  function renderDegradedBanner(cfg) {
    if (!cfg?.degraded) {
      if (bannerDegraded instanceof HTMLElement) bannerDegraded.hidden = true;
      return;
    }
    const issues = Array.isArray(cfg.issues) ? cfg.issues : [];
    const issueText = issues
      .map((issue) => `${issue.field || "config"}: ${issue.message || ""}`.trim())
      .filter(Boolean)
      .slice(0, 3)
      .join("；");
    showConfigBanner(
      bannerDegraded,
      `后端处于降级模式，保存修复后需要 restart daemon。${issueText}`,
      "warning",
    );
    setSaveButtonMode("degraded");
  }

  function renderIssues(issues) {
    issuesContainer.innerHTML = "";
    if (!Array.isArray(issues) || issues.length === 0) return;
    for (const issue of issues) {
      const div = document.createElement("div");
      div.className = "settings-issue";
      div.textContent = `${issue.field}: ${issue.message}`;
      issuesContainer.appendChild(div);
    }
  }

  function renderStructuredConfigError(err) {
    if (!Array.isArray(err.details?.config?.issues)) return false;
    applyRuntimeConfig(err.details.config);
    renderIssues(err.details.config.issues);
    renderDegradedBanner(err.details.config);
    showToast(err.details.message || "配置未保存，请先修正高亮问题。", "error");
    return true;
  }

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? "";
  };

  const getVal = (id) => {
    const el = document.getElementById(id);
    return el ? el.value : "";
  };

  function joinLogPath(directory, filename) {
    const dir = String(directory || "").trim();
    const name = String(filename || "").trim();
    if (!dir) return name;
    if (!name) return dir;
    return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
  }

  function resolveLogPathFromConfig(loggingConfig) {
    if (loggingConfig?.file_path) return loggingConfig.file_path;
    return joinLogPath(loggingConfig?.directory || "logs", loggingConfig?.filename || "openbiliclaw.log");
  }

  function splitLogPath(rawPath, currentLogging) {
    const fallback = { directory: "logs", filename: "openbiliclaw.log" };
    const trimmed = String(rawPath || "").trim();
    if (!trimmed) return fallback;
    if (currentLogging && trimmed === resolveLogPathFromConfig(currentLogging)) {
      return {
        directory: currentLogging.directory || fallback.directory,
        filename: currentLogging.filename || fallback.filename,
      };
    }
    const normalized = trimmed.replaceAll("\\", "/").replace(/\/+$/, "");
    const slashIndex = normalized.lastIndexOf("/");
    if (slashIndex === -1) {
      return { directory: fallback.directory, filename: normalized || fallback.filename };
    }
    return {
      directory: normalized.slice(0, slashIndex) || "/",
      filename: normalized.slice(slashIndex + 1) || fallback.filename,
    };
  }

  const getInt = (id, fallback) => {
    const raw = getVal(id);
    if (raw === "") return fallback;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const getFloat = (id, fallback) => {
    const raw = getVal(id);
    if (raw === "") return fallback;
    const parsed = parseFloat(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const checked = (id, fallback = false) => {
    const el = document.getElementById(id);
    return el ? el.checked : fallback;
  };

  // Unified per-source login / cookie status from GET /api/sources/status,
  // rendered as a uniform colored-dot line inside every source card. Only X is
  // live-validated (state ok); the rest report local cookie/token readiness.
  const SOURCE_STATUS_DOT = {
    ok: "#2ecc71",
    ready: "#2ecc71",
    no_auth: "#9aa0a6",
    missing: "#e0a800",
    missing_cookie: "#e0a800",
    rate_limited: "#e0a800",
    partial: "#e0a800",
    stale: "#e0a800",
    expired_cookie: "#e74c3c",
    blocked: "#e74c3c",
  };
  const SOURCE_STATUS_KEYS = ["bilibili", "xiaohongshu", "douyin", "youtube", "twitter"];

  // Best-effort: when the backend is unreachable, leave a neutral hint.
  async function renderSourcesStatus() {
    let data = null;
    try {
      data = await fetchSourcesStatus();
    } catch {
      data = null;
    }
    for (const key of SOURCE_STATUS_KEYS) {
      const row = document.querySelector(`[data-source-status="${key}"]`);
      if (!row) continue;
      const dot = row.querySelector(".src-dot");
      const detail = row.querySelector(".src-detail");
      const item = data && data[key];
      if (!item) {
        if (detail) detail.textContent = "状态暂不可用(后端未连接)。";
        if (dot) dot.style.color = "#9aa0a6";
        row.style.opacity = "1";
        continue;
      }
      if (detail) detail.textContent = (item.enabled ? "" : "(未启用) ") + (item.detail || "");
      if (dot) dot.style.color = SOURCE_STATUS_DOT[item.state] || "#9aa0a6";
      row.style.opacity = item.enabled ? "1" : "0.6";
    }
  }

  // The side panel stays open while the user signs into platforms in other
  // tabs, so a one-shot render goes stale — re-poll while a status row is
  // actually visible.
  setInterval(() => {
    if (document.hidden) return;
    const row = document.querySelector("[data-source-status]");
    if (!row || row.offsetParent === null) return;
    void renderSourcesStatus();
  }, 30000);

  function populateForm(cfg) {
    applyRuntimeConfig(cfg);
    // LLM
    providerSelect.value = cfg.llm?.default_provider || "openai";
    showProviderFields(providerSelect.value);
    setVal("cfgLlmConcurrency", cfg.llm?.concurrency ?? 3);
    setVal("cfgLlmTimeout", cfg.llm?.timeout ?? 300);
    setVal("cfgLlmFallbackProvider", cfg.llm?.fallback_provider);

    setVal("cfgOpenaiAuthMode", cfg.llm?.openai?.auth_mode || "api_key");
    setVal("cfgOpenaiKey", cfg.llm?.openai?.api_key);
    setVal("cfgOpenaiModel", cfg.llm?.openai?.model);
    setVal("cfgOpenaiBaseUrl", cfg.llm?.openai?.base_url);
    setVal("cfgClaudeKey", cfg.llm?.claude?.api_key);
    setVal("cfgClaudeModel", cfg.llm?.claude?.model);
    setVal("cfgGeminiKey", cfg.llm?.gemini?.api_key);
    setVal("cfgGeminiModel", cfg.llm?.gemini?.model);
    setVal("cfgDeepseekKey", cfg.llm?.deepseek?.api_key);
    setVal("cfgDeepseekModel", cfg.llm?.deepseek?.model);
    setVal("cfgDeepseekBaseUrl", cfg.llm?.deepseek?.base_url);
    const deepseekReasoning = document.getElementById("cfgDeepseekReasoning");
    if (deepseekReasoning) deepseekReasoning.value = cfg.llm?.deepseek?.reasoning_effort || "";
    setVal("cfgOllamaModel", cfg.llm?.ollama?.model);
    setVal("cfgOllamaBaseUrl", cfg.llm?.ollama?.base_url);
    setVal("cfgOpenrouterKey", cfg.llm?.openrouter?.api_key);
    setVal("cfgOpenrouterModel", cfg.llm?.openrouter?.model);
    setVal("cfgOpenrouterBaseUrl", cfg.llm?.openrouter?.base_url);
    setVal("cfgOpenrouterReferer", cfg.llm?.openrouter?.http_referer);
    setVal("cfgOpenrouterTitle", cfg.llm?.openrouter?.x_title);
    setVal("cfgOpenaiCompatibleKey", cfg.llm?.openai_compatible?.api_key);
    setVal("cfgOpenaiCompatibleModel", cfg.llm?.openai_compatible?.model);
    setVal("cfgOpenaiCompatibleBaseUrl", cfg.llm?.openai_compatible?.base_url);

    setVal("cfgModuleSoulProvider", cfg.llm?.soul?.provider);
    setVal("cfgModuleSoulModel", cfg.llm?.soul?.model);
    setVal("cfgModuleDiscoveryProvider", cfg.llm?.discovery?.provider);
    setVal("cfgModuleDiscoveryModel", cfg.llm?.discovery?.model);
    setVal("cfgModuleRecommendationProvider", cfg.llm?.recommendation?.provider);
    setVal("cfgModuleRecommendationModel", cfg.llm?.recommendation?.model);
    setVal("cfgModuleEvaluationProvider", cfg.llm?.evaluation?.provider);
    setVal("cfgModuleEvaluationModel", cfg.llm?.evaluation?.model);

    // Embedding (v0.3.32+ — owns its own api_key/base_url)
    const embProvider = document.getElementById("cfgEmbeddingProvider");
    if (embProvider) embProvider.value = cfg.llm?.embedding?.provider || "";
    setVal("cfgEmbeddingFallbackProvider", cfg.llm?.embedding?.fallback_provider);
    setVal("cfgEmbeddingApiKey", cfg.llm?.embedding?.api_key);
    setVal("cfgEmbeddingBaseUrl", cfg.llm?.embedding?.base_url);
    setVal("cfgEmbeddingModel", cfg.llm?.embedding?.model);
    setVal("cfgEmbeddingSimilarity", cfg.llm?.embedding?.similarity_threshold);
    applyEmbeddingProviderUI();

    // Bilibili
    const biliAuth = document.getElementById("cfgBiliAuth");
    if (biliAuth) biliAuth.value = cfg.bilibili?.auth_method || "cookie";
    setVal("cfgBiliCookie", cfg.bilibili?.cookie);
    setVal("cfgBiliBrowserExecutable", cfg.bilibili?.browser_executable);
    const biliBrowserHeaded = document.getElementById("cfgBiliBrowserHeaded");
    if (biliBrowserHeaded) biliBrowserHeaded.checked = cfg.bilibili?.browser_headed === true;
    const bilibiliEnabled = document.getElementById("cfgBilibiliEnabled");
    if (bilibiliEnabled) bilibiliEnabled.checked = cfg.sources?.bilibili?.enabled !== false;

    // Sources
    setVal("cfgSourcesBrowserCdp", cfg.sources?.browser?.cdp_url);
    const sourcesBrowserHeaded = document.getElementById("cfgSourcesBrowserHeaded");
    if (sourcesBrowserHeaded) {
      sourcesBrowserHeaded.checked = cfg.sources?.browser?.headed === true;
    }
    const xhsEnabled = document.getElementById("cfgXhsEnabled");
    if (xhsEnabled) xhsEnabled.checked = cfg.sources?.xiaohongshu?.enabled === true;
    setVal("cfgXhsDailySearchBudget", cfg.sources?.xiaohongshu?.daily_search_budget);
    setVal("cfgXhsDailyCreatorBudget", cfg.sources?.xiaohongshu?.daily_creator_budget);
    setVal("cfgXhsTaskInterval", cfg.sources?.xiaohongshu?.task_interval_seconds);
    const douyinEnabled = document.getElementById("cfgDouyinEnabled");
    if (douyinEnabled) douyinEnabled.checked = cfg.sources?.douyin?.enabled === true;
    setVal("cfgDouyinCookie", cfg.sources?.douyin?.cookie);
    setVal("cfgDouyinCookieEnv", cfg.sources?.douyin?.cookie_env);
    setVal("cfgDouyinDailySearchBudget", cfg.sources?.douyin?.daily_search_budget);
    setVal("cfgDouyinDailyHotBudget", cfg.sources?.douyin?.daily_hot_budget);
    setVal("cfgDouyinDailyFeedBudget", cfg.sources?.douyin?.daily_feed_budget);
    setVal("cfgDouyinRequestInterval", cfg.sources?.douyin?.request_interval_seconds);
    const youtubeEnabled = document.getElementById("cfgYoutubeEnabled");
    if (youtubeEnabled) youtubeEnabled.checked = cfg.sources?.youtube?.enabled === true;
    setVal("cfgYoutubeDailySearchBudget", cfg.sources?.youtube?.daily_search_budget);
    setVal("cfgYoutubeDailyTrendingBudget", cfg.sources?.youtube?.daily_trending_budget);
    setVal("cfgYoutubeDailyChannelBudget", cfg.sources?.youtube?.daily_channel_budget);
    setVal("cfgYoutubeRequestInterval", cfg.sources?.youtube?.request_interval_seconds);
    setVal("cfgYoutubeMinInterval", cfg.sources?.youtube?.min_interval_minutes);
    const twitterEnabled = document.getElementById("cfgTwitterEnabled");
    if (twitterEnabled) twitterEnabled.checked = cfg.sources?.twitter?.enabled === true;
    setVal("cfgTwitterCookie", cfg.sources?.twitter?.cookie);
    setVal("cfgTwitterCookieEnv", cfg.sources?.twitter?.cookie_env);
    setVal("cfgTwitterDailySearchBudget", cfg.sources?.twitter?.daily_search_budget);
    setVal("cfgTwitterDailyFeedBudget", cfg.sources?.twitter?.daily_feed_budget);
    setVal("cfgTwitterDailyCreatorBudget", cfg.sources?.twitter?.daily_creator_budget);
    setVal("cfgTwitterRequestInterval", cfg.sources?.twitter?.request_interval_seconds);
    setVal("cfgTwitterMinInterval", cfg.sources?.twitter?.min_interval_minutes);
    void renderSourcesStatus();

    // General
    const lang = document.getElementById("cfgLanguage");
    if (lang) lang.value = cfg.language || "zh";
    setVal("cfgDataDir", cfg.data_dir);
    setVal("cfgStorageDbPath", cfg.storage?.db_path);

    // Scheduler
    const schedEnabled = document.getElementById("cfgSchedulerEnabled");
    if (schedEnabled) schedEnabled.checked = cfg.scheduler?.enabled === false;
    const pauseOnDisconnect = document.getElementById("cfgPauseOnDisconnect");
    if (pauseOnDisconnect) {
      pauseOnDisconnect.checked = cfg.scheduler?.pause_on_extension_disconnect === true;
    }
    setVal("cfgExtensionDisconnectGrace", cfg.scheduler?.extension_disconnect_grace_seconds);
    setVal("cfgPoolTarget", cfg.scheduler?.pool_target_count);
    setVal("cfgAccountSyncInterval", cfg.scheduler?.account_sync_interval_hours);
    setVal("cfgRefreshCheckInterval", cfg.scheduler?.refresh_check_interval_seconds);
    setVal("cfgSignalEventThreshold", cfg.scheduler?.signal_event_threshold);
    setVal("cfgFeedbackBatchThreshold", cfg.scheduler?.feedback_batch_threshold);
    setVal("cfgTrendingRefreshHours", cfg.scheduler?.trending_refresh_hours);
    setVal("cfgExploreRefreshHours", cfg.scheduler?.explore_refresh_hours);
    setVal("cfgDiscoveryLimit", cfg.scheduler?.discovery_limit);
    setVal("cfgProactivePushInterval", cfg.scheduler?.proactive_push_interval_seconds);
    setVal("cfgSpeculatorIdleInterval", cfg.scheduler?.speculator_idle_interval_minutes);
    const autoUpdate = document.getElementById("cfgAutoUpdate");
    if (autoUpdate) autoUpdate.checked = cfg.scheduler?.auto_update_enabled === true;
    setVal("cfgAutoUpdateInterval", cfg.scheduler?.auto_update_check_interval_hours);
    setVal("cfgPoolShareBilibili", cfg.scheduler?.pool_source_shares?.bilibili);
    setVal("cfgPoolShareXhs", cfg.scheduler?.pool_source_shares?.xiaohongshu);
    setVal("cfgPoolShareDouyin", cfg.scheduler?.pool_source_shares?.douyin);
    setVal("cfgPoolShareYoutube", cfg.scheduler?.pool_source_shares?.youtube);
    setVal("cfgPoolShareTwitter", cfg.scheduler?.pool_source_shares?.twitter);
    setVal("cfgSpeculationInterval", cfg.scheduler?.speculation_interval_minutes);
    setVal("cfgSpeculationTtl", cfg.scheduler?.speculation_ttl_days);
    setVal("cfgSpeculationCooldown", cfg.scheduler?.speculation_cooldown_days);
    setVal("cfgSpeculationThreshold", cfg.scheduler?.speculation_confirmation_threshold);
    setVal("cfgSpeculationMaxActive", cfg.scheduler?.speculation_max_active);
    setVal("cfgSpeculationMaxPrimary", cfg.scheduler?.speculation_max_primary_interests);
    setVal("cfgSpeculationMaxSecondary", cfg.scheduler?.speculation_max_secondary_interests);

    // Logging
    const logLevel = document.getElementById("cfgLogLevel");
    if (logLevel) logLevel.value = cfg.logging?.level || "INFO";
    const logFileLevel = document.getElementById("cfgLogFileLevel");
    if (logFileLevel) logFileLevel.value = cfg.logging?.file_level || "DEBUG";
    setVal("cfgLogPath", resolveLogPathFromConfig(cfg.logging));
    setVal("cfgLogMaxFileSize", cfg.logging?.max_file_size_mb);
    setVal("cfgLogBackupCount", cfg.logging?.backup_count);
    setVal("cfgLogAggregateBudget", cfg.logging?.aggregate_budget_mb);
    setVal("cfgLogUnmanagedTruncate", cfg.logging?.unmanaged_truncate_mb);
    setVal("cfgLogUnmanagedMaxAge", cfg.logging?.unmanaged_max_age_days);

    renderIssues(cfg.issues);
    renderDegradedBanner(cfg);
  }

  function collectForm() {
    const logPath = splitLogPath(getVal("cfgLogPath"), state.runtimeConfig?.logging);
    const llmFallbackProvider = getVal("cfgLlmFallbackProvider");
    const embeddingFallbackProvider = getVal("cfgEmbeddingFallbackProvider");
    return {
      language: getVal("cfgLanguage"),
      data_dir: getVal("cfgDataDir"),
      llm: {
        default_provider: providerSelect.value,
        concurrency: getInt("cfgLlmConcurrency", 3),
        timeout: getInt("cfgLlmTimeout", 300),
        fallback_enabled: Boolean(llmFallbackProvider),
        fallback_provider: llmFallbackProvider,
        openai: {
          auth_mode: getVal("cfgOpenaiAuthMode") || "api_key",
          api_key: getVal("cfgOpenaiKey"),
          model: getVal("cfgOpenaiModel"),
          base_url: getVal("cfgOpenaiBaseUrl"),
        },
        claude: {
          api_key: getVal("cfgClaudeKey"),
          model: getVal("cfgClaudeModel"),
        },
        gemini: {
          api_key: getVal("cfgGeminiKey"),
          model: getVal("cfgGeminiModel"),
        },
        deepseek: {
          api_key: getVal("cfgDeepseekKey"),
          model: getVal("cfgDeepseekModel"),
          base_url: getVal("cfgDeepseekBaseUrl"),
          reasoning_effort: getVal("cfgDeepseekReasoning"),
        },
        ollama: {
          model: getVal("cfgOllamaModel"),
          base_url: getVal("cfgOllamaBaseUrl"),
        },
        openrouter: {
          api_key: getVal("cfgOpenrouterKey"),
          model: getVal("cfgOpenrouterModel"),
          base_url: getVal("cfgOpenrouterBaseUrl"),
          http_referer: getVal("cfgOpenrouterReferer"),
          x_title: getVal("cfgOpenrouterTitle"),
        },
        openai_compatible: {
          api_key: getVal("cfgOpenaiCompatibleKey"),
          model: getVal("cfgOpenaiCompatibleModel"),
          base_url: getVal("cfgOpenaiCompatibleBaseUrl"),
        },
        embedding: {
          provider: getVal("cfgEmbeddingProvider"),
          api_key: getVal("cfgEmbeddingApiKey"),
          base_url: getVal("cfgEmbeddingBaseUrl"),
          model: getVal("cfgEmbeddingModel"),
          similarity_threshold: getFloat("cfgEmbeddingSimilarity", 0.82),
          fallback_enabled: Boolean(embeddingFallbackProvider),
          fallback_provider: embeddingFallbackProvider,
        },
        soul: {
          provider: getVal("cfgModuleSoulProvider"),
          model: getVal("cfgModuleSoulModel"),
        },
        discovery: {
          provider: getVal("cfgModuleDiscoveryProvider"),
          model: getVal("cfgModuleDiscoveryModel"),
        },
        recommendation: {
          provider: getVal("cfgModuleRecommendationProvider"),
          model: getVal("cfgModuleRecommendationModel"),
        },
        evaluation: {
          provider: getVal("cfgModuleEvaluationProvider"),
          model: getVal("cfgModuleEvaluationModel"),
        },
      },
      bilibili: {
        auth_method: getVal("cfgBiliAuth"),
        // An empty textarea must not wipe the synced cookie on save — omit
        // the field so the backend keeps the current value (the web desktop
        // settings page applies the same guard).
        ...(getVal("cfgBiliCookie") ? { cookie: getVal("cfgBiliCookie") } : {}),
        browser_executable: getVal("cfgBiliBrowserExecutable"),
        browser_headed: checked("cfgBiliBrowserHeaded"),
      },
      sources: {
        browser: {
          cdp_url: getVal("cfgSourcesBrowserCdp"),
          headed: checked("cfgSourcesBrowserHeaded"),
        },
        bilibili: {
          enabled: checked("cfgBilibiliEnabled", true),
        },
        // Empty-field fallbacks mirror the backend dataclass defaults
        // (budgets: 0 = uncapped) so the popup and the web settings page
        // write identical values for an untouched form.
        xiaohongshu: {
          enabled: checked("cfgXhsEnabled"),
          daily_search_budget: getInt("cfgXhsDailySearchBudget", 0),
          daily_creator_budget: getInt("cfgXhsDailyCreatorBudget", 0),
          task_interval_seconds: getInt("cfgXhsTaskInterval", 45),
        },
        douyin: {
          enabled: checked("cfgDouyinEnabled"),
          mode: "direct",
          ...(getVal("cfgDouyinCookie") ? { cookie: getVal("cfgDouyinCookie") } : {}),
          cookie_env: getVal("cfgDouyinCookieEnv"),
          daily_search_budget: getInt("cfgDouyinDailySearchBudget", 0),
          daily_hot_budget: getInt("cfgDouyinDailyHotBudget", 0),
          daily_feed_budget: getInt("cfgDouyinDailyFeedBudget", 0),
          request_interval_seconds: getInt("cfgDouyinRequestInterval", 2),
        },
        youtube: {
          enabled: checked("cfgYoutubeEnabled"),
          daily_search_budget: getInt("cfgYoutubeDailySearchBudget", 0),
          daily_trending_budget: getInt("cfgYoutubeDailyTrendingBudget", 0),
          daily_channel_budget: getInt("cfgYoutubeDailyChannelBudget", 0),
          request_interval_seconds: getInt("cfgYoutubeRequestInterval", 2),
          min_interval_minutes: getInt("cfgYoutubeMinInterval", 60),
        },
        twitter: {
          enabled: checked("cfgTwitterEnabled"),
          mode: "cookie",
          ...(getVal("cfgTwitterCookie") ? { cookie: getVal("cfgTwitterCookie") } : {}),
          cookie_env: getVal("cfgTwitterCookieEnv"),
          daily_search_budget: getInt("cfgTwitterDailySearchBudget", 0),
          daily_feed_budget: getInt("cfgTwitterDailyFeedBudget", 0),
          daily_creator_budget: getInt("cfgTwitterDailyCreatorBudget", 0),
          request_interval_seconds: getInt("cfgTwitterRequestInterval", 3),
          min_interval_minutes: getInt("cfgTwitterMinInterval", 60),
        },
      },
      scheduler: {
        enabled: !checked("cfgSchedulerEnabled"),
        pause_on_extension_disconnect: checked("cfgPauseOnDisconnect"),
        extension_disconnect_grace_seconds: getInt("cfgExtensionDisconnectGrace", 90),
        pool_target_count: getInt("cfgPoolTarget", 300),
        account_sync_interval_hours: getInt("cfgAccountSyncInterval", 6),
        refresh_check_interval_seconds: getInt("cfgRefreshCheckInterval", 60),
        signal_event_threshold: getInt("cfgSignalEventThreshold", 6),
        feedback_batch_threshold: getInt("cfgFeedbackBatchThreshold", 3),
        trending_refresh_hours: getInt("cfgTrendingRefreshHours", 3),
        explore_refresh_hours: getInt("cfgExploreRefreshHours", 12),
        discovery_limit: getInt("cfgDiscoveryLimit", 30),
        proactive_push_interval_seconds: getInt("cfgProactivePushInterval", 120),
        speculator_idle_interval_minutes: getInt("cfgSpeculatorIdleInterval", 30),
        pool_source_shares: {
          bilibili: getInt("cfgPoolShareBilibili", 8),
          xiaohongshu: getInt("cfgPoolShareXhs", 1),
          douyin: getInt("cfgPoolShareDouyin", 1),
          youtube: getInt("cfgPoolShareYoutube", 1),
          twitter: getInt("cfgPoolShareTwitter", 1),
        },
        speculation_interval_minutes: getInt("cfgSpeculationInterval", 10),
        speculation_ttl_days: getInt("cfgSpeculationTtl", 3),
        speculation_cooldown_days: getInt("cfgSpeculationCooldown", 7),
        speculation_confirmation_threshold: getInt("cfgSpeculationThreshold", 3),
        speculation_max_active: getInt("cfgSpeculationMaxActive", 5),
        speculation_max_primary_interests: getInt("cfgSpeculationMaxPrimary", 15),
        speculation_max_secondary_interests: getInt("cfgSpeculationMaxSecondary", 60),
        auto_update_enabled: checked("cfgAutoUpdate"),
        auto_update_check_interval_hours: getInt("cfgAutoUpdateInterval", 6),
      },
      storage: {
        db_path: getVal("cfgStorageDbPath"),
      },
      logging: {
        level: getVal("cfgLogLevel"),
        file_level: getVal("cfgLogFileLevel"),
        directory: logPath.directory,
        filename: logPath.filename,
        max_file_size_mb: getInt("cfgLogMaxFileSize", 100),
        backup_count: getInt("cfgLogBackupCount", 1),
        aggregate_budget_mb: getInt("cfgLogAggregateBudget", 500),
        unmanaged_truncate_mb: getInt("cfgLogUnmanagedTruncate", 200),
        unmanaged_max_age_days: getInt("cfgLogUnmanagedMaxAge", 30),
      },
    };
  }

  function renderProbeResult(statusEl, result) {
    if (!statusEl) return;
    const ok = Boolean(result?.ok);
    const provider = result?.provider ? ` ${result.provider}` : "";
    const model = result?.model ? ` / ${result.model}` : "";
    const latency = Number.isFinite(Number(result?.latency_ms)) && Number(result.latency_ms) > 0
      ? ` (${Math.round(Number(result.latency_ms))}ms)`
      : "";
    const detail = result?.message || result?.error || (ok ? "服务可用" : "服务不可用");
    statusEl.dataset.tone = ok ? "success" : "error";
    statusEl.textContent = `${ok ? "可用" : "不可用"}${provider}${model}${latency}: ${detail}`;
  }

  function renderProbePending(statusEl, label) {
    if (!statusEl) return;
    statusEl.dataset.tone = "pending";
    statusEl.textContent = `${label} 探测中...`;
  }

  async function runLlmConfigProbe(button, statusEl) {
    if (!button) return;
    button.disabled = true;
    renderProbePending(statusEl, "LLM");
    try {
      const result = await probeConfigService("llm", collectForm());
      renderProbeResult(statusEl, result);
    } catch (err) {
      renderProbeResult(statusEl, {
        ok: false,
        error: err?.message || "LLM 探测失败",
      });
    } finally {
      button.disabled = false;
    }
  }

  async function runEmbeddingConfigProbe(button, statusEl) {
    if (!button) return;
    button.disabled = true;
    renderProbePending(statusEl, "Embedding");
    try {
      const result = await probeConfigService("embedding", collectForm());
      renderProbeResult(statusEl, result);
    } catch (err) {
      renderProbeResult(statusEl, {
        ok: false,
        error: err?.message || "Embedding 探测失败",
      });
    } finally {
      button.disabled = false;
    }
  }

  const probeLlmBtn = document.getElementById("cfgProbeLlm");
  const probeLlmStatus = document.getElementById("cfgProbeLlmStatus");
  if (probeLlmBtn instanceof HTMLButtonElement) {
    probeLlmBtn.addEventListener("click", () => {
      void runLlmConfigProbe(probeLlmBtn, probeLlmStatus);
    });
  }

  const probeEmbeddingBtn = document.getElementById("cfgProbeEmbedding");
  const probeEmbeddingStatus = document.getElementById("cfgProbeEmbeddingStatus");
  if (probeEmbeddingBtn instanceof HTMLButtonElement) {
    probeEmbeddingBtn.addEventListener("click", () => {
      void runEmbeddingConfigProbe(probeEmbeddingBtn, probeEmbeddingStatus);
    });
  }

  const backendCheckBtn = document.getElementById("backendUpdateCheck");
  const backendApplyBtn = document.getElementById("backendUpdateApply");
  if (backendCheckBtn instanceof HTMLButtonElement) {
    backendCheckBtn.addEventListener("click", async () => {
      backendCheckBtn.disabled = true;
      try {
        const payload = await checkBackendUpdate();
        renderBackendUpdateStatus(payload);
        showToast("后端更新检查完成", "success");
      } catch {
        showToast("后端更新检查失败", "error");
      } finally {
        backendCheckBtn.disabled = false;
      }
    });
  }
  if (backendApplyBtn instanceof HTMLButtonElement) {
    backendApplyBtn.addEventListener("click", async () => {
      const tag = backendApplyBtn.dataset.tag || "";
      backendApplyBtn.disabled = true;
      try {
        const payload = await applyBackendUpdate(tag);
        renderBackendUpdateStatus({ state: payload.state, reason: payload.reason, latest_tag: tag });
        showToast("后端更新已开始，稍后会重启", "success");
      } catch {
        showToast("后端更新未能开始", "error");
      } finally {
        backendApplyBtn.disabled = false;
      }
    });
  }

  gearBtn.addEventListener("click", async () => {
    overlay.hidden = false;
    toast.hidden = true;
    issuesContainer.innerHTML = "";
    hideConfigBanners();
    setSaveButtonMode("");
    setActiveSettingsPanel("models");
    // Backend port is stored in chrome.storage, not on the backend, so it
    // populates even when the backend is unreachable — which is the whole
    // point of changing it.
    await populateBackendEndpoint();
    void loadBackendUpdateStatus();
    void authControl.reload();
    void autostartControl.reload();
    try {
      const cfg = await fetchConfig();
      populateForm(cfg);
    } catch {
      const cached = await readCachedConfigSnapshot();
      if (cached?.config) {
        populateForm(cached.config);
        showConfigBanner(
          bannerOffline,
          `后端不可达，已使用 ${formatCachedAt(cached.cached_at)} 的缓存配置。`,
          "warning",
        );
        setSaveButtonMode("warning");
        showToast("后端不可达，当前显示缓存配置。", "error");
        return;
      }
      showConfigBanner(
        bannerNoCache,
        "后端不可达且没有缓存配置。请先启动 daemon 后再打开设置。",
        "error",
      );
      showToast("无法加载配置，请确认后端已启动。", "error");
    }
  });

  backBtn.addEventListener("click", () => {
    overlay.hidden = true;
  });

  const suggestBtn = document.getElementById("cfgSuggestPoolShares");
  if (suggestBtn) {
    suggestBtn.addEventListener("click", async () => {
      suggestBtn.disabled = true;
      toast.hidden = true;
      try {
        const suggestion = await fetchSourceShareSuggestion({
          enabled_sources: {
            bilibili: checked("cfgBilibiliEnabled", true),
            xiaohongshu: checked("cfgXhsEnabled"),
            douyin: checked("cfgDouyinEnabled"),
            youtube: checked("cfgYoutubeEnabled"),
            twitter: checked("cfgTwitterEnabled"),
          },
          configured_shares: {
            bilibili: getInt("cfgPoolShareBilibili", 8),
            xiaohongshu: getInt("cfgPoolShareXhs", 1),
            douyin: getInt("cfgPoolShareDouyin", 1),
            youtube: getInt("cfgPoolShareYoutube", 1),
            twitter: getInt("cfgPoolShareTwitter", 1),
          },
        });
        const shares = suggestion?.suggested_shares || {};
        if (shares.bilibili !== undefined) setVal("cfgPoolShareBilibili", shares.bilibili);
        if (shares.xiaohongshu !== undefined) setVal("cfgPoolShareXhs", shares.xiaohongshu);
        if (shares.douyin !== undefined) setVal("cfgPoolShareDouyin", shares.douyin);
        if (shares.youtube !== undefined) setVal("cfgPoolShareYoutube", shares.youtube);
        if (shares.twitter !== undefined) setVal("cfgPoolShareTwitter", shares.twitter);
        showToast("已按已有信号填入建议比例，保存后生效。", "success");
      } catch (err) {
        showToast(`生成建议失败: ${err.message}`, "error");
      } finally {
        suggestBtn.disabled = false;
      }
    });
  }

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "保存中...";
    toast.hidden = true;
    try {
      // Backend endpoint lives in chrome.storage, not the backend's
      // config.toml — persist it locally first so the subsequent
      // updateConfig() PUT targets the new origin.
      let endpointChanged = false;
      let newEndpointLabel = null;
      const hostRaw = backendHostInput instanceof HTMLInputElement
        ? backendHostInput.value.trim() : "";
      const portRaw = backendPortInput instanceof HTMLInputElement
        ? backendPortInput.value.trim() : "";
      if (hostRaw !== "" && !isValidBackendHost(hostRaw)) {
        showToast("后端地址必须是有效的 IP 地址或主机名。", "error");
        return;
      }
      if (portRaw !== "" && !isValidBackendPort(portRaw)) {
        showToast("后端端口必须是 1-65535 的整数。", "error");
        return;
      }
      {
        const previous = await getBackendEndpointConfig();
        const next = await updateBackendEndpoint(hostRaw, portRaw || "8420");
        newEndpointLabel = `${next.host}:${next.port}`;
        endpointChanged = next.host !== previous.host || next.port !== previous.port;
      }

      const data = collectForm();
      try {
        const result = await updateConfig(data);
        if (result.config) {
          applyRuntimeConfig(result.config);
          renderIssues(result.config.issues);
          renderDegradedBanner(result.config);
        }
        const tone = result.restart_required ? "warning" : result.reloaded ? "success" : "warning";
        showToast(result.message || "配置已保存。", tone);
      } catch (err) {
        if (err?.name === "AbortError") {
          showToast(
            "后端处理超时，保存请求可能已写入；热重载可能仍在后台进行。请稍后刷新设置确认。",
            "warning",
          );
          return;
        }
        if (renderStructuredConfigError(err)) {
          return;
        }
        if (endpointChanged) {
          showToast(
            `后端已切换为 ${newEndpointLabel}，但保存其余配置失败。请确认后端已在该地址运行后重试。`,
            "warning",
          );
        } else {
          throw err;
        }
      }

      if (endpointChanged) {
        // Rebind the runtime stream against the new origin and refresh
        // the online indicator. If the backend isn't yet running on the
        // new port these will retry per the WS backoff and the popup
        // status will flip to offline — exactly the signal the user
        // needs to remember to start the daemon with --port.
        connectRuntimeStream();
        state.online = await checkBackendStatus();
        setStatus(state.online);
      }
    } catch (err) {
      if (!renderStructuredConfigError(err)) {
        showToast(`保存失败: ${err.message}`, "error");
      }
    } finally {
      saveBtn.disabled = false;
      setSaveButtonMode(state.runtimeConfig?.degraded ? "degraded" : "");
    }
  });
}

// Session-scoped dismissal so we don't nag on every popup open after the
// user explicitly closes the banner. Re-appears next session if embedding
// is still disabled.
const EMBEDDING_BANNER_DISMISS_KEY = "embeddingBannerDismissed";

async function enableLocalOllamaEmbedding(enableBtn) {
  const original = enableBtn ? enableBtn.textContent : "";
  if (enableBtn) {
    enableBtn.disabled = true;
    enableBtn.textContent = "启用中…";
  }
  try {
    await updateConfig({
      llm: {
        embedding: {
          provider: "ollama",
          model: "bge-m3",
          base_url: "http://localhost:11434/v1",
        },
      },
    });
    // Re-check: hot-reload rebuilds the embedding service in-process and
    // /api/health probes it live, so embedding_ready only flips true once
    // Ollama actually serves a vector. Don't claim success on a config
    // write alone.
    const health = await fetchHealth();
    const banner = document.getElementById("embeddingBanner");
    if (health && health.embedding_ready) {
      if (banner) banner.hidden = true;
      setHint("已启用本地 Ollama 语义去重，重复内容会少很多。", "success");
    } else {
      if (enableBtn) {
        enableBtn.disabled = false;
        enableBtn.textContent = "重试";
      }
      setHint(
        "配置已写入，但 Ollama 还没就绪。请确认已运行 `ollama serve` 并 `ollama pull bge-m3`。",
        "error",
      );
    }
  } catch {
    if (enableBtn) {
      enableBtn.disabled = false;
      enableBtn.textContent = "重试";
    }
    setHint("启用失败，请检查后端连接后重试。", "error");
  }
}

async function maybeShowEmbeddingBanner() {
  const banner = document.getElementById("embeddingBanner");
  if (!banner) return;
  if (sessionStorage.getItem(EMBEDDING_BANNER_DISMISS_KEY) === "1") return;
  const health = await fetchHealth();
  if (!shouldShowEmbeddingBanner(health)) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  const enableBtn = document.getElementById("embeddingBannerEnable");
  const dismissBtn = document.getElementById("embeddingBannerDismiss");
  if (enableBtn && !enableBtn.dataset.bound) {
    enableBtn.dataset.bound = "1";
    enableBtn.addEventListener("click", () => void enableLocalOllamaEmbedding(enableBtn));
  }
  if (dismissBtn && !dismissBtn.dataset.bound) {
    dismissBtn.dataset.bound = "1";
    dismissBtn.addEventListener("click", () => {
      sessionStorage.setItem(EMBEDDING_BANNER_DISMISS_KEY, "1");
      banner.hidden = true;
    });
  }
}

async function initializePopup() {
  const params = new URLSearchParams(window.location.search);
  const requestedTab = params.get("tab");
  state.delightHighlightBvid = params.get("delight")?.trim() || "";
  bindTabs();
  bindProfileHistoryLoading();
  initRecommendationAutoLoadIntent();
  bindRefreshButton();
  bindActivityToggle();
  bindChat();
  bindOpenWeb();
  bindMobileQr();
  bindSettings();
  bindStarButton();

  bindMessages();
  setActiveTab(
    requestedTab === "profile" || requestedTab === "chat" || requestedTab === "recommend"
      ? requestedTab
      : "recommend",
  );
  setHint("先看看本地后端连上没。");
  await initializeRecommendations();
  void maybeShowEmbeddingBanner();
  // Re-check when the panel regains visibility/focus so a stale "semantic
  // dedup off" banner clears itself once embedding recovers — the one-shot
  // call above never re-runs while a side panel stays open.
  installEmbeddingBannerAutoRefresh(maybeShowEmbeddingBanner);
  await hydrateChatHistory();
  // Always fetch profile-summary on startup so the messages inbox is
  // populated regardless of which tab the user lands on.  Without this
  // the inbox stays empty until the user manually opens the profile
  // tab (the place where loadProfileSummary historically fired).
  void loadProfileSummary();
  connectRuntimeStream();
}

void initializePopup();
