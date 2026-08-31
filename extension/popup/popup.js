import {
  buildStaleProbeResponseState,
  buildImageProxyPath,
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildContentUrl,
  buildRecommendationClickPayload,
  buildVideoUrl,
  formatRecommendationAuthorLine,
  formatRelativeTimestamp,
  formatPublishedTime,
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
  platformDisplayName,
  probeMessageKey,
  reconcileRecommendationReplacement,
  resolveInitBangumiUsername,
  shouldDisplayProbeFromWebSocket,
  shouldHydrateProbe,
  shouldAutoLoadRecommendations,
  shouldFetchProfileSummary,
  shouldSubmitChatOnEnter,
  validateCommentInput,
} from "./popup-helpers.js";
import { createRuntimeStreamClient } from "./popup-stream.js";
import {
  createBackendConnectionCoordinator,
  createOfflineBackendPoller,
} from "./popup-connection-poller.js";
import {
  buildInitChecklist,
  describeInitFailure,
  describeInitReason,
  describeInitStatusReason,
  describeInitStartError,
  embeddingRepairStartAccepted,
  initProgressView,
  INIT_EXPECTATION_HINT,
  INIT_RUNNING_HINT,
  INIT_SOURCE_OPTIONS,
  INIT_SOURCE_LOGIN_HINT,
  shouldAttachEmbeddingPullProgress,
  shouldAttachRunningInitProgress,
  stalenessView,
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
import { initExtLogin } from "./popup-ext-login.js";
import { clearPopupSession, readPopupSessionToken } from "./popup-device-auth.js";
import { initAutostartControl } from "./popup-autostart-control.js";
import {
  createQrSvgMarkup,
  getMobileQrViewState,
  isLoopbackMobileHost,
} from "./popup-qr.js";
import {
  createSavedToggleRegistry,
  captureSavedFocus,
  createRetainedSavedListState,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  createSavedSyncTaskTracker,
  getSavedSyncPresentation,
  isSavedSyncEligibleStatus,
  normalizeCanonicalSavedItem,
  partitionSavedQueueResults,
  restoreSavedFocus,
  sanitizeSavedSyncTask,
  summarizeSavedSyncResults,
  updateSavedBatchButtonState,
} from "./popup-saved-sync.js";
import {
  installEmbeddingBannerAutoRefresh,
  shouldShowEmbeddingBanner,
} from "./popup-embedding-banner.js";
import {
  appendRecommendations,
  actOnChatCard,
  checkBackendStatus,
  fetchActivityFeed,
  fetchUpdateStatus,
  checkBackendUpdate,
  applyBackendUpdate,
  cancelInit,
  fetchChatContext,
  fetchChatTurn,
  fetchChatTurns,
  fetchConfig,
  discoverConfigModels,
  fetchEmbeddingRepairStatus,
  fetchHealth,
  fetchInitStatus,
  fetchPendingDelight,
  fetchPendingDelightBatch,
  fetchPendingConfirmations,
  fetchProfileSummary,
  fetchProjectStats,
  fetchRecommendations,
  fetchContentHistory,
  fetchDiagnosticsAlerts,
  fetchRuntimeStatus,
  fetchSourceShareSuggestion,
  fetchSourcesStatus,
  fetchV2exIdentity,
  acceptV2exBrowserIdentity,
  markDelightSent,
  openPendingConfirmation,
  probeConfigService,
  startEmbeddingRepair,
  startInit,
  readCachedConfigSnapshot,
  reconcileContentHistoryPage,
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
  updateConfig,
  fetchSavedItems,
  pollSavedSyncTask,
  removeSavedItem,
  saveItem,
  savedItemStatus,
  sendBehaviorEvents,
  syncSavedItems,
  verifySource,
} from "./popup-api.js";

const dialogueConfirmation = globalThis.OpenBiliClawDialogueConfirmation;
if (!dialogueConfirmation) {
  throw new Error("dialogue-confirmation shared helper did not load");
}
const {
  activateReplyQuote,
  clearContextSelection,
  contextBarMarkup,
  contextErrorCode,
  contextErrorMessage,
  contextSelectionFromTurn,
  executeCardAction,
  executePendingConfirmationOpen,
  isCardTurn,
  isDialogueReplyTurn,
  isTerminalCardTurn,
  isQuestionTurn,
  normalizeContextPreview,
  readContextSelection,
  replyQuoteMarkup,
  renderMarkdown,
  renderPendingListMarkup,
  renderTurnMarkup,
  selectDialogueTurns,
  writeContextSelection,
} = dialogueConfirmation;
const dialogueCardActionAbortController = new AbortController();

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
  llmDraft: null,
  llmProbeResults: new Map(),
  llmEditingInstanceId: "",
  initBangumiUsername: "",
  initBangumiUsernameTouched: false,
  initBangumiUsernamePrefilled: false,
  initBangumiToken: "",
  initLlmConcurrency: 3,
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
  pendingConfirmations: {
    count: 0,
    items: [],
    expanded: false,
  },
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
let dialogueConfirmationRefreshTimer = null;
let suppressChatAutoScroll = false;

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
  emptyAction: document.getElementById("emptyAction"),
  initPanel: document.getElementById("initPanel"),
  initSources: document.getElementById("initSources"),
  initChecklist: document.getElementById("initChecklist"),
  initProgress: document.getElementById("initProgress"),
  initProgressBar: document.getElementById("initProgressBar"),
  initProgressLabel: document.getElementById("initProgressLabel"),
  initStallHint: document.getElementById("initStallHint"),
  initStartBtn: document.getElementById("initStartBtn"),
  initCancelBtn: document.getElementById("initCancelBtn"),
  initStartReason: document.getElementById("initStartReason"),
  list: document.getElementById("recommendationList"),
  refreshRecommendationsButton: document.getElementById("refreshRecommendationsButton"),
  poolStatus: document.getElementById("poolStatus"),
  poolAvailable: document.getElementById("poolAvailable"),
  poolReplenished: document.getElementById("poolReplenished"),
  poolTopics: document.getElementById("poolTopics"),
  delightSlot: document.getElementById("delightSlot"),
  tabRecommend: document.getElementById("tabRecommend"),
  tabLibrary: document.getElementById("tabLibrary"),
  tabWatchLater: document.getElementById("tabWatchLater"),
  tabFavorites: document.getElementById("tabFavorites"),
  tabHistory: document.getElementById("tabHistory"),
  tabProfile: document.getElementById("tabProfile"),
  tabChat: document.getElementById("tabChat"),
  viewRecommend: document.getElementById("viewRecommend"),
  viewLibrary: document.getElementById("viewLibrary"),
  viewWatchLater: document.getElementById("viewWatchLater"),
  viewFavorites: document.getElementById("viewFavorites"),
  viewHistory: document.getElementById("viewHistory"),
  viewProfile: document.getElementById("viewProfile"),
  viewChat: document.getElementById("viewChat"),
  watchLaterList: document.getElementById("watchLaterList"),
  watchLaterEmpty: document.getElementById("watchLaterEmpty"),
  favoritesList: document.getElementById("favoritesList"),
  favoritesEmpty: document.getElementById("favoritesEmpty"),
  watchLaterSyncAll: document.getElementById("watchLaterSyncAll"),
  watchLaterSyncStatus: document.getElementById("watchLaterSyncStatus"),
  favoritesSyncAll: document.getElementById("favoritesSyncAll"),
  favoritesSyncStatus: document.getElementById("favoritesSyncStatus"),
  historyRefresh: document.getElementById("historyRefresh"),
  historySections: document.getElementById("historySections"),
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
  chatPendingToggle: document.getElementById("chatPendingToggle"),
  chatPendingCount: document.getElementById("chatPendingCount"),
  chatPendingList: document.getElementById("chatPendingList"),
  chatPendingTabCount: document.getElementById("chatPendingTabCount"),
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

const POPUP_OVERLAY_FOCUS_SELECTOR = [
  'button:not([disabled]):not([tabindex="-1"])',
  'a[href]:not([tabindex="-1"])',
  'input:not([disabled]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  '[tabindex="0"]',
].join(",");
const popupOverlayReturnFocus = new WeakMap();
const popupOverlayBackgroundState = new Map();
let activePopupOverlay = null;

function popupOverlayFocusableElements(overlay) {
  if (!(overlay instanceof HTMLElement)) return [];
  return Array.from(overlay.querySelectorAll(POPUP_OVERLAY_FOCUS_SELECTOR))
    .filter((element) => element instanceof HTMLElement && element.getClientRects().length > 0);
}

function restorePopupOverlayBackground() {
  for (const [element, previous] of popupOverlayBackgroundState) {
    element.inert = previous.inert;
    if (previous.inertAttribute === null) element.removeAttribute("inert");
    else element.setAttribute("inert", previous.inertAttribute);
    if (previous.ariaHidden === null) element.removeAttribute("aria-hidden");
    else element.setAttribute("aria-hidden", previous.ariaHidden);
  }
  popupOverlayBackgroundState.clear();
}

function openPopupOverlay(overlay, { trigger = null, initialFocus = null } = {}) {
  if (!(overlay instanceof HTMLElement)) return;
  if (activePopupOverlay && activePopupOverlay !== overlay) {
    activePopupOverlay.hidden = true;
    restorePopupOverlayBackground();
  }
  const focusReturnTarget = trigger instanceof HTMLElement
    ? trigger
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  if (focusReturnTarget) popupOverlayReturnFocus.set(overlay, focusReturnTarget);
  overlay.hidden = false;
  const shell = overlay.parentElement;
  if (shell) {
    for (const child of shell.children) {
      if (!(child instanceof HTMLElement) || child === overlay) continue;
      popupOverlayBackgroundState.set(child, {
        inert: child.inert,
        inertAttribute: child.getAttribute("inert"),
        ariaHidden: child.getAttribute("aria-hidden"),
      });
      child.inert = true;
      child.setAttribute("inert", "");
      child.setAttribute("aria-hidden", "true");
    }
  }
  activePopupOverlay = overlay;
  const focusTarget = initialFocus instanceof HTMLElement
    ? initialFocus
    : popupOverlayFocusableElements(overlay)[0];
  focusTarget?.focus({ preventScroll: true });
}

function closePopupOverlay(overlay) {
  if (!(overlay instanceof HTMLElement)) return;
  overlay.hidden = true;
  if (activePopupOverlay === overlay) {
    restorePopupOverlayBackground();
    activePopupOverlay = null;
  }
  const returnFocus = popupOverlayReturnFocus.get(overlay);
  popupOverlayReturnFocus.delete(overlay);
  if (returnFocus instanceof HTMLElement && returnFocus.isConnected && !returnFocus.inert) {
    returnFocus.focus({ preventScroll: true });
  }
}

function bindPopupOverlayKeyboard(overlay, close) {
  if (!(overlay instanceof HTMLElement)) return;
  overlay.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = popupOverlayFocusableElements(overlay);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

async function setProxyImageSrc(image, coverUrl) {
  const path = buildImageProxyPath(coverUrl);
  if (!path) return false;
  const origin = await getBackendOrigin();
  const token = await readPopupSessionToken();
  let url = `${origin}${path}`;
  if (token) url += `&token=${encodeURIComponent(token)}`;
  image.src = url;
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
  const token = await readPopupSessionToken();
  const loaders = (Array.isArray(items) ? items : [])
    .map((item) => {
      const path = item?.cover_url ? buildImageProxyPath(item.cover_url) : null;
      if (!path) return null;
      return new Promise((resolve) => {
        const img = new Image();
        img.decoding = "async";
        img.addEventListener("load", () => resolve(), { once: true });
        img.addEventListener("error", () => resolve(), { once: true });
        let url = `${origin}${path}`;
        if (token) url += `&token=${encodeURIComponent(token)}`;
        img.src = url;
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
let offlineBackendPoller = null;
const backendConnectionCoordinator = createBackendConnectionCoordinator({
  checkBackendStatus,
  onStatusChange(status) {
    state.online = status !== "offline";
    setStatus(status);
    if (status === "offline") {
      offlineBackendPoller?.start();
      return;
    }
    offlineBackendPoller?.stop();
  },
});
offlineBackendPoller = createOfflineBackendPoller({
  isOnline: () => state.online,
  checkBackendStatus,
  onOnline: async () => {
    const wasOnline = state.online;
    backendConnectionCoordinator.markHttpReachable();
    if (!wasOnline) {
      setHint("后端连上了，正在刷新。", "success");
    }
    scheduleRecommendationsRefresh({ delayMs: 0 });
    scheduleDialogueConfirmationRefresh();
    void maybeShowEmbeddingBanner();
  },
});
const CHAT_SESSION = "popup";
const CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500;
const CHAT_POLL_INTERVAL_MS = 1200;
const CHAT_POLL_DEADLINE_MS = 180_000;
const activeChatPolls = new Map();
let chatHistoryRefreshTimer = null;
let chatHistoryHydrationInFlight = false;
let lastChatHistorySignature = null;
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
const THUMBS_UP_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>';
const THUMBS_DOWN_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>';
const MESSAGE_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>';
const HISTORY_IMAGE_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
const HISTORY_RESTORE_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';

const CONTENT_HISTORY_PAGE_SIZE = 12;
const CONTENT_HISTORY_SECTIONS = [
  { category: "clicked", eyebrow: "Opened", title: "主动点开过", description: "你明确选择打开的内容，最近一次操作排在前面。" },
  { category: "shown", eyebrow: "Passed by", title: "出现过，但没点开", description: "曾进入推荐列表、但近 30 天没有打开记录的内容。" },
  { category: "removed", eyebrow: "Recently removed", title: "最近移除", description: "从保存列表移除、忽略或标记不感兴趣的内容。" },
];
const contentHistoryState = Object.fromEntries(CONTENT_HISTORY_SECTIONS.map(({ category }) => [
  category,
  {
    items: [],
    total: 0,
    nextCursor: "",
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    notice: "",
    refreshRequired: false,
  },
]));
let contentHistoryGeneration = 0;
let contentHistoryLoadedAt = 0;

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

function setStatus(status) {
  if (
    !(elements.statusBadge instanceof HTMLElement) ||
    !(elements.statusDot instanceof HTMLElement) ||
    !(elements.statusLabel instanceof HTMLElement)
  ) {
    return;
  }
  const badgeState = getConnectionBadgeState(status);
  elements.statusBadge.dataset.tone = badgeState.tone;
  elements.statusDot.classList.toggle("offline", badgeState.tone === "offline");
  elements.statusDot.classList.toggle("reconnecting", badgeState.tone === "reconnecting");
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
  if (!state.initBangumiUsernameTouched) {
    state.initBangumiUsername = String(config.sources?.bangumi?.username || "").trim();
    // Mark that a successful /api/config prefill populated the field, so an
    // explicit clear afterwards is a deliberate reset (sends username="") while
    // an untouched or never-prefilled empty field omits it (keeps configured).
    state.initBangumiUsernamePrefilled = true;
    const input = document.getElementById("initBangumiUsername");
    if (input instanceof HTMLInputElement) {
      input.value = state.initBangumiUsername;
    }
  }
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

const POPUP_LIBRARY_STORAGE_KEY = "openbiliclaw.popup.contentLibraryTab";
const POPUP_LIBRARY_TABS = ["watchLater", "favorites", "history"];
const popupLibraryScroll = new Map();
let popupLibraryTab = "watchLater";
let popupLibraryVisible = false;

function normalizePopupLibraryTab(value, fallback = "watchLater") {
  const normalized = String(value || "").trim().toLowerCase();
  return {
    watchlater: "watchLater",
    "watch-later": "watchLater",
    watch_later: "watchLater",
    favorites: "favorites",
    favorite: "favorites",
    history: "history",
  }[normalized] || fallback;
}

function storedPopupLibraryTab() {
  try { return normalizePopupLibraryTab(localStorage.getItem(POPUP_LIBRARY_STORAGE_KEY)); }
  catch { return "watchLater"; }
}

function persistPopupLibraryTab(tab) {
  try { localStorage.setItem(POPUP_LIBRARY_STORAGE_KEY, tab); } catch { /* unavailable */ }
}

function loadPopupLibraryTab(tab) {
  if (tab === "watchLater") void loadWatchLater();
  else if (tab === "favorites") void loadFavorites();
  else void refreshContentHistory();
}

function setActiveLibraryTab(value, { focus = false, entering = false, forceLoad = false } = {}) {
  const tabName = normalizePopupLibraryTab(value, popupLibraryTab);
  const changed = popupLibraryTab !== tabName;
  if (popupLibraryVisible && changed && elements.content instanceof HTMLElement) {
    popupLibraryScroll.set(popupLibraryTab, elements.content.scrollTop);
  }
  popupLibraryTab = tabName;
  persistPopupLibraryTab(tabName);
  const tabs = [
    ["watchLater", elements.tabWatchLater, elements.viewWatchLater],
    ["favorites", elements.tabFavorites, elements.viewFavorites],
    ["history", elements.tabHistory, elements.viewHistory],
  ];
  for (const [name, button, panel] of tabs) {
    const selected = name === tabName;
    button?.classList.toggle("is-active", selected);
    button?.setAttribute("aria-selected", String(selected));
    if (button instanceof HTMLButtonElement) button.tabIndex = selected ? 0 : -1;
    if (panel instanceof HTMLElement) panel.hidden = !selected;
  }
  if (changed || entering || forceLoad) loadPopupLibraryTab(tabName);
  if ((changed || entering) && elements.content instanceof HTMLElement) {
    requestAnimationFrame(() => {
      elements.content.scrollTop = popupLibraryScroll.get(tabName) || 0;
      if (focus) elements.viewLibrary?.querySelector('.library-tab[aria-selected="true"]')?.focus();
    });
  } else if (focus) {
    elements.viewLibrary?.querySelector('.library-tab[aria-selected="true"]')?.focus();
  }
}

function setActiveTab(requestedTab, { libraryTab = "" } = {}) {
  const legacyChild = POPUP_LIBRARY_TABS.includes(requestedTab)
    ? normalizePopupLibraryTab(requestedTab)
    : "";
  const tabName = legacyChild ? "library" : requestedTab;
  if (!["recommend", "library", "profile", "chat"].includes(tabName)) return;
  const enteringLibrary = tabName === "library" && !popupLibraryVisible;
  if (state.activeTab === "library" && tabName !== "library" && elements.content instanceof HTMLElement) {
    popupLibraryScroll.set(popupLibraryTab, elements.content.scrollTop);
  }
  popupLibraryVisible = tabName === "library";
  state.activeTab = tabName;

  const tabs = [
    ["recommend", elements.tabRecommend, elements.viewRecommend],
    ["library", elements.tabLibrary, elements.viewLibrary],
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
  if (tabName === "library") {
    setActiveLibraryTab(libraryTab || legacyChild || storedPopupLibraryTab(), { entering: enteringLibrary });
  }
  if (tabName === "chat") {
    scrollChatMessagesToBottom();
    void refreshPendingConfirmations();
    void hydrateChatHistory();
  }
}

function normalizePopupSavedItem(itemOrBvid) {
  const item = typeof itemOrBvid === "object" && itemOrBvid ? itemOrBvid : { bvid: itemOrBvid };
  return {
    ...item,
    ...normalizeCanonicalSavedItem(item),
  };
}

async function toggleWatchLaterSaved(itemOrBvid) {
  const item = normalizePopupSavedItem(itemOrBvid);
  return watchLaterToggles.toggle(item.item_key, {
    add: () => saveItem("watch_later", item),
    remove: () => removeSavedItem("watch_later", item.item_key),
  });
}

async function toggleFavoriteSaved(itemOrBvid) {
  const item = normalizePopupSavedItem(itemOrBvid);
  return favoriteToggles.toggle(item.item_key, {
    add: () => saveItem("favorite", item),
    remove: () => removeSavedItem("favorite", item.item_key),
  });
}

async function toggleSavedWithFeedback(label, itemOrBvid, registry, toggle) {
  const item = normalizePopupSavedItem(itemOrBvid);
  setHint(`正在更新${label}…`, "info");
  try {
    await toggle(item);
    setHint(
      registry.isSaved(item.item_key) ? `已保存到${label}` : `已从${label}移除`,
      "success",
    );
  } catch {
    // The registry has already restored the previous optimistic state.
    setHint(`${label}更新失败，请确认本地后端正在运行后重试。`, "error");
  }
}

function bindWatchLaterToggle(button, itemOrBvid, labels = {}) {
  const item = normalizePopupSavedItem(itemOrBvid);
  watchLaterToggles.registerButton(item.item_key, button, labels);
  void watchLaterToggles.hydrateStatus(
    item.item_key,
    (itemKey) => savedItemStatus("watch_later", itemKey),
  );
  return button;
}

function bindFavoriteToggle(button, itemOrBvid, labels = {}) {
  const item = normalizePopupSavedItem(itemOrBvid);
  favoriteToggles.registerButton(item.item_key, button, labels);
  void favoriteToggles.hydrateStatus(
    item.item_key,
    (itemKey) => savedItemStatus("favorite", itemKey),
  );
  return button;
}

// ── Platform-neutral saved views ─────────────────────────────────
const savedListStates = {
  watch_later: createRetainedSavedListState(),
  favorite: createRetainedSavedListState(),
};
const savedPendingFocus = { watch_later: null, favorite: null };
function createSavedTaskRuntime() {
  const tracker = createSavedSyncTaskTracker({ poll: (taskId) => pollSavedSyncTask(taskId) });
  return {
    tracker,
    submissions: createSavedSubmissionFence(),
    coordinator: createSavedTaskCoordinator({
      tracker,
      fetchTask: (taskId) => pollSavedSyncTask(taskId),
    }),
  };
}
const savedTaskRuntimes = {
  watch_later: createSavedTaskRuntime(),
  favorite: createSavedTaskRuntime(),
};
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    for (const runtime of Object.values(savedTaskRuntimes)) runtime.coordinator.resumeAll();
    scheduleDialogueConfirmationRefresh();
  }
});
window.addEventListener("pagehide", () => {
  dialogueCardActionAbortController.abort();
  for (const runtime of Object.values(savedTaskRuntimes)) runtime.coordinator.dispose();
}, { once: true });

function syncEligible(item, listKind = "") {
  const runtime = savedTaskRuntimes[listKind];
  return isSavedSyncEligibleStatus(item?.sync_status, item?.error_code, item?.sync_task_id)
    && !runtime?.submissions.has(item?.item_key)
    && !runtime?.coordinator.owns(item?.item_key);
}

function savedSyncDetail(item) {
  return getSavedSyncPresentation(
    item?.sync_status,
    item?.error_code,
    item?.resolved_target,
    item?.error_message,
    item?.sync_task_id,
  ).detail;
}

async function runSavedSync(listKind, items, button, status, reload, confirmBatch = false) {
  if (button?.disabled) return;
  const runtime = savedTaskRuntimes[listKind];
  const coordinator = runtime.coordinator;
  const selected = (Array.isArray(items) ? items : []).filter((item) => syncEligible(item, listKind));
  if (!selected.length) return;
  const platforms = Array.from(new Set(selected.map((item) => (
    platformDisplayName(item.source_platform || item.item_key?.split(":", 1)[0])
  ))));
  if (confirmBatch && !window.confirm(
    `将同步 ${selected.length} 项到 ${platforms.join("、")}，继续吗？`,
  )) return;
  const selectedKeys = selected.map((item) => item.item_key);
  if (!runtime.submissions.claim(selectedKeys)) return;

  let submitted = false;
  if (button) {
    const focusRoot = button.closest?.(".view") || button.parentElement;
    savedPendingFocus[listKind] = captureSavedFocus(focusRoot, button)
      || { kind: "list", action: "sync-all" };
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-busy", "true");
    button.textContent = "同步中…";
  }
  if (status) {
    status.removeAttribute("role");
    status.setAttribute("aria-busy", "true");
    status.textContent = `正在同步 ${selected.length} 项…`;
  }
  try {
    const task = sanitizeSavedSyncTask(
      await syncSavedItems(listKind, selectedKeys),
    );
    if (!task.task_id) throw new Error("同步任务缺少 task_id，请重试。");
    coordinator.track(task, selectedKeys, {
      onProgress: () => {
        if (status) status.textContent = `正在同步 ${selected.length} 项…`;
      },
      onBackground: () => {
        if (status) status.textContent = "仍在后台同步；可切换页面，返回后会继续更新。";
      },
      onPollError: () => {
        if (status) status.textContent = "仍在后台同步；连接恢复后会继续查询。";
      },
      onTerminal: (terminalTask) => {
        if (status) status.removeAttribute("aria-busy");
        if (status) status.textContent = summarizeSavedSyncResults(terminalTask.items) || "同步已完成";
        void reload();
      },
    });
    submitted = true;
    if (status) status.textContent = `同步任务已提交 · ${selected.length} 项`;
  } catch (error) {
    if (status) {
      status.role = "alert";
      status.textContent = error?.message || "同步失败，请稍后重试。";
    }
  } finally {
    runtime.submissions.release(selectedKeys);
    if (!submitted && button) {
      button.disabled = false;
      button.setAttribute("aria-disabled", "false");
      button.removeAttribute("aria-busy");
    }
    if (!submitted && status) status.removeAttribute("aria-busy");
    await reload();
  }
}

async function loadSavedList(listKind, { list, empty, syncAll, status, toggles }) {
  if (!(list instanceof HTMLElement)) return;
  const focusRoot = list.closest?.(".view") || list;
  const focusToken = captureSavedFocus(focusRoot) || savedPendingFocus[listKind];
  const retained = savedListStates[listKind];
  const coordinator = savedTaskRuntimes[listKind].coordinator;
  const hadLoadError = Boolean(retained.snapshot().error);
  try {
    const data = await fetchSavedItems(listKind, 100, 0);
    retained.commit({
      items: Array.isArray(data?.items) ? data.items.map(normalizePopupSavedItem) : [],
      total: data?.total,
    });
    await coordinator.recover(retained.snapshot().items, {
      onProgress: () => {
        if (status) status.textContent = "正在同步已恢复的任务…";
      },
      onBackground: () => {
        if (status) status.textContent = "仍在后台同步；可切换页面，返回后会继续更新。";
      },
      onPollError: () => {
        if (status) status.textContent = "同步状态查询超时；连接恢复后会继续查询。";
      },
      onTerminal: (task) => {
        if (status) status.textContent = summarizeSavedSyncResults(task.items) || "同步已完成";
        void loadSavedList(listKind, { list, empty, syncAll, status, toggles });
      },
    });
    if (status && hadLoadError) {
      status.removeAttribute("role");
      status.replaceChildren();
    }
  } catch (error) {
    retained.fail(error);
    if (status) {
      status.role = "alert";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "saved-load-retry";
      retry.dataset.savedListAction = "retry";
      retry.textContent = "重试加载";
      retry.addEventListener("click", (event) => {
        savedPendingFocus[listKind] = captureSavedFocus(focusRoot, event.currentTarget);
        void loadSavedList(listKind, { list, empty, syncAll, status, toggles });
      });
      status.replaceChildren(
        document.createTextNode(`${retained.snapshot().error} `),
        retry,
      );
    }
  }
  const { items } = retained.snapshot();
  list.replaceChildren();
  if (empty instanceof HTMLElement) empty.hidden = items.length > 0;
  for (const item of items) {
    toggles.setSaved(item.item_key, true);
    list.appendChild(buildSavedCard(listKind, item, { list, empty, toggles }));
  }
  if (restoreSavedFocus(focusRoot, focusToken)) savedPendingFocus[listKind] = null;
  const pendingCount = items.filter((item) => syncEligible(item, listKind)).length;
  if (syncAll instanceof HTMLButtonElement) {
    syncAll.textContent = `同步未同步内容（${pendingCount}）`;
    updateSavedBatchButtonState(syncAll, pendingCount);
    syncAll.onclick = () => runSavedSync(
      listKind, items, syncAll, status,
      () => loadSavedList(listKind, { list, empty, syncAll, status, toggles }),
      true,
    );
  }
}

async function loadWatchLater() {
  return loadSavedList("watch_later", {
    list: elements.watchLaterList,
    empty: elements.watchLaterEmpty,
    syncAll: elements.watchLaterSyncAll,
    status: elements.watchLaterSyncStatus,
    toggles: watchLaterToggles,
  });
}

// Optimistic saved-card removal shared by the watch-later and favorites
// views. The card disappears on click; if the DELETE then fails the card is
// restored in place and the button flips to "重试". The previous code waited
// for the response before touching the DOM and swallowed errors silently —
// whenever the DELETE queued behind slow same-origin requests (covers via
// image-proxy compete for Chrome's 6-connection limit) or failed, clicking
// looked like it did nothing.
function bindSavedCardRemove(card, remove, { listKind, itemKey, requestRemove, toggles, list, empty, onRemoved }) {
  remove.addEventListener("click", async () => {
    if (remove.disabled) return;
    savedPendingFocus[listKind] = captureSavedFocus(list.closest?.(".view") || list, remove);
    remove.disabled = true;
    const anchor = card.nextElementSibling;
    card.remove();
    if (empty instanceof HTMLElement && !list?.children.length) {
      empty.hidden = false;
    }
    try {
      await requestRemove(itemKey);
      toggles.setSaved(itemKey, false);
      if (typeof onRemoved === "function") await onRemoved();
    } catch (error) {
      console.error("saved-card remove failed:", itemKey, error);
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

async function postSavedFeedback(item, feedbackType, note = "") {
  const contentId = item.content_id || item.bvid || "";
  const retryKey = [
    item.item_key || item.id || contentId,
    feedbackType,
    note,
  ].join("|");
  const res = await sendBehaviorEvents([{
    type: "feedback",
    source_platform: item.source_platform || "bilibili",
    title: item.title || "",
    url: buildContentUrl(item) || item.content_url || "",
    timestamp: Date.now(),
    metadata: {
      feedback_type: feedbackType,
      bvid: contentId,
      content_id: contentId,
      feedback_note: note,
      saved_feedback: true,
    },
  }], { retryKey });
  if (!res || !(res.accepted >= 1)) {
    const reason = res?.rejected?.[0]?.reason;
    throw new Error(reason === "not_initialized"
      ? "画像尚未就绪，暂时无法记录反馈。"
      : "反馈未被接受，请稍后重试。");
  }
  return res;
}

async function handleSavedCardFeedback(item, feedbackType, clicked, other) {
  if (clicked.disabled || other.disabled) return;
  const previousPressed = [clicked, other].map((button) => (
    button.getAttribute("aria-pressed")
  ));
  clicked.setAttribute("aria-pressed", "true");
  other.setAttribute("aria-pressed", "false");
  clicked.disabled = true;
  other.disabled = true;
  setHint(
    feedbackType === "like" ? "正在记录喜欢…" : "正在记录不感兴趣…",
    "info",
  );
  try {
    await postSavedFeedback(item, feedbackType);
    setHint(
      feedbackType === "like" ? "记下了，这类多来点。" : "记下了，这类先少来点。",
      "success",
    );
  } catch (error) {
    [clicked, other].forEach((button, index) => {
      const pressed = previousPressed[index];
      if (pressed === null) button.removeAttribute("aria-pressed");
      else button.setAttribute("aria-pressed", pressed);
    });
    setHint(error?.message || "反馈提交失败，请稍后重试。", "error");
  } finally {
    clicked.disabled = false;
    other.disabled = false;
  }
}

function buildSavedCard(listKind, item, { list, empty, toggles }) {
  if (savedTaskRuntimes[listKind].submissions.has(item.item_key)
    || savedTaskRuntimes[listKind].coordinator.owns(item.item_key)) {
    item = { ...item, sync_status: "syncing" };
  }
  const card = document.createElement("article");
  card.className = "saved-card";
  card.dataset.itemKey = item.item_key;

  const body = document.createElement("button");
  body.type = "button";
  body.className = "saved-card-open";
  body.dataset.savedAction = "open";
  const media = buildSavedCardMedia(item);
  const copy = document.createElement("span");
  copy.className = "saved-card-copy";
  const title = document.createElement("p");
  title.className = "saved-card-title";
  title.textContent = item.title || item.content_id;
  const platform = document.createElement("span");
  platform.className = "saved-card-platform";
  platform.dataset.source = item.source_platform || "bilibili";
  platform.textContent = platformDisplayName(item.source_platform || "bilibili");
  const up = document.createElement("p");
  up.className = "saved-card-up";
  up.textContent = item.author_name || item.up_name || "";
  const syncLine = document.createElement("span");
  syncLine.className = "saved-sync-line";
  const presentation = getSavedSyncPresentation(
    item.sync_status,
    item.error_code,
    item.resolved_target,
    item.error_message,
    item.sync_task_id,
  );
  const chip = document.createElement("span");
  chip.className = "saved-sync-chip";
  chip.dataset.tone = presentation.tone;
  chip.textContent = presentation.label;
  const target = document.createElement("span");
  target.className = "saved-sync-target";
  target.textContent = savedSyncDetail(item);
  syncLine.append(chip, target);
  copy.append(platform, title, up, syncLine);
  body.append(copy);
  body.prepend(media);
  body.addEventListener("click", () => {
    const url = buildContentUrl(item);
    if (url) window.open(url, "_blank");
  });

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "saved-card-remove";
  remove.dataset.savedAction = "remove";
  remove.textContent = "移除";
  remove.title = listKind === "watch_later" ? "移出本地稍后再看" : "从本地收藏移除";
  const onRemoved = listKind === "watch_later" ? loadWatchLater : loadFavorites;
  bindSavedCardRemove(card, remove, {
    listKind,
    itemKey: item.item_key,
    requestRemove: (itemKey) => removeSavedItem(listKind, itemKey),
    toggles,
    list,
    empty,
    onRemoved,
  });

  const actions = document.createElement("span");
  actions.className = "saved-card-actions";
  if (presentation.actionable || presentation.busy) {
    const sync = document.createElement("button");
    sync.type = "button";
    sync.className = "saved-card-sync";
    sync.dataset.savedAction = "sync";
    sync.textContent = presentation.actionLabel;
    sync.disabled = presentation.busy;
    sync.setAttribute("aria-disabled", String(presentation.busy));
    sync.setAttribute("aria-label", presentation.busy ? `${presentation.label}，请稍候` : presentation.actionLabel);
    if (presentation.actionable) {
      sync.addEventListener("click", () => runSavedSync(
        listKind,
        [item],
        sync,
        listKind === "watch_later" ? elements.watchLaterSyncStatus : elements.favoritesSyncStatus,
        listKind === "watch_later" ? loadWatchLater : loadFavorites,
      ));
    }
    actions.append(sync);
  }
  actions.append(remove);

  const feedbackActions = document.createElement("div");
  feedbackActions.className = "saved-card-feedback";
  feedbackActions.setAttribute("aria-label", "反馈与保存操作");

  const like = document.createElement("button");
  like.type = "button";
  like.className = "feedback-icon-btn";
  like.dataset.savedAction = "like";
  like.setAttribute("aria-label", "喜欢");
  like.title = "喜欢";
  like.setAttribute("aria-pressed", "false");
  like.innerHTML = THUMBS_UP_ICON_SVG;

  const dislike = document.createElement("button");
  dislike.type = "button";
  dislike.className = "feedback-icon-btn";
  dislike.dataset.savedAction = "dislike";
  dislike.setAttribute("aria-label", "不感兴趣");
  dislike.title = "不感兴趣";
  dislike.setAttribute("aria-pressed", "false");
  dislike.innerHTML = THUMBS_DOWN_ICON_SVG;

  like.addEventListener("click", () => {
    void handleSavedCardFeedback(item, "like", like, dislike);
  });
  dislike.addEventListener("click", () => {
    void handleSavedCardFeedback(item, "dislike", dislike, like);
  });

  const comment = document.createElement("button");
  comment.type = "button";
  comment.className = "feedback-icon-btn";
  comment.dataset.savedAction = "comment";
  comment.setAttribute("aria-label", "聊一聊");
  comment.title = "聊一聊";
  comment.innerHTML = MESSAGE_ICON_SVG;
  comment.addEventListener("click", async () => {
    const draft = window.prompt("想围绕这条聊什么？");
    if (draft === null) return;
    const note = draft.trim();
    if (!note) {
      setHint("先写一句想聊的内容，再提交这条反馈。", "warning");
      return;
    }
    comment.disabled = true;
    setHint("正在提交聊天线索…", "info");
    try {
      await postSavedFeedback(item, "comment", note);
      setHint("已提交聊天线索。", "success");
    } catch (error) {
      setHint(error?.message || "反馈提交失败，请稍后重试。", "error");
    } finally {
      comment.disabled = false;
    }
  });

  // The card already belongs to listKind (managed by 移除), so show only the
  // other list's toggle: watch_later → 收藏; favorite → 稍后再看.
  const crossIsFavorite = listKind === "watch_later";
  const toggleCross = () => {
    if (crossIsFavorite) return toggleSavedWithFeedback("收藏", item, favoriteToggles, toggleFavoriteSaved);
    return toggleSavedWithFeedback("稍后再看", item, watchLaterToggles, toggleWatchLaterSaved);
  };
  const crossToggle = createActionButton(
    "",
    `feedback-icon-btn saved-toggle cross-toggle ${crossIsFavorite ? "favorite-btn" : "watch-later-btn"}`,
    toggleCross,
  );
  crossToggle.dataset.savedAction = crossIsFavorite ? "favorite" : "watch-later";
  crossToggle.innerHTML = crossIsFavorite ? FAVORITE_ICON_SVG : WATCH_LATER_ICON_SVG;
  if (crossIsFavorite) {
    bindFavoriteToggle(crossToggle, item);
  } else {
    bindWatchLaterToggle(crossToggle, item);
  }

  feedbackActions.append(like, dislike, comment, crossToggle);
  card.append(body, actions, feedbackActions);
  return card;
}

function buildSavedCardMedia(item) {
  const media = document.createElement("span");
  media.className = "saved-card-cover";
  let fallbackShown = false;
  const showFallback = () => {
    if (fallbackShown) return;
    fallbackShown = true;
    media.classList.add("is-fallback");
    media.innerHTML = HISTORY_IMAGE_ICON_SVG;
  };
  if (item.cover_url) {
    const image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    image.addEventListener("error", showFallback, { once: true });
    media.append(image);
    void setProxyImageSrc(image, item.cover_url)
      .then((loaded) => {
        if (!loaded || (image.complete && image.naturalWidth === 0)) showFallback();
      })
      .catch(showFallback);
  } else {
    showFallback();
  }
  return media;
}

async function loadFavorites() {
  return loadSavedList("favorite", {
    list: elements.favoritesList,
    empty: elements.favoritesEmpty,
    syncAll: elements.favoritesSyncAll,
    status: elements.favoritesSyncStatus,
    toggles: favoriteToggles,
  });
}

function historyTextElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = String(text || "");
  return element;
}

function contentHistoryEventLabel(item, category) {
  if (category === "clicked") return "点开";
  if (category === "shown") return "出现";
  return {
    watch_later: "从稍后再看移除",
    favorite: "从收藏移除",
    dismiss: "已忽略",
    dislike: "不感兴趣",
  }[item.context] || "已移除";
}

function contentHistoryRemovedContexts(item) {
  const contexts = Array.isArray(item?.contexts) ? item.contexts : [];
  if (contexts.length) return contexts.filter((entry) => entry && typeof entry.context === "string");
  if (!item?.context) return [];
  item.contexts = [{
    context: item.context,
    occurred_at: item.occurred_at,
    restored: item.restored === true,
    restoring: item.restoring === true,
  }];
  return item.contexts;
}

function contentHistoryRestoreLabel(context) {
  return context === "favorite" ? "重新收藏" : "重新加入稍后";
}

function contentHistoryTime(value) {
  const text = String(value || "").trim();
  if (!text) return "时间未知";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(" ", "T")}Z`
    : text;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function contentHistoryUrl(item) {
  return String(item.content_url || buildContentUrl({
    ...item,
    bvid: item.content_id,
  }) || "").trim();
}

function openContentHistoryItem(item, category) {
  const url = contentHistoryUrl(item);
  if (!url) return;
  const clickReport = reportRecommendationClick({
    recommendation_id: item.recommendation_id,
    bvid: item.content_id,
    content_id: item.content_id,
    content_url: url,
    source_platform: item.source_platform,
    title: item.title,
    up_name: item.author_name,
  });
  if (category === "shown") {
    void clickReport.then((reported) => {
      if (reported) return refreshContentHistory(true);
      return undefined;
    });
  }
  window.open(url, "_blank");
}

function buildContentHistoryCard(item, category) {
  const card = document.createElement("article");
  card.className = "history-card";
  card.dataset.historyItemKey = String(item.item_key || "");

  const open = document.createElement("button");
  open.type = "button";
  open.className = "history-card-open";
  const titleText = String(item.title || item.body_text || "这条内容暂时没有标题").trim();
  open.setAttribute("aria-label", `打开：${titleText}`);
  open.disabled = !contentHistoryUrl(item);
  open.addEventListener("click", () => openContentHistoryItem(item, category));

  const media = document.createElement("span");
  media.className = "history-card-media";
  if (item.cover_url) {
    const image = document.createElement("img");
    image.alt = `${titleText} 的封面`;
    image.setAttribute("loading", "lazy");
    image.setAttribute("fetchpriority", "low");
    image.decoding = "async";
    image.addEventListener("error", () => {
      media.innerHTML = HISTORY_IMAGE_ICON_SVG;
    }, { once: true });
    media.append(image);
    void setProxyImageSrc(image, item.cover_url);
  } else {
    media.innerHTML = HISTORY_IMAGE_ICON_SVG;
  }

  const copy = document.createElement("span");
  copy.className = "history-card-copy";
  copy.append(
    historyTextElement("strong", "history-card-title", titleText),
    historyTextElement("span", "history-card-author", item.author_name || platformDisplayName(item.source_platform)),
  );
  const meta = document.createElement("span");
  meta.className = "history-card-meta";
  const contexts = category === "removed" ? contentHistoryRemovedContexts(item) : [];
  meta.append(
    historyTextElement("span", "", category === "removed" ? `${contexts.length || 1} 项记录` : contentHistoryEventLabel(item, category)),
    historyTextElement("time", "", contentHistoryTime(item.occurred_at)),
  );
  copy.append(meta);
  open.append(media, copy);
  card.append(open);

  if (contexts.length) {
    const contextList = document.createElement("div");
    contextList.className = "history-contexts";
    contextList.setAttribute("aria-label", "移除原因");
    for (const context of contexts) {
      const row = document.createElement("div");
      row.className = "history-context-row";
      const contextCopy = document.createElement("span");
      contextCopy.className = "history-context-copy";
      contextCopy.append(
        historyTextElement("span", "", contentHistoryEventLabel(context, "removed")),
        historyTextElement("time", "", contentHistoryTime(context.occurred_at)),
      );
      row.append(contextCopy);
      if (["watch_later", "favorite"].includes(context.context)) {
        const restore = document.createElement("button");
        restore.type = "button";
        restore.className = "history-restore";
        restore.disabled = context.restored === true;
        if (context.restoring === true) restore.setAttribute("aria-disabled", "true");
        restore.dataset.historyContext = context.context;
        restore.innerHTML = HISTORY_RESTORE_ICON_SVG;
        restore.append(document.createTextNode(
          context.restoring ? "恢复中…" : context.restored ? "已恢复" : contentHistoryRestoreLabel(context.context),
        ));
        restore.addEventListener("click", async () => {
          if (context.restoring || context.restored) return;
          const focusToken = contentHistoryFocusToken({
            category: "removed",
            itemKey: String(item.item_key || ""),
            context: context.context,
          });
          context.restoring = true;
          renderContentHistory();
          restoreContentHistoryFocus(focusToken);
          let restored = false;
          try {
            await saveItem(context.context, item);
            context.restored = true;
            restored = true;
            if (item.context === context.context) item.restored = true;
            if (context.context === "favorite") favoriteToggles.setSaved(item.item_key, true);
            else watchLaterToggles.setSaved(item.item_key, true);
            setHint(context.context === "favorite" ? "已重新收藏。" : "已重新加入稍后再看。", "success");
          } catch (error) {
            setHint(error?.message || "恢复失败，请稍后重试。", "error");
          } finally {
            context.restoring = false;
            renderContentHistory();
            restoreContentHistoryFocus(focusToken, { preferAction: !restored });
          }
        });
        row.append(restore);
      }
      contextList.append(row);
    }
    card.append(contextList);
  }
  return card;
}

function buildContentHistorySection(section) {
  const page = contentHistoryState[section.category];
  const container = document.createElement("section");
  container.className = "history-section";
  container.dataset.historyCategory = section.category;
  const heading = document.createElement("div");
  heading.className = "history-section-head";
  const title = document.createElement("div");
  const titleHeading = historyTextElement("h3", "", section.title);
  titleHeading.tabIndex = -1;
  title.append(
    historyTextElement("p", "view-kicker", section.eyebrow),
    titleHeading,
  );
  heading.append(
    title,
    historyTextElement("span", "history-count", page.loading && !page.items.length ? "读取中" : `${page.total} 条`),
  );
  container.append(
    heading,
    historyTextElement("p", "history-description", section.description),
  );

  if (page.error && !page.items.length) {
    const empty = historyTextElement("div", "history-empty", page.error);
    const retry = historyTextElement("button", "history-more", "重试");
    retry.type = "button";
    retry.dataset.historyRetry = section.category;
    retry.addEventListener("click", () => void loadContentHistoryCategory(
      section.category,
      false,
      contentHistoryGeneration,
      contentHistoryFocusToken({ category: section.category, action: "retry" }),
    ));
    empty.append(retry);
    container.append(empty);
  } else if (page.loading && !page.items.length) {
    const loading = historyTextElement("div", "history-empty", "正在整理这段历史…");
    loading.setAttribute("role", "status");
    container.append(loading);
  } else if (!page.items.length) {
    container.append(historyTextElement("div", "history-empty", "近 30 天还没有这类记录。"));
  } else {
    const list = document.createElement("div");
    list.className = "history-list";
    page.items.forEach((item) => list.append(buildContentHistoryCard(item, section.category)));
    container.append(list);
  }

  if (page.items.length && (page.error || page.notice)) {
    const message = historyTextElement(
      "p",
      `history-page-message ${page.error ? "is-error" : "is-notice"}`,
      page.error || page.notice,
    );
    message.setAttribute("role", page.error ? "alert" : "status");
    container.append(message);
  }

  const refreshingExisting = page.loading && page.items.length > 0;
  if (
    refreshingExisting
    || page.refreshRequired
    || (page.error && page.items.length)
    || page.hasMore
  ) {
    const label = refreshingExisting
      ? "刷新中…"
      : page.loadingMore
      ? "加载中…"
      : page.refreshRequired
        ? "重试刷新列表"
        : page.error
          ? "重试加载更多"
          : "加载更多";
    const more = historyTextElement("button", "history-more", label);
    more.type = "button";
    more.dataset[page.refreshRequired || refreshingExisting ? "historyRetry" : "historyMore"] = section.category;
    if (page.loading || page.loadingMore) more.setAttribute("aria-disabled", "true");
    more.addEventListener("click", () => void loadContentHistoryCategory(
      section.category,
      !page.refreshRequired && !refreshingExisting,
      contentHistoryGeneration,
      contentHistoryFocusToken({
        category: section.category,
        action: page.refreshRequired || refreshingExisting ? "retry" : "more",
      }),
    ));
    container.append(more);
  }
  return container;
}

function renderContentHistory() {
  if (!(elements.historySections instanceof HTMLElement)) return;
  elements.historySections.replaceChildren(...CONTENT_HISTORY_SECTIONS.map(buildContentHistorySection));
}

function contentHistoryFocusToken(token) {
  return {
    ...token,
    scrollTop: Number(elements.content?.scrollTop) || 0,
  };
}

function restoreContentHistoryFocus(token, { preferAction = true } = {}) {
  if (!(elements.historySections instanceof HTMLElement) || !token) return;
  const section = [...elements.historySections.querySelectorAll("[data-history-category]")]
    .find((entry) => entry.dataset.historyCategory === token.category);
  if (!section) return;
  const card = token.itemKey
    ? [...section.querySelectorAll("[data-history-item-key]")]
      .find((entry) => entry.dataset.historyItemKey === token.itemKey)
    : null;
  let target = null;
  if (card && preferAction && token.context) {
    target = [...card.querySelectorAll("[data-history-context]")].find((button) => (
      button.dataset.historyContext === token.context && !button.disabled
    ));
  }
  if (card && !target) {
    target = card.querySelector("[data-history-context]:not(:disabled):not([aria-disabled='true'])")
      || card.querySelector(".history-card-open:not(:disabled)");
  }
  if (!target && token.action) {
    target = section.querySelector(`[data-history-${token.action}]`)
      || section.querySelector("[data-history-more], [data-history-retry]");
  }
  target ||= section.querySelector("h3[tabindex='-1']");
  if (elements.content instanceof HTMLElement) elements.content.scrollTop = token.scrollTop;
  target?.focus({ preventScroll: true });
  if (elements.content instanceof HTMLElement) elements.content.scrollTop = token.scrollTop;
}

async function loadContentHistoryCategory(category, append, generation = contentHistoryGeneration, focusToken = null) {
  const page = contentHistoryState[category];
  if (!page || page.loading || page.loadingMore) return;
  if (append) page.loadingMore = true;
  else page.loading = true;
  page.error = "";
  page.notice = "";
  page.refreshRequired = false;
  renderContentHistory();
  restoreContentHistoryFocus(focusToken);
  try {
    const payload = await fetchContentHistory(
      category,
      CONTENT_HISTORY_PAGE_SIZE,
      append ? page.nextCursor : "",
    );
    if (generation !== contentHistoryGeneration) return;
    const reconciled = reconcileContentHistoryPage({
      items: page.items,
      incomingItems: payload.items,
      incomingTotal: payload.total,
      nextCursor: payload.next_cursor,
      hasMore: payload.has_more,
      append,
    });
    page.items = reconciled.items;
    page.total = reconciled.total;
    page.nextCursor = reconciled.nextCursor;
    page.hasMore = reconciled.hasMore;
  } catch (error) {
    if (generation !== contentHistoryGeneration) return;
    page.error = error?.message || "历史记录加载失败，请稍后重试。";
  } finally {
    if (generation !== contentHistoryGeneration) return;
    page.loading = false;
    page.loadingMore = false;
    renderContentHistory();
    restoreContentHistoryFocus(focusToken);
  }
}

async function refreshContentHistory(force = false) {
  if (!force && contentHistoryLoadedAt && Date.now() - contentHistoryLoadedAt < 5_000) return;
  contentHistoryGeneration += 1;
  const generation = contentHistoryGeneration;
  contentHistoryLoadedAt = Date.now();
  Object.values(contentHistoryState).forEach((page) => {
    page.items = [];
    page.total = 0;
    page.nextCursor = "";
    page.hasMore = false;
    page.loading = false;
    page.loadingMore = false;
    page.error = "";
    page.notice = "";
    page.refreshRequired = false;
  });
  await Promise.allSettled(CONTENT_HISTORY_SECTIONS.map(({ category }) => (
    loadContentHistoryCategory(category, false, generation)
  )));
}

function bindContentHistory() {
  if (elements.historyRefresh instanceof HTMLButtonElement) {
    elements.historyRefresh.addEventListener("click", () => void refreshContentHistory(true));
  }
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
  // Only the degraded branch re-shows the action button after this reset.
  if (elements.emptyAction instanceof HTMLElement) {
    elements.emptyAction.hidden = true;
  }
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

function _setInitCancelButton(visible, enabled = visible) {
  if (!(elements.initCancelBtn instanceof HTMLButtonElement)) {
    return;
  }
  elements.initCancelBtn.hidden = !visible;
  elements.initCancelBtn.disabled = !enabled;
  if (!elements.initCancelBtn.dataset.bound) {
    elements.initCancelBtn.dataset.bound = "1";
    elements.initCancelBtn.addEventListener("click", () => {
      void handleCancelInitClick();
    });
  }
}

function _setInitReason(text, assertive = true) {
  if (elements.initStartReason instanceof HTMLElement) {
    elements.initStartReason.textContent = text || "";
    elements.initStartReason.hidden = !text;
    elements.initStartReason.setAttribute("role", text && assertive ? "alert" : "status");
    elements.initStartReason.setAttribute(
      "aria-live",
      text && assertive ? "assertive" : "polite",
    );
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
    if (row.key === "embedding" && !row.ok) {
      const pull = row.pull || {};
      const repair = row.repair || {};
      if (pull.active) {
        const wrap = document.createElement("div");
        wrap.className = "init-embed-pull";
        const bar = document.createElement("div");
        bar.className = "init-embed-pull-bar";
        const fill = document.createElement("div");
        fill.className = "init-embed-pull-fill";
        fill.style.width = `${Math.max(1, Math.min(99, Number(pull.pct) || 1))}%`;
        bar.append(fill);
        wrap.append(bar);
        if (pull.label) {
          const label = document.createElement("p");
          label.className = "init-embed-pull-label";
          label.textContent = pull.label;
          wrap.append(label);
        }
        li.append(wrap);
      } else if (repair.repairable) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "init-repair-btn";
        btn.textContent = repair.label || "修复向量模型";
        btn.addEventListener("click", () =>
          void _handleChecklistEmbeddingRepair(btn, selected),
        );
        li.append(btn);
      }
    }
    elements.initChecklist.append(li);
  }
}

// Start server-side embedding repair and keep the checklist synchronized with
// the existing init-status progress fields until repair settles.
async function _handleChecklistEmbeddingRepair(btn, selected = null) {
  if (!(btn instanceof HTMLButtonElement)) return;
  btn.disabled = true;
  btn.textContent = "修复中…";
  try {
    const kicked = await startEmbeddingRepair();
    if (!embeddingRepairStartAccepted(kicked)) {
      btn.disabled = false;
      btn.textContent = "重试";
      const detail =
        kicked && kicked.status === 403
          ? "只能在本机操作向量模型修复。"
          : kicked && kicked.status === 404
            ? "当前后端版本不支持向量模型修复，请先升级后端。"
            : kicked && kicked.detail
              ? kicked.detail
              : "向量模型修复未能开始，请稍后重试。";
      setHint(detail, "error");
      return;
    }
  } catch {
    btn.disabled = false;
    btn.textContent = "重试";
    setHint("向量模型修复请求失败，请稍后重试。", "error");
    return;
  }
  for (let i = 0; i < EMBEDDING_REPAIR_POLL_LIMIT; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, EMBEDDING_REPAIR_POLL_MS));
    let status;
    try {
      status = await fetchInitStatus();
    } catch {
      continue;
    }
    if (!status) continue;
    _renderInitChecklist(status, selected);
    const prereq = status.prerequisites || {};
    if (prereq.embedding_ready) return;
    const stillPulling =
      Boolean(prereq.embedding_repair_running) ||
      prereq.embedding_check === "repairing";
    if (!stillPulling && i > 1) return;
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
  const llmConcurrencyRow = document.createElement("label");
  llmConcurrencyRow.className = "init-source-row";
  const llmConcurrencyLabel = document.createElement("span");
  llmConcurrencyLabel.textContent = "初始化 LLM 并发（1-16，默认 3；越小越不容易限流）";
  const llmConcurrencyInput = document.createElement("input");
  llmConcurrencyInput.id = "initLlmConcurrency";
  llmConcurrencyInput.type = "number";
  llmConcurrencyInput.min = "1";
  llmConcurrencyInput.max = "16";
  llmConcurrencyInput.step = "1";
  llmConcurrencyInput.inputMode = "numeric";
  llmConcurrencyInput.value = String(state.initLlmConcurrency);
  llmConcurrencyInput.addEventListener("input", () => {
    const value = Number(llmConcurrencyInput.value);
    state.initLlmConcurrency = Number.isFinite(value) && value >= 1 && value <= 16 ? value : 3;
  });
  llmConcurrencyRow.append(llmConcurrencyLabel, llmConcurrencyInput);
  elements.initSources.append(llmConcurrencyRow);
  const bangumiRow = document.createElement("label");
  bangumiRow.className = "init-source-row";
  const bangumiLabel = document.createElement("span");
  bangumiLabel.textContent = "Bangumi 公开用户名（可留空，仅启用发现）";
  const bangumiInput = document.createElement("input");
  bangumiInput.id = "initBangumiUsername";
  bangumiInput.maxLength = 128;
  bangumiInput.autocomplete = "off";
  bangumiInput.disabled = true;
  bangumiInput.value = state.initBangumiUsername;
  bangumiInput.addEventListener("input", () => {
    state.initBangumiUsername = bangumiInput.value;
    state.initBangumiUsernameTouched = true;
  });
  bangumiRow.append(bangumiLabel, bangumiInput);
  elements.initSources.append(bangumiRow);

  // Optional personal access token: identifies the account via /v0/me and reads
  // private collections. When set, the username above is auto-resolved.
  const bangumiTokenRow = document.createElement("label");
  bangumiTokenRow.className = "init-source-row";
  const bangumiTokenLabel = document.createElement("span");
  bangumiTokenLabel.textContent = "Bangumi 个人令牌（可留空，推荐：自动识别当前用户，可读私密收藏）";
  const bangumiTokenInput = document.createElement("input");
  bangumiTokenInput.id = "initBangumiToken";
  bangumiTokenInput.type = "password";
  bangumiTokenInput.maxLength = 512;
  bangumiTokenInput.autocomplete = "off";
  bangumiTokenInput.disabled = true;
  bangumiTokenInput.value = state.initBangumiToken;
  bangumiTokenInput.addEventListener("input", () => {
    state.initBangumiToken = bangumiTokenInput.value;
  });
  bangumiTokenRow.append(bangumiTokenLabel, bangumiTokenInput);
  elements.initSources.append(bangumiTokenRow);
  const bangumiTokenHint = document.createElement("p");
  bangumiTokenHint.className = "init-sources-hint";
  // Spell out the three-way choice: the backend accepts a token, an explicit
  // public username, OR the account the extension reads off a logged-in
  // bgm.tv page. Leaving both fields empty is a valid path, and users who
  // aren't told that assume Bangumi needs a token they haven't got.
  const bangumiTokenLink = document.createElement("a");
  bangumiTokenLink.href = "https://next.bgm.tv/demo/access-token";
  bangumiTokenLink.target = "_blank";
  bangumiTokenLink.rel = "noopener noreferrer";
  bangumiTokenLink.textContent = "生成个人令牌";
  const bangumiTokenDocLink = document.createElement("a");
  bangumiTokenDocLink.href =
    "https://github.com/whiteguo233/OpenBiliClaw/blob/main/docs/modules/bangumi.md#获取-bangumi-个人令牌";
  bangumiTokenDocLink.target = "_blank";
  bangumiTokenDocLink.rel = "noopener noreferrer";
  bangumiTokenDocLink.textContent = "取令牌步骤";
  bangumiTokenHint.append(
    document.createTextNode(
      "Bangumi 账号三选一，填哪个都行：个人令牌最完整（自动认出你，还能读到私密收藏）；" +
        "公开用户名次之（只能读公开收藏）；都留空也行——只要你浏览器里登录着 bgm.tv，" +
        "扩展会自动识别账号（只拿到账号名，可能未经校验）。",
    ),
    bangumiTokenLink,
    document.createTextNode("（约 1 年有效，视同密码保管）·"),
    bangumiTokenDocLink,
  );
  elements.initSources.append(bangumiTokenHint);

  elements.initSources.querySelector('input[data-init-source="bangumi"]')?.addEventListener("change", (event) => {
    const checked = Boolean(event.currentTarget.checked);
    bangumiInput.disabled = !checked;
    bangumiTokenInput.disabled = !checked;
  });
  const hint = document.createElement("p");
  hint.className = "init-sources-hint";
  hint.textContent = INIT_SOURCE_LOGIN_HINT.replace(
    "Bangumi 使用公开 API，无需登录。",
    "Bangumi 与 Linux.do 的公开发现无需登录；Linux.do 浏览器登录可增强个人信号。",
  );
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

function _readInitBangumiUsername() {
  state.initBangumiUsername = String(
    document.getElementById("initBangumiUsername")?.value || "",
  ).trim();
  return state.initBangumiUsername;
}

function _readInitBangumiToken() {
  state.initBangumiToken = String(
    document.getElementById("initBangumiToken")?.value || "",
  ).trim();
  return state.initBangumiToken;
}

function _readInitLlmConcurrency() {
  const input = document.getElementById("initLlmConcurrency");
  const value = Number(input ? input.value : state.initLlmConcurrency);
  state.initLlmConcurrency = Number.isFinite(value) && value >= 1 && value <= 16 ? value : 4;
  return state.initLlmConcurrency;
}

// Decide what Bangumi username (if any) guided init should send, delegating the
// omit-vs-clear rule to the shared pure helper. Returns the trimmed value to
// send, or null to omit it so the backend keeps the configured username.
function _resolveInitBangumiUsernameForSubmit(value) {
  return resolveInitBangumiUsername({
    touched: state.initBangumiUsernameTouched,
    prefilled: state.initBangumiUsernamePrefilled,
    value,
  });
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
    // Expectation management: total time is highly variable, so orient the
    // user about that variability instead of quoting a duration.
    const expectation = document.createElement("li");
    expectation.className = "init-hint-row";
    expectation.textContent = INIT_EXPECTATION_HINT;
    elements.initChecklist.append(expectation);
  }
  if (elements.initProgress instanceof HTMLElement) {
    elements.initProgress.hidden = true;
  }
  if (elements.initStallHint instanceof HTMLElement) {
    elements.initStallHint.hidden = true;
    elements.initStallHint.classList.remove("stale");
  }
  _setInitStartButton("开始初始化", true);
  _setInitCancelButton(false);
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
  // The one reassurance a waiting user needs, said once: after v0.3.180 a run
  // that keeps producing results is literally never interrupted.
  if (elements.initChecklist instanceof HTMLElement && progress.active) {
    const patience = document.createElement("li");
    patience.className = "init-hint-row";
    patience.textContent = INIT_RUNNING_HINT;
    elements.initChecklist.append(patience);
  }
  if (elements.initProgress instanceof HTMLElement) {
    elements.initProgress.hidden = false;
    if (elements.initProgressBar instanceof HTMLElement) {
      elements.initProgressBar.style.width = progress.indeterminate ? "100%" : `${progress.pct}%`;
      elements.initProgressBar.classList.toggle("indeterminate", progress.indeterminate);
    }
    if (elements.initProgressLabel instanceof HTMLElement) {
      elements.initProgressLabel.textContent = progress.failed
        ? `初始化未完成：${describeInitFailure(status, progress)}`
        : progress.partial
          ? `部分完成：${describeInitStatusReason(status) || "初始化部分完成；已采数据已保留并使用，请按提示稍后补齐。你现在可以先进入应用。"}`
        : progress.active
          ? progress.indeterminate
            ? progress.stageLabel || "正在初始化"
            : `${progress.stageLabel || "正在初始化"}（${progress.pct}%）`
          : "初始化完成！";
      elements.initProgressLabel.setAttribute("role", progress.failed ? "alert" : "status");
      elements.initProgressLabel.setAttribute(
        "aria-live",
        progress.failed ? "assertive" : "polite",
      );
    }
  }
  // Liveness line under the bar: "● 进行中 (+ observed elapsed / counts)" while
  // the backend keeps writing; amber stall copy after >90s of silence.
  if (elements.initStallHint instanceof HTMLElement) {
    if (progress.active) {
      const staleness = stalenessView(status);
      const text = staleness.fresh
        ? [staleness.text, progress.stageDetailText].filter(Boolean).join(" · ")
        : staleness.text;
      elements.initStallHint.textContent = text;
      elements.initStallHint.classList.toggle("stale", !staleness.fresh);
      elements.initStallHint.hidden = !text;
    } else {
      elements.initStallHint.hidden = true;
      elements.initStallHint.classList.remove("stale");
    }
  }
  if (progress.active) {
    _setInitStartButton("初始化进行中…", false);
    _setInitCancelButton(true);
    _setInitReason("");
  } else if (progress.failed) {
    _setInitStartButton("重试初始化", true);
    _setInitCancelButton(false);
    _setInitReason("");
  } else if (progress.partial) {
    _setInitStartButton("画像已生成", false);
    _setInitCancelButton(false);
    _setInitReason(describeInitStatusReason(status), false);
  } else {
    _setInitStartButton("已初始化", false);
    _setInitCancelButton(false);
    _setInitReason("");
  }
}

// Poll init-status while a run is in progress; on terminal, reload (success) or
// leave the failure reason on screen with the button re-enabled for a retry.
async function pollInitProgress() {
  let status = null;
  try {
    status = await fetchInitStatus();
  } catch (error) {
    _setInitReason(
      `暂时无法连接初始化后台：${error?.message || "正在重试"}。已保留当前进度。`,
      false,
    );
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
    setHint(
      status.partial_success
        ? describeInitStatusReason(status) ||
          "初始化部分完成；已采数据已保留并使用，请按提示稍后补齐。你现在可以先进入应用。"
        : "初始化完成！正在加载画像和推荐…",
      status.partial_success ? "warning" : "success",
    );
    scheduleRecommendationsRefresh();
    void loadProfileSummary({ force: true });
  }
}

async function handleCancelInitClick() {
  if (elements.initCancelBtn instanceof HTMLButtonElement) {
    elements.initCancelBtn.disabled = true;
    elements.initCancelBtn.textContent = "取消中…";
  }
  try {
    await cancelInit();
    _setInitReason("已发送取消请求，正在安全结束当前步骤…", false);
    clearInitPolling();
    initPollTimer = setTimeout(() => void pollInitProgress(), 300);
  } catch (error) {
    if (error?.status === 409) {
      clearInitPolling();
      initPollTimer = setTimeout(() => void pollInitProgress(), 300);
    } else {
      _setInitReason(error?.details?.detail || error?.message || "取消请求失败。");
    }
  } finally {
    if (elements.initCancelBtn instanceof HTMLButtonElement) {
      elements.initCancelBtn.textContent = "取消";
      elements.initCancelBtn.disabled = false;
    }
  }
}

function renderEmbeddingPullStatus(status) {
  renderInitPanelIdle();
  _renderInitChecklist(status, _readSelectedInitSources());
}

async function pollEmbeddingPullProgress() {
  let status;
  try {
    status = await fetchInitStatus();
  } catch {
    clearInitPolling();
    initPollTimer = setTimeout(() => void pollEmbeddingPullProgress(), 3000);
    return;
  }
  if (status?.running || status?.initialized) {
    renderInitProgress(status);
    if (status.running) {
      _startInitProgressPoll();
    } else {
      clearInitPolling();
    }
    return;
  }
  renderEmbeddingPullStatus(status);
  if (shouldAttachEmbeddingPullProgress(status)) {
    clearInitPolling();
    initPollTimer = setTimeout(() => void pollEmbeddingPullProgress(), 3000);
  } else {
    clearInitPolling();
  }
}

// Boot-time re-attach: when the popup opens while a run is already live, the
// uninitialized branch would otherwise paint the idle panel and never poll
// (the run started elsewhere, so no click/SSE kicked the poll here). The same
// applies to a packaged desktop's background bge-m3 pull: it is live work,
// but it has no guided-init run id or SSE event of its own.
async function maybeAttachRunningInitProgress() {
  let status;
  try {
    status = await fetchInitStatus();
  } catch {
    return false;
  }
  if (!shouldAttachRunningInitProgress(status)) {
    if (shouldAttachEmbeddingPullProgress(status)) {
      renderEmbeddingPullStatus(status);
      clearInitPolling();
      initPollTimer = setTimeout(() => void pollEmbeddingPullProgress(), 1200);
      return true;
    }
    return false;
  }
  renderInitProgress(status);
  _startInitProgressPoll();
  return true;
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
  const bangumiUsername = _readInitBangumiUsername();
  const bangumiUsernameOption = _resolveInitBangumiUsernameForSubmit(bangumiUsername);
  const bangumiToken = _readInitBangumiToken();
  // Only send a token when the user typed one; omit otherwise so the backend
  // keeps any configured token (empty string would clear a stored token).
  const bangumiTokenOption = bangumiToken ? bangumiToken : null;
  if (selectedSources.length === 0) {
    _setInitStartButton("开始初始化", true);
    _setInitReason("至少勾选一个数据来源。");
    return;
  }
  // No client-side Bangumi-only admission check here on purpose. The backend
  // owns a THREE-tier account ladder (token → explicit username →
  // browser-extension-reported identity); a local "username or token required"
  // copy of it can't see the third tier and silently blocked zero-config
  // extension users from ever reaching /api/init. The backend answers 409
  // no_profile_signal_sources when all three are genuinely missing, and the
  // startInit catch below renders it via describeInitStartError.
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

  // A background bge-m3 pull is not a guided-init run. Keep the CTA idle and
  // attach the checklist poll instead of treating the pull as a failed init.
  if (shouldAttachEmbeddingPullProgress(status)) {
    renderEmbeddingPullStatus(status);
    clearInitPolling();
    initPollTimer = setTimeout(() => void pollEmbeddingPullProgress(), 1200);
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
      describeInitStatusReason(status) || "以下条件未满足，无法开始初始化，补齐后再点一次。",
    );
    return;
  }

  // All conditions pass → start with the chosen sources. The backend
  // re-validates in its critical section, so a race can still 409 — surface
  // that and let the user retry.
  let startResult;
  try {
    startResult = await startInit({
      force: false,
      sources: selectedSources,
      bangumiUsername: bangumiUsernameOption,
      bangumiToken: bangumiTokenOption,
      llmConcurrency: _readInitLlmConcurrency(),
    });
  } catch (error) {
    _renderInitChecklist(status, selectedSources);
    _setInitStartButton("开始初始化", true);
    _setInitReason(describeInitStartError(error));
    return;
  }
  // The 202 response may carry backend warnings (e.g. Bangumi selected without a
  // public username → discovery-only). Surface them instead of the generic
  // "已开始" note so the user knows the run is proceeding with a caveat.
  const startWarnings = Array.isArray(startResult?.warnings)
    ? startResult.warnings.filter((text) => typeof text === "string" && text.trim())
    : [];
  setHint(
    startWarnings.length ? startWarnings.join(" ") : "初始化已开始，正在拉取数据…",
    "info",
  );
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

function runtimeEventCarriesPoolCounts(event) {
  return (
    event?.type === "refresh.pool_updated" ||
    typeof event?.pool_available_count === "number" ||
    typeof event?.pool_pending_count === "number" ||
    typeof event?.pool_raw_count === "number"
  );
}

function renderReadyRecommendationHint() {
  if (
    state.activeTab !== "recommend" ||
    !(elements.viewRecommend instanceof HTMLElement) ||
    elements.viewRecommend.hidden ||
    !Array.isArray(state.recommendations) ||
    state.recommendations.length === 0
  ) {
    return;
  }
  const hint = getReadyRecommendationHint(state.runtimeStatus);
  setHint(hint.message, hint.tone);
}

function rememberDismissedDelight(bvid) {
  if (!bvid) {
    return Promise.resolve();
  }
  // A user-driven × means "handled / already seen", not merely "hide this
  // popup instance". The dismiss response writes both delight_notified and
  // the canonical seen ledger. A failed write stays visible for retry.
  return respondToDelight(bvid, "dismiss").then((result) => {
    if (!state.dismissedDelightBvids.includes(bvid)) {
      state.dismissedDelightBvids = [...state.dismissedDelightBvids, bvid];
    }
    return result;
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

function probeActionDescriptors(type) {
  return isAvoidanceProbeType(type)
    ? [
        { action: "confirm", label: "确认避雷", className: "is-confirm" },
        { action: "defer", label: "搁置避雷", className: "is-neutral" },
        { action: "reject", label: "不是雷点", className: "is-reject" },
        { action: "chat", label: "多聊聊", className: "is-chat" },
      ]
    : [
        { action: "confirm", label: "确认喜欢", className: "is-confirm" },
        { action: "defer", label: "暂时搁置", className: "is-neutral" },
        { action: "reject", label: "确认不喜欢", className: "is-reject" },
        { action: "chat", label: "多聊聊", className: "is-chat" },
      ];
}

function probeResponseMessage(type, responseType, domain) {
  const isAvoidance = isAvoidanceProbeType(type);
  if (responseType === "defer") {
    return isAvoidance
      ? `好，「${domain}」先搁置，过阵子再确认是不是雷点。`
      : `好，「${domain}」先搁置，过阵子再问。`;
  }
  if (responseType === "confirm") {
    return isAvoidance
      ? `好，「${domain}」会作为避雷方向处理。`
      : `好，「${domain}」记住了。`;
  }
  return isAvoidance
    ? `好，「${domain}」不记成避雷。`
    : `好，「${domain}」会作为不喜欢处理。`;
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
      if (runtimeEventCarriesPoolCounts(event)) {
        renderReadyRecommendationHint();
      }
      if (event.type === "delight.candidate" && event.bvid) {
        mergeIncomingDelight(event);
      }
      state.activeFeedbackProgress?.handle?.(event);
      if (elements.footer instanceof HTMLElement) {
        elements.footer.dataset.tone = getHintBannerState(getRuntimeEventTone(event)).tone;
      }
      renderActivityCard();
      scheduleDialogueConfirmationRefresh();
      // Hot-reload: re-fetch all data when backend config is reloaded
      if (event.type === "config_reloaded") {
        setHint("后端配置已热重载，正在刷新数据…", "success");
        scheduleRecommendationsRefresh();
      }
      if (event.type === "config_reload_failed") {
        const message = String(event.message || "后台应用配置失败，已恢复上一次生效配置。");
        setHint(message, "error");
        showToast(message, "error");
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
      if (event.type === "delight.liked") {
        const data = event.data || event;
        const bvid = String(data.bvid || data.domain || event.bvid || event.domain || "");
        const index = state.activeDelights.findIndex((item) => item?.bvid === bvid);
        if (index >= 0) {
          state.activeDelights[index] = {
            ...state.activeDelights[index],
            state: "liked",
            response_message: String(data.message || event.message || "好，这类多来点。"),
          };
          syncDelightHead();
          renderDelightSlot();
        }
      }
      // Live guided-init progress (gui-init F1): drive the recommend-tab
      // progress bar from the run's stage events.
      if (event.type === "init_progress" || event.type === "init_failed") {
        void pollInitProgress();
      }
      // Init completed: re-fetch everything including profile
      if (event.type === "init_completed") {
        state.profileLoaded = false;
        setHint(
          event.partial_success
            ? String(
                event.detail ||
                  "初始化部分完成；已采数据已保留并使用，请按提示稍后补齐。你现在可以先进入应用。",
              )
            : "初始化完成！正在加载画像和推荐…",
          event.partial_success ? "warning" : "success",
        );
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
      const wasOnline = state.online;
      const { reconnected } = backendConnectionCoordinator.markStreamConnected();
      if (!wasOnline || reconnected) {
        setHint(
          reconnected && wasOnline ? "实时连接已恢复，正在刷新。" : "后端连上了，正在刷新。",
          "success",
        );
        scheduleRecommendationsRefresh({ delayMs: 0 });
        scheduleDialogueConfirmationRefresh();
      }
    },
    onDisconnect() {
      void backendConnectionCoordinator.markStreamDisconnected().then((result) => {
        if (!result.applied) return;
        if (result.reachable) {
          setHint("实时连接正在恢复，后端功能仍可用。");
          return;
        }
        setHint("后端连接断了，等重连上会自动恢复。", "error");
      });
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
      for (const { action: responseType, label, className } of probeActionDescriptors(
        probeType,
      ).filter(({ action }) => action !== "chat")) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `probe-btn ${className}`;
        button.textContent = label;
        button.setAttribute("aria-label", label);
        button.title = label;
        button.dataset.responseType = responseType;
        button.addEventListener("click", () =>
          handleSpecResponse(item.domain, responseType, row, probeType),
        );
        actions.append(button);
      }
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
      msg.textContent = probeResponseMessage(type, responseType, domain);
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
  for (const descriptor of probeActionDescriptors("interest.probe")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `probe-btn ${descriptor.className}`;
    button.textContent = descriptor.label;
    button.setAttribute("aria-label", descriptor.label);
    button.title = descriptor.label;
    button.addEventListener("click", () => handleProbeResponse(descriptor.action));
    actions.append(button);
  }
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
      msg.textContent = probeResponseMessage("interest.probe", responseType, domain);
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
  openPopupOverlay(overlay, {
    trigger: elements.messagesButton,
    initialFocus: elements.messagesBack,
  });
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
  closePopupOverlay(overlay);
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

// Fetch + cache the GitHub stargazers count through the local backend. The
// backend owns GitHub ETag/rate-limit handling so the extension never emits a
// failed cross-origin request in DevTools.
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
    const data = await fetchProjectStats();
    const n = data?.github_stars;
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

  // When the configured host is loopback, ask the lightweight QR endpoint
  // for the server's detected LAN IP. Unlike the full readiness endpoint,
  // this endpoint does not wait for embedding readiness before the QR code can be rendered.
  let effectiveEndpoint = endpoint;
  if (isLoopbackMobileHost(endpoint.host)) {
    try {
      const scheme = endpoint.scheme === "https" ? "https" : "http";
      const urlHost = endpoint.host.includes(":") && !endpoint.host.startsWith("[")
        ? `[${endpoint.host}]`
        : endpoint.host;
      const base = `${scheme}://${urlHost}:${endpoint.port}`;
      const resp = await fetch(`${base}/api/qr-info`, { signal: AbortSignal.timeout(2000) });
      if (resp.ok) {
        const data = await resp.json();
        if (data.lan_ip && !isLoopbackMobileHost(data.lan_ip)) {
          effectiveEndpoint = { ...endpoint, host: data.lan_ip };
        }
      }
    } catch {
      // QR-info fetch failed — fall through with original endpoint.
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
  openPopupOverlay(overlay, {
    trigger: elements.mobileQrButton,
    initialFocus: elements.mobileQrBack,
  });
  await renderMobileQrPanel();
}

function closeMobileQrPanel() {
  closePopupOverlay(elements.mobileQrOverlay);
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
  bindPopupOverlayKeyboard(elements.mobileQrOverlay, closeMobileQrPanel);
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

// Single delegated click handler for every message card's action buttons.
// Bound once on the (persistent) container so it survives the frequent
// container.replaceChildren() re-renders that used to orphan per-button
// listeners and silently drop clicks.
function onMessageActionClick(event) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const btn = target.closest("[data-msg-action]");
  if (!(btn instanceof HTMLElement) || btn.disabled) return;
  const card = btn.closest(".message-item");
  if (!(card instanceof HTMLElement)) return;
  const domain = card.dataset.domain || "";
  const type = card.dataset.type || "interest.probe";
  const action = btn.dataset.msgAction;
  if (action === "dismiss") {
    dismissMessage(domain, type);
  } else if (action === "chat") {
    expandInlineChat(card, domain, type);
  } else if (action === "confirm" || action === "defer" || action === "reject") {
    // Guard against a double-click firing the API twice before the card is
    // replaced with its success state; clear on settle so an error path (card
    // kept) can be retried (success replaces the card, so it's moot there).
    if (card.dataset.responding === "1") return;
    card.dataset.responding = "1";
    void handleMessageResponse(domain, action, type).finally(() => {
      delete card.dataset.responding;
    });
  }
}

function renderMessagesList() {
  const container = elements.messagesList;
  if (!(container instanceof HTMLElement)) return;
  if (!container.dataset.actionsDelegated) {
    container.dataset.actionsDelegated = "1";
    container.addEventListener("click", onMessageActionClick);
  }
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
  // Actions are wired via ONE delegated listener on the messages container
  // (see renderMessagesList) rather than per-button, so a background re-render
  // \u2014 chat-turn polling, the post-fetch re-render in openMessagesPanel, or
  // another card's response \u2014 can't orphan the handler and swallow the click
  // (field report 2026-07-06: "\u8FD9\u4E2A\u6309\u94AE\u6709\u65F6\u5019\u6CA1\u53CD\u5E94").
  const dismiss = document.createElement("button");
  dismiss.className = "message-dismiss";
  dismiss.textContent = "\u00D7";
  dismiss.title = "\u5173\u95ED";
  dismiss.dataset.msgAction = "dismiss";
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
    reply.className = "message-chat-reply chat-markdown";
    reply.innerHTML = renderMarkdown(probe.chat_reply);
    item.append(reply);
  }

  const actions = document.createElement("div");
  actions.className = "message-actions";
  for (const descriptor of probeActionDescriptors(type)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `probe-btn ${descriptor.className}`;
    button.textContent = descriptor.label;
    button.setAttribute("aria-label", descriptor.label);
    button.title = descriptor.label;
    button.dataset.msgAction = descriptor.action;
    button.disabled = probe.chat_status === "pending";
    actions.append(button);
  }
  item.append(actions);
  return item;
}

// ── Engagement stats ───────────────────────────────────────────
// Condense a raw count into Chinese-style 万/亿 units. Empty string for
// non-positive values so callers render nothing.
function formatCountCn(n) {
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
function recommendationStats(item) {
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

// Append a muted stats line to `parent` when the item has any positive
// engagement count. No-op (renders nothing) otherwise.
function appendRecommendationStats(parent, item) {
  const text = recommendationStats(item);
  if (!text) return;
  const stats = document.createElement("div");
  stats.className = "recommendation-stats";
  stats.textContent = text;
  parent.append(stats);
}

function appendPublishedTime(parent, item) {
  const text = formatPublishedTime(item);
  if (!text) return;
  const time = document.createElement("span");
  time.className = "recommendation-published-time";
  time.textContent = text;
  if (item.published_at && Number.isFinite(Date.parse(item.published_at))) {
    time.title = new Date(item.published_at).toLocaleString();
  }
  parent.append(time);
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
  dismiss.title = "\u770B\u8FC7\u4E86\uFF0C\u4E0D\u518D\u63A8\u8350";
  dismiss.setAttribute("aria-label", "\u770B\u8FC7\u4E86\uFF0C\u4E0D\u518D\u63A8\u8350");
  dismiss.addEventListener("click", async () => {
    dismiss.disabled = true;
    try {
      await dismissMessageByBvid(delight.bvid);
    } catch {
      dismiss.disabled = false;
      dismiss.title = "操作失败，请重试";
      dismiss.setAttribute("aria-label", "操作失败，请重试");
    }
  });
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

  const platformChip = document.createElement("span");
  platformChip.className = "message-delight-platform";
  platformChip.textContent = platformDisplayName(delight.source_platform || "bilibili");
  textCol.append(platformChip);

  const title = document.createElement("div");
  title.className = "message-delight-title";
  title.textContent = delight.title || "";
  textCol.append(title);
  appendPublishedTime(textCol, delight);

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

  appendRecommendationStats(item, delight);

  if (delight.chat_status === "pending") {
    item.append(createChatThinkingPlaceholder("阿B 正在品你这句话"));
  } else if (delight.chat_reply) {
    const reply = document.createElement("div");
    reply.className = "message-chat-reply chat-markdown";
    reply.innerHTML = renderMarkdown(delight.chat_reply);
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
      await dismissMessageByBvid(delight.bvid, false, false);
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
      const showFailure = (nextTurn) => {
        thinking.remove();
        const errorEl = document.createElement("div");
        errorEl.className = "message-chat-reply";
        errorEl.textContent = nextTurn.error || "刚刚没发出去，换个说法再试试。";
        itemEl.append(errorEl);
        sendBtn.disabled = false;
        if (actions) actions.hidden = false;
        applyTurnToMessage(nextTurn);
        applyTurnToDelight(nextTurn);
      };
      const showReply = (nextTurn) => {
        thinking.remove();
        const replyEl = document.createElement("div");
        replyEl.className = "message-chat-reply chat-markdown";
        replyEl.innerHTML = renderMarkdown(
          nextTurn.reply || "\u6536\u5230\u4E86\uFF0C\u6211\u4F1A\u7EE7\u7EED\u89C2\u5BDF\u3002",
        );
        itemEl.append(replyEl);
        applyTurnToMessage(nextTurn);
        applyTurnToDelight(nextTurn);
      };
      const settleTurn = (nextTurn) => {
        if (nextTurn.status === "failed") {
          showFailure(nextTurn);
          return;
        }
        if (nextTurn.status === "completed") showReply(nextTurn);
      };
      if (turn.status === "completed" || turn.status === "failed") {
        settleTurn(turn);
      } else {
        applyTurnToMessage(turn);
        pollChatTurnUntilSettled(turn.turn_id, {
          onUpdate(nextTurn) {
            if (nextTurn.status === "completed" || nextTurn.status === "failed") {
              settleTurn(nextTurn);
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

async function dismissMessageByBvid(bvid, removeFromDom = true, persist = true) {
  if (persist) await rememberDismissedDelight(bvid);
  state.messages = state.messages.filter((m) => m.bvid !== bvid);
  updateMessageBadge();
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
  const chatArea = input.closest(".message-chat-area");
  if (!chatArea || !input.isConnected || !sendBtn.isConnected) return;
  const isAvoidance = isAvoidanceProbeType(type);

  chatArea.querySelector(".message-chat-reply.is-error")?.remove();
  input.disabled = true;
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

    // Completed turns remove the card after showing the reply. Failed turns
    // restore the handled/retry state and keep the card visible.
    const showFailure = (nextTurn) => {
      forgetHandledProbe(domain, type);
      thinking.remove();
      input.disabled = false;
      sendBtn.disabled = false;
      const errorEl = document.createElement("div");
      errorEl.className = "message-chat-reply is-error";
      errorEl.textContent = nextTurn.error || "刚刚没发出去，换个说法再试试。";
      chatArea.append(errorEl);
      applyTurnToMessage(nextTurn);
      input.focus();
    };

    const showReply = (nextTurn) => {
      thinking.remove();
      chatArea.remove();
      const replyEl = document.createElement("div");
      replyEl.className = "message-chat-reply chat-markdown";
      replyEl.innerHTML = renderMarkdown(
        nextTurn.reply || "\u6536\u5230\u4E86\uFF0C\u6211\u4F1A\u7ED3\u5408\u8FD9\u4E2A\u65B9\u5411\u7EE7\u7EED\u89C2\u5BDF\u3002",
      );
      itemEl.append(replyEl);
      applyTurnToMessage(nextTurn);
      setTimeout(() => {
        removeMessageFromState(domain, type);
        itemEl.remove();
        renderMessagesEmptyIfNeeded();
      }, 4000);
    };

    const settleTurn = (nextTurn) => {
      if (nextTurn.status === "failed") {
        showFailure(nextTurn);
        return;
      }
      if (nextTurn.status === "completed") showReply(nextTurn);
    };

    if (turn.status === "completed" || turn.status === "failed") {
      settleTurn(turn);
    } else {
      applyTurnToMessage(turn);
      pollChatTurnUntilSettled(turn.turn_id, {
        onUpdate(nextTurn) {
          if (nextTurn.status === "completed" || nextTurn.status === "failed") {
            settleTurn(nextTurn);
          }
        },
      });
    }
  } catch (err) {
    console.error("Inline chat failed:", err);
    forgetHandledProbe(domain, type);
    thinking.remove();
    input.disabled = false;
    sendBtn.disabled = false;
    // Show error hint inline
    const errEl = document.createElement("div");
    errEl.className = "message-chat-reply is-error";
    errEl.textContent = "\u540E\u53F0\u6B63\u5FD9\uFF0C\u7B49\u4E00\u4E0B\u518D\u804A\u3002";
    chatArea.append(errEl);
    input.focus();
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
      msg.textContent = probeResponseMessage(type, responseType, domain);
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
  bindPopupOverlayKeyboard(elements.messagesOverlay, closeMessagesPanel);
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

    container.append(row);
  }
  const hint = document.createElement("p");
  hint.className = "insight-readonly-hint";
  hint.textContent = "洞察区只读；请在对话的待聊确认入口继续。";
  container.append(hint);
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

// Placeholders the LLM emits when it has no signal. Treated as absent so the
// panel falls back to its "still observing" copy instead of rendering garbage.
const UNKNOWNISH_TEXT = new Set(["", "unknown", "none", "n/a", "未知"]);

function isUnknownishText(value) {
  return UNKNOWNISH_TEXT.has(String(value ?? "").trim().toLowerCase());
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
  let hasAny = false;
  for (const [label, value] of textFields) {
    if (isUnknownishText(value)) continue;
    hasAny = true;
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
    hasAny = true;
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
  if (!hasAny) {
    const fb = document.createElement("p");
    fb.className = "is-fallback";
    fb.textContent = "内容口味还在摸索中。";
    container.append(fb);
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
    if (isUnknownishText(value)) continue;
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

function makeRemovableChip(label, onRemove, chipClass = "") {
  const chip = document.createElement("span");
  chip.className = "edit-chip";
  if (chipClass) chip.classList.add(chipClass);
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

function editSpecificName(item) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object") return item.name || item.label || "";
  return "";
}

function hasInterestSpecificEdits(field) {
  const edits = field && typeof field === "object" ? field.specific_edits : null;
  if (!edits || typeof edits !== "object") return false;
  return Object.values(edits).some((edit) => {
    if (!edit || typeof edit !== "object") return false;
    return (edit.add?.length || 0) > 0 || (edit.remove?.length || 0) > 0;
  });
}

function renderInterestEditField(path, label, field) {
  const domains = Array.isArray(field.domains) ? field.domains : [];
  const removed = Array.isArray(field.removed_domains) ? field.removed_domains : [];
  const edited =
    removed.length > 0 ||
    domains.some((d) => d && d.user_added) ||
    hasInterestSpecificEdits(field);
  const block = makeEditFieldBlock(label, edited);

  const tree = document.createElement("div");
  tree.className = "edit-interest-tree";
  for (const dom of domains) {
    if (!dom || !dom.domain) continue;
    const name = dom.user_added ? `${dom.domain} ＋` : dom.domain;
    const domain = document.createElement("div");
    domain.className = "edit-interest-domain";
    const head = document.createElement("div");
    head.className = "edit-interest-domain-head";
    head.append(
      makeRemovableChip(
        name,
        () => applyProfileEdit({ target: path, op: "remove", value: dom.domain }),
        "edit-domain-chip",
      ),
    );
    domain.append(head);

    const specificList = document.createElement("div");
    specificList.className = "edit-specific-list";
    const specifics = Array.isArray(dom.specifics)
      ? dom.specifics.map(editSpecificName).filter(Boolean)
      : [];
    for (const specific of specifics) {
      specificList.append(
        makeRemovableChip(
          specific,
          () => applyProfileEdit({ target: path, op: "remove", value: specific, parent: dom.domain }),
          "edit-specific-chip",
        ),
      );
    }
    if (specifics.length === 0) {
      const emptySpecific = document.createElement("p");
      emptySpecific.className = "edit-empty edit-specific-empty";
      emptySpecific.textContent = "还没有二级兴趣";
      specificList.append(emptySpecific);
    }
    domain.append(specificList);

    const specificAddRow = makeAddRow("添加二级兴趣", (value) =>
      applyProfileEdit({ target: path, op: "add", value, parent: dom.domain }),
    );
    specificAddRow.classList.add("edit-specific-add-row");
    domain.append(specificAddRow);
    tree.append(domain);
  }
  if (domains.length === 0) {
    const empty = document.createElement("p");
    empty.className = "edit-empty";
    empty.textContent = "还没有，添加一个吧";
    tree.append(empty);
  }
  block.append(tree);
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

const dialogueTurnsById = new Map();
let dialogueContextSelection = readContextSelection(
  (() => {
    try { return globalThis.localStorage; } catch { return null; }
  })(),
  "extension-popup",
);
let retainedChatDraft = "";

function popupContextStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

function storeDialogueContext(selection) {
  dialogueContextSelection = writeContextSelection(
    popupContextStorage(),
    "extension-popup",
    selection,
  );
  return dialogueContextSelection;
}

async function validateDialogueContext({ announce = false } = {}) {
  const current = normalizeContextPreview(dialogueContextSelection);
  if (!current) return null;
  if (isTerminalCardTurn(dialogueTurnsById.get(current.reply_to_turn_id))) {
    storeDialogueContext(clearContextSelection());
    return null;
  }
  try {
    const preview = normalizeContextPreview(await fetchChatContext(current.reply_to_turn_id));
    if (!preview) throw new Error("invalid_context_preview");
    return storeDialogueContext(preview);
  } catch (error) {
    const code = contextErrorCode(error);
    if (["reply_target_not_found", "reply_target_inactive", "invalid_reply_target"].includes(code)) {
      storeDialogueContext(clearContextSelection());
      if (announce) setHint(contextErrorMessage(error), "error");
    } else if (announce && code === "reply_target_processing") {
      setHint(contextErrorMessage(error), "warning");
    }
    return code === "reply_target_processing" ? current : null;
  }
}

async function selectDialogueContext(turnId, preview = null) {
  const turn = dialogueTurnsById.get(turnId) || { turn_id: turnId };
  const candidate = contextSelectionFromTurn(turn, preview);
  if (candidate) {
    storeDialogueContext(candidate);
    renderStructuredDialogueTurn(turn, { forceBottom: false });
    return candidate;
  }
  try {
    const fetched = normalizeContextPreview(await fetchChatContext(turnId));
    const fetchedCandidate = contextSelectionFromTurn(turn, fetched);
    if (!fetchedCandidate) throw new Error("invalid_context_preview");
    storeDialogueContext(fetchedCandidate);
    renderStructuredDialogueTurn(turn, { forceBottom: false });
    return fetchedCandidate;
  } catch (error) {
    setHint(contextErrorMessage(error), "error");
    return null;
  }
}

function renderPendingConfirmations() {
  const { count, items, expanded } = state.pendingConfirmations;
  const countText = count > 99 ? "99+" : String(Math.max(0, count));
  if (elements.chatPendingCount instanceof HTMLElement) {
    elements.chatPendingCount.textContent = countText;
  }
  if (elements.chatPendingTabCount instanceof HTMLElement) {
    elements.chatPendingTabCount.textContent = countText;
    elements.chatPendingTabCount.hidden = count <= 0;
  }
  if (elements.chatPendingToggle instanceof HTMLButtonElement) {
    elements.chatPendingToggle.setAttribute("aria-expanded", String(expanded));
    elements.chatPendingToggle.classList.toggle("is-expanded", expanded);
  }
  if (elements.chatPendingList instanceof HTMLElement) {
    const previousScrollTop = elements.chatPendingList.scrollTop;
    elements.chatPendingList.hidden = !expanded;
    elements.chatPendingList.innerHTML = renderPendingListMarkup(items);
    elements.chatPendingList.scrollTop = Math.min(
      previousScrollTop,
      Math.max(0, elements.chatPendingList.scrollHeight - elements.chatPendingList.clientHeight),
    );
  }
}

function renderDialogueContextBar() {
  const existing = document.getElementById("chatContextBar");
  const markup = contextBarMarkup(dialogueContextSelection);
  if (!markup) {
    existing?.remove();
    return;
  }
  const bar = existing || document.createElement("div");
  bar.id = "chatContextBar";
  bar.innerHTML = markup;
  if (!existing && elements.chatForm?.parentElement) {
    elements.chatForm.parentElement.insertBefore(bar, elements.chatForm);
  }
  bar.querySelector("[data-context-clear]")?.addEventListener("click", () => {
    storeDialogueContext(clearContextSelection());
    setHint("已清除这条消息的对话上下文。", "info");
    renderDialogueContextBar();
  });
}

async function refreshPendingConfirmations() {
  if (!state.online) {
    state.pendingConfirmations = {
      ...state.pendingConfirmations,
      count: 0,
      items: [],
    };
    renderPendingConfirmations();
    return;
  }
  try {
    const payload = await fetchPendingConfirmations({ session: CHAT_SESSION });
    state.pendingConfirmations = {
      ...state.pendingConfirmations,
      count: Math.max(0, Number(payload?.count) || 0),
      items: Array.isArray(payload?.items) ? payload.items : [],
    };
    renderPendingConfirmations();
  } catch {
    // Keep the last successful list in the open popup; the toolbar badge
    // independently suppresses stale counts when backend health changes.
  }
}

function scheduleDialogueConfirmationRefresh() {
  if (dialogueConfirmationRefreshTimer !== null) {
    window.clearTimeout(dialogueConfirmationRefreshTimer);
  }
  dialogueConfirmationRefreshTimer = window.setTimeout(() => {
    dialogueConfirmationRefreshTimer = null;
    void refreshPendingConfirmations();
    if (state.activeTab === "chat") void hydrateChatHistory();
  }, 300);
}

function isChatMessagesNearBottom(messages = elements.chatMessages) {
  if (!(messages instanceof HTMLElement)) return true;
  return (
    messages.scrollHeight -
      messages.clientHeight -
      messages.scrollTop <=
    40
  );
}

function openChatEvidenceTurnIds() {
  if (!(elements.chatMessages instanceof HTMLElement)) return new Set();
  return new Set(
    Array.from(elements.chatMessages.querySelectorAll(".dialogue-evidence[open]"))
      .map((details) => details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "")
      .filter(Boolean),
  );
}
function renderStructuredDialogueTurn(turn, { forceBottom = false } = {}) {
  if (!(elements.chatMessages instanceof HTMLElement) || !turn?.turn_id) return;
  const shouldStickToBottom = forceBottom || isChatMessagesNearBottom();
  const previousScrollTop = elements.chatMessages.scrollTop;
  const openEvidence = openChatEvidenceTurnIds();
  dialogueTurnsById.set(turn.turn_id, turn);
  const selector = `[data-dialogue-turn-container="${CSS.escape(turn.turn_id)}"]`;
  let container = elements.chatMessages.querySelector(selector);
  if (!(container instanceof HTMLElement)) {
    container = document.createElement("div");
    container.className = "dialogue-turn";
    container.dataset.dialogueTurnContainer = turn.turn_id;
    elements.chatMessages.append(container);
  }
  container.innerHTML = replyQuoteMarkup(turn, [...dialogueTurnsById.values()]) + renderTurnMarkup(turn, { surface: "popup" });
  for (const details of container.querySelectorAll(".dialogue-evidence")) {
    const turnId = details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "";
    if (openEvidence.has(turnId)) details.open = true;
  }
  if (shouldStickToBottom) scrollChatMessagesToBottom();
  else elements.chatMessages.scrollTop = previousScrollTop;
}

function updateDialogueTurn(turn) {
  if (!turn?.turn_id) return;
  dialogueTurnsById.set(turn.turn_id, turn);
  renderStructuredDialogueTurn(turn);
}

function scrollChatMessagesToBottom() {
  if (!(elements.chatMessages instanceof HTMLElement) || suppressChatAutoScroll) {
    return;
  }
  const scroll = () => {
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  };
  scroll();
  window.requestAnimationFrame(scroll);
}

function chatHistorySignature(turns) {
  return JSON.stringify(turns);
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

  const text = document.createElement(role === "助手" ? "div" : "p");
  text.className = `chat-content${role === "助手" ? " chat-markdown" : ""}`;
  if (role === "助手") text.innerHTML = renderMarkdown(content);
  else text.textContent = content;

  item.append(label, text);
  elements.chatMessages.append(item);
  scrollChatMessagesToBottom();
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

  const text = document.createElement("div");
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
  scrollChatMessagesToBottom();
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
    text.classList.add("chat-markdown");
    text.innerHTML = renderMarkdown(content);
  }
  scrollChatMessagesToBottom();
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
  dialogueTurnsById.set(turn.turn_id, turn);
  if (isCardTurn(turn) || isQuestionTurn(turn)) {
    renderStructuredDialogueTurn(turn);
    return;
  }
  let userPart = findChatTurnElement(turn.turn_id, "user");
  if (!userPart) {
    appendChatMessage("你", turn.message || "", {
      turnId: turn.turn_id,
      part: "user",
    });
    userPart = findChatTurnElement(turn.turn_id, "user");
  }
  if (turn.reply_to_turn_id && !elements.chatMessages.querySelector(
    `[data-reply-quote-for="${CSS.escape(turn.turn_id)}"]`,
  )) {
    const quoteHolder = document.createElement("div");
    quoteHolder.innerHTML = replyQuoteMarkup(turn, [...dialogueTurnsById.values()]);
    const quote = quoteHolder.firstElementChild;
    if (quote instanceof HTMLElement) {
      quote.dataset.replyQuoteFor = turn.turn_id;
      elements.chatMessages.insertBefore(quote, userPart || null);
    }
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
    const message = turn.error || "刚刚没发出去，换个说法再试试。";
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
  if (chatHistoryHydrationInFlight) return;
  chatHistoryHydrationInFlight = true;
  const messages = elements.chatMessages;
  const shouldStickToBottom = isChatMessagesNearBottom();
  const previousScrollTop = messages.scrollTop;
  try {
    const payload = await fetchChatTurns({ session: CHAT_SESSION, limit: 100 });
    const nextTurns = selectDialogueTurns(payload.items || []);
    const signature = chatHistorySignature(nextTurns);
    if (signature === lastChatHistorySignature) return;
    lastChatHistorySignature = signature;
    const openEvidence = openChatEvidenceTurnIds();
    suppressChatAutoScroll = true;
    try {
      elements.chatMessages.replaceChildren();
      dialogueTurnsById.clear();
      for (const turn of nextTurns) {
        renderChatTurn(turn);
        if (isDialogueReplyTurn(turn) && (turn.status === "pending" || turn.status === "processing")) {
          pollChatTurnUntilSettled(turn.turn_id, {
            onUpdate: renderChatTurn,
            onDone: refreshAfterChatTurn,
          });
        }
      }
      await validateDialogueContext({ announce: true });
      renderDialogueContextBar();
    } finally {
      suppressChatAutoScroll = false;
    }
    for (const details of elements.chatMessages.querySelectorAll(".dialogue-evidence")) {
      const turnId = details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "";
      if (openEvidence.has(turnId)) details.open = true;
    }
    if (shouldStickToBottom) {
      scrollChatMessagesToBottom();
    } else {
      window.requestAnimationFrame(() => {
        messages.scrollTop = Math.min(
          previousScrollTop,
          Math.max(0, messages.scrollHeight - messages.clientHeight),
        );
      });
    }
  } catch {
    // History is opportunistic; core panel loading should continue offline.
  } finally {
    chatHistoryHydrationInFlight = false;
  }
}

function startChatHistorySync() {
  if (chatHistoryRefreshTimer !== null) return;
  chatHistoryRefreshTimer = window.setInterval(() => {
    if (state.activeTab !== "chat" || document.hidden || !state.online) return;
    void hydrateChatHistory();
    void refreshPendingConfirmations();
  }, CHAT_HISTORY_REFRESH_INTERVAL_MS);
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
  const row = document.createElement("div");
  row.className = "delight-banner-row";
  const toggleExpanded = () => {
    updateDelightHead({ expanded: !isExpanded });
    renderDelightSlot();
  };
  row.addEventListener("click", toggleExpanded);

  // Thumbnail (left)
  const thumb = document.createElement("span");
  thumb.className = "delight-banner-thumb";
  const renderTextThumb = () => {
    thumb.replaceChildren();
    thumb.classList.add("is-fallback", "is-text-card");
    const excerpt = document.createElement("span");
    excerpt.className = "delight-banner-thumb-text";
    excerpt.textContent = delight.body_text || delight.title || "一条文字推荐";
    thumb.append(excerpt);
  };
  if (delight.cover_url) {
    const image = document.createElement("img");
    void setProxyImageSrc(image, delight.cover_url);
    image.alt = "";
    image.addEventListener("error", () => {
      image.remove();
      renderTextThumb();
    });
    thumb.append(image);
  } else {
    renderTextThumb();
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
  const platformChip = document.createElement("span");
  platformChip.className = "delight-banner-platform";
  platformChip.textContent = platformDisplayName(delight.source_platform || "bilibili");
  kickerLine.append(platformChip);
  appendPublishedTime(kickerLine, delight);
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

  const chevron = document.createElement("button");
  chevron.type = "button";
  chevron.className = "delight-banner-chevron";
  chevron.setAttribute("aria-label", isExpanded ? "收起惊喜推荐" : "展开惊喜推荐");
  chevron.setAttribute("aria-expanded", isExpanded ? "true" : "false");
  chevron.textContent = isExpanded ? "▾" : "▸";
  chevron.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleExpanded();
  });

  row.append(thumb, textCol, chevron);

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "delight-banner-dismiss";
  dismiss.title = "看过了，不再推荐";
  dismiss.setAttribute("aria-label", "看过了，不再推荐");
  dismiss.textContent = "×";
  dismiss.addEventListener("click", async (event) => {
    event.stopPropagation();
    dismiss.disabled = true;
    try {
      await rememberDismissedDelight(delight.bvid);
      shiftDelightQueue();
      setHint(
        state.activeDelights.length > 0
          ? "已标为看过，下一条上。"
          : "已标为看过，不会再推荐。",
        "success",
      );
      renderDelightSlot();
    } catch {
      dismiss.disabled = false;
      setHint("这次还没记上，请再试一次。", "error");
    }
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

    if (uiState.show_status) {
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
          aiBubble.className = "delight-turn-bubble is-assistant chat-markdown";
          aiBubble.innerHTML = renderMarkdown(t.reply || "");
        }
        bubbleArea.append(aiBubble);
      }
      body.append(bubbleArea);
    } else if (delight.chat_reply) {
      // Fallback: show single chat_reply for backward compat
      const reply = document.createElement("p");
      reply.className = "delight-banner-chat-reply chat-markdown";
      reply.innerHTML = renderMarkdown(delight.chat_reply);
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
          setHint("这次喜欢还没记上，可以再试一次。", "error");
          renderDelightSlot();
          return;
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
    likeButton.setAttribute("aria-pressed", uiState.like_pressed ? "true" : "false");
    likeButton.disabled = uiState.like_disabled;

    const rejectButton = createActionButton(
      "不感兴趣",
      "action-button action-secondary delight-banner-action",
      async () => {
        try {
          await respondToDelight(delight.bvid, "dislike", delight.title);
        } catch (err) {
          console.error("Delight dislike failed:", err);
          setHint("这次还没记上，请再试一次。", "error");
          renderDelightSlot();
          return;
        }
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
        await toggleSavedWithFeedback("稍后再看", delight, watchLaterToggles, toggleWatchLaterSaved);
      });
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
      bindWatchLaterToggle(btn, delight);
      return btn;
    })();

    // \u6536\u85CF = \u661F\u661F\u56FE\u6807\uFF0C\u4E0E\u7A0D\u540E\u518D\u770B\u76F8\u4E92\u72EC\u7ACB
    const delightFavoriteButton = (() => {
      const btn = createActionButton("", "action-button action-secondary delight-banner-action delight-save-toggle favorite-btn", async () => {
        await toggleSavedWithFeedback("收藏", delight, favoriteToggles, toggleFavoriteSaved);
      });
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';
      bindFavoriteToggle(btn, delight);
      return btn;
    })();

    actions.append(
      openButton,
      likeButton,
      delightWatchLaterButton,
      delightFavoriteButton,
      rejectButton,
      chatButton,
    );
    if (uiState.show_actions) {
      body.append(actions);
    }

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
      dismissAll.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (dismissAll.disabled) return;
        dismissAll.disabled = true;
        dismissAll.textContent = "本地保存中…";
        const snapshot = state.activeDelights.map((item) => normalizePopupSavedItem(item));
        const results = await Promise.allSettled(
          snapshot.map((item) => saveItem("watch_later", item)),
        );
        const partition = partitionSavedQueueResults(snapshot, results);
        let syncing = 0;
        partition.saved.forEach(({ item, itemKey, value }) => {
          if (itemKey) {
            watchLaterToggles.setSaved(itemKey, true);
          }
          if (value?.sync_task_id && ["pending", "syncing"].includes(value?.sync_status)) {
            syncing += 1;
            savedTaskRuntimes.watch_later.coordinator.track({
              task_id: value.sync_task_id,
              items: [{ item_key: itemKey, status: value.sync_status }],
            }, [itemKey], {
              onTerminal: () => { void loadWatchLater(); },
            });
          }
          void rememberDismissedDelight(item.bvid || item.content_id).catch(() => {
            // The saved item remains available in watch later; a failed delight
            // acknowledgement can be retried if it is surfaced again.
          });
        });
        const saved = partition.savedCount;
        const failed = partition.failedCount;
        state.activeDelights = partition.remaining;
        syncDelightHead();
        setHint(`本地保存 ${saved} · 同步中 ${syncing} · 失败 ${failed}`, failed ? "warning" : "success");
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
      card.classList.add("is-text-only");
      preview.classList.add("is-text-only");
      cover.classList.add("is-text-card");
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
      { bilibili: "B 站", xiaohongshu: "小红书", douyin: "抖音", weibo: "微博", youtube: "YouTube", twitter: "X", zhihu: "知乎", reddit: "Reddit", bangumi: "Bangumi", linuxdo: "Linux.do", v2ex: "V2EX" }[
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
    metaLine.textContent = formatRecommendationAuthorLine(item);
    appendPublishedTime(metaLine, item);

    content.append(top, copyBlock, metaLine);
    appendRecommendationStats(content, item);
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
          await toggleSavedWithFeedback("稍后再看", item, watchLaterToggles, toggleWatchLaterSaved);
        });
        btn.innerHTML = WATCH_LATER_ICON_SVG;
        btn.classList.add("saved-toggle", "watch-later-btn");
        bindWatchLaterToggle(btn, item);
        return btn;
      })(),
      (() => {
        const btn = createActionButton("", "action-button action-secondary", async () => {
          await toggleSavedWithFeedback("收藏", item, favoriteToggles, toggleFavoriteSaved);
        });
        btn.innerHTML = FAVORITE_ICON_SVG;
        btn.classList.add("saved-toggle", "favorite-btn");
        bindFavoriteToggle(btn, item);
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

  if (stateShape.kind === "degraded") {
    showRecommendationEmptyState("AI 服务配置需要修复", stateShape.message);
    if (elements.emptyAction instanceof HTMLElement) {
      elements.emptyAction.textContent = "去设置修复 →";
      elements.emptyAction.hidden = false;
    }
    setHint("AI 服务配置有误：修好 LLM 配置并保存后即可恢复。", "error");
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
      stateShape.degraded
        ? "先修好 AI 服务配置（下方检查项会说明原因）；保存成功后即可点「开始初始化」。"
        : "点「开始初始化」，会先检查前置条件，再依次保存完整画像并基于它生成首轮可用推荐。",
    );
    if (stateShape.degraded && elements.emptyAction instanceof HTMLElement) {
      // Keep the one-click config repair entry alongside the init journey —
      // the checklist explains the blocker, this button opens the fix.
      elements.emptyAction.textContent = "去设置修复 →";
      elements.emptyAction.hidden = false;
    }
    setHint(
      stateShape.degraded
        ? "AI 服务配置有误：修好 LLM 配置并保存后即可开始初始化。"
        : "先完成初始化，把画像和候选池攒起来。",
      stateShape.degraded ? "error" : "info",
    );
    renderInitPanelIdle();
    // If a run is already live (started elsewhere / page reopened mid-init),
    // take over with the progress view + poll instead of a dead idle panel.
    void maybeAttachRunningInitProgress();
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
  if (online) {
    backendConnectionCoordinator.markHttpReachable();
  } else {
    backendConnectionCoordinator.markOffline();
  }

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
  // The banner is gated on the runtime snapshot (initialized + not degraded);
  // the boot-time check usually races ahead of this fetch, so re-evaluate now
  // that the snapshot is in.
  void maybeShowEmbeddingBanner();
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
    state.runtimeStatus = await fetchRuntimeStatus().catch(() => state.runtimeStatus);
    renderPoolStatus(state.runtimeStatus);
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
    const excludedBvids = state.recommendations.map((item) => item?.bvid).filter(Boolean);
    const result = await reshuffleRecommendations(excludedBvids);
    if (!Array.isArray(result.items)) {
      setHint("还没初始化好。去「推荐」页点「开始初始化」，完成后再刷新。", "error");
      return;
    }
    const replacement = reconcileRecommendationReplacement(
      state.recommendations,
      result.items,
    );
    resetRecommendationAutoLoadIntent();
    state.recommendations = replacement.items;
    state.loadingMore = false;
    state.hasMoreRecommendations = replacement.preserved
      ? false
      : result.items.length >= 10;
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
      preservedCurrent: replacement.preserved,
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
    [elements.tabLibrary, "library"],
    [elements.tabProfile, "profile"],
    [elements.tabChat, "chat"],
  ];

  bindings.forEach(([button, tabName], index) => {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.addEventListener("click", () => setActiveTab(tabName));
    button.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % bindings.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + bindings.length) % bindings.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = bindings.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      setActiveTab(bindings[nextIndex][1]);
      bindings[nextIndex][0]?.focus();
    });
  });

  const libraryBindings = [
    [elements.tabWatchLater, "watchLater"],
    [elements.tabFavorites, "favorites"],
    [elements.tabHistory, "history"],
  ];
  libraryBindings.forEach(([button, tabName], index) => {
    if (!(button instanceof HTMLButtonElement)) return;
    button.addEventListener("click", () => setActiveLibraryTab(tabName, { forceLoad: true }));
    button.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % libraryBindings.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + libraryBindings.length) % libraryBindings.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = libraryBindings.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      setActiveLibraryTab(libraryBindings[nextIndex][1], { focus: true });
    });
  });
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

async function handleDialogueCardAction(button) {
  const card = button.closest(".dialogue-card");
  const turnId = card?.dataset.dialogueTurnId || "";
  const action = button.dataset.cardAction || "";
  const turn = dialogueTurnsById.get(turnId);
  if (!turn || !action || button.disabled) return;
  button.disabled = true;
  try {
    const { response } = await executeCardAction(turn, action, {
      request(_path, body) {
        return actOnChatCard(turnId, body.action, {
          signal: dialogueCardActionAbortController.signal,
        });
      },
      fetchTurn(id, options) {
        return fetchChatTurn(id, options);
      },
      signal: dialogueCardActionAbortController.signal,
      onUpdate: updateDialogueTurn,
    });
    if (response?.outcome === "retryable_error") {
      const reason = String(response?.reason || "").toLowerCase();
      if (reason === "stale_anchor" || reason === "anchor_dependency_failed") {
        setHint("这条暂时结算不了：你正在聊另一条，先把那条聊完或结束再试。", "error");
      } else {
        setHint("后端结果暂未同步；可刷新确认，或直接重试这次操作。", "error");
      }
      return;
    }
    if (action === "discuss") {
      await selectDialogueContext(turnId, response?.context_preview || null);
    } else if (dialogueContextSelection?.reply_to_turn_id === turnId) {
      storeDialogueContext(clearContextSelection());
      renderDialogueContextBar();
    }
    if (response?.outcome === "already_settled") {
      setHint("这条已在另一个窗口结算，已同步最终状态。", "success");
    } else if (action === "discuss") {
      setHint("好，沿着这条猜测继续聊。", "success");
      elements.chatInput?.focus();
    } else if (action === "defer") {
      setHint("先放一放，之后再聊。", "success");
    } else {
      setHint(
        response?.state === "revised"
          ? "已按你的修正记下这条。"
          : action === "confirm"
            ? "已确认这条猜测。"
            : "已记下这条猜测不准。",
        "success",
      );
    }
    await Promise.all([hydrateChatHistory(), refreshPendingConfirmations()]);
  } catch {
    setHint("这次没有结算成功，卡片已恢复，可以重试。", "error");
  }
}

async function handlePendingConfirmationOpen(button) {
  const ref = button.dataset.confirmationRef || "";
  if (!ref || button.disabled) return;
  button.disabled = true;
  button.textContent = "打开中…";
  try {
    const turn = await executePendingConfirmationOpen(ref, {
      session: CHAT_SESSION,
      signal: dialogueCardActionAbortController.signal,
      request(_path, body, { signal } = {}) {
        return openPendingConfirmation(ref, { session: body.session, signal });
      },
      onWaiting({ message }) {
        button.textContent = "等待中…";
        setHint(`${message}，空闲后会自动打开。`);
      },
    });
    if (turn?.turn_id) {
      renderChatTurn(turn);
      await selectDialogueContext(turn.turn_id);
    }
    await Promise.all([hydrateChatHistory(), refreshPendingConfirmations()]);
    setHint(
      isQuestionTurn(turn) ? "这条疑惑已经放进对话里。" : "这张确认卡已经放进对话里。",
      "success",
    );
    elements.chatInput?.focus();
  } catch (error) {
    button.disabled = false;
    button.textContent = "打开";
    if (Number(error?.status) === 409) {
      await refreshPendingConfirmations();
      setHint("另一条疑惑正在聊，待聊列表已经同步。", "warning");
    } else if (error?.name !== "AbortError") {
      const detail = String(error?.details?.detail?.message || "").trim();
      setHint(detail || "这条待聊内容暂时打不开，请稍后重试。", "error");
    }
  }
}

function bindDialogueConfirmations() {
  if (elements.chatPendingToggle instanceof HTMLButtonElement) {
    elements.chatPendingToggle.addEventListener("click", () => {
      state.pendingConfirmations.expanded = !state.pendingConfirmations.expanded;
      renderPendingConfirmations();
      if (state.pendingConfirmations.expanded) void refreshPendingConfirmations();
    });
  }
  if (elements.chatPendingList instanceof HTMLElement) {
    elements.chatPendingList.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-confirmation-ref]")
        : null;
      if (button instanceof HTMLButtonElement) void handlePendingConfirmationOpen(button);
    });
  }
  if (elements.chatMessages instanceof HTMLElement) {
    elements.chatMessages.addEventListener("click", (event) => {
      activateReplyQuote(event, elements.chatMessages);
      const button = event.target instanceof Element
        ? event.target.closest("[data-card-action]")
        : null;
      if (button instanceof HTMLButtonElement) void handleDialogueCardAction(button);
    });
  }
  renderPendingConfirmations();
  renderDialogueContextBar();
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
    const replyToTurnId = dialogueContextSelection?.reply_to_turn_id || "";
    retainedChatDraft = "";
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
        replyToTurnId,
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
    } catch (error) {
      clearSlowStatusTimer();
      retainedChatDraft = message;
      elements.chatInput.value = message;
      if (thinkingPlaceholder) {
        replaceChatThinkingPlaceholder(thinkingPlaceholder, "刚刚没发出去，换个说法再试试。");
      } else {
        appendChatMessage("助手", "刚刚没发出去，换个说法再试试。", {
          turnId,
          part: "assistant",
        });
      }
      setChatStatus(contextErrorMessage(error), "error");
      setHint(contextErrorMessage(error), "error");
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
  const backendSchemeInput = document.getElementById("cfgBackendScheme");
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

  const extLogin = initExtLogin(
    { deviceKey: document.getElementById("cfgExtDeviceKey"),
      btn: document.getElementById("cfgExtLoginBtn"),
      status: document.getElementById("cfgExtLoginStatus") },
    { getBaseUrl: getBackendBaseUrl, onPaired: connectRuntimeStream }
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
    ["advanced", document.getElementById("settingsTabAdvanced")],
    ["general", document.getElementById("settingsTabGeneral")],
    ["logging", document.getElementById("settingsTabLogging")],
  ];

  function setActiveSettingsPanel(activePanel = "models") {
    for (const [name, tab] of settingsTabs) {
      const isActive = name === activePanel;
      if (tab instanceof HTMLButtonElement) {
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
        tab.tabIndex = isActive ? 0 : -1;
      }
      const panel = overlay.querySelector(`[data-settings-panel="${name}"]`);
      if (panel instanceof HTMLElement) {
        panel.hidden = !isActive;
        panel.setAttribute("aria-hidden", isActive ? "false" : "true");
      }
    }
    if (activePanel === "logging") startDiagAlertFeed();
    else stopDiagAlertFeed();
  }

  // ── 异常报警（LLM / Embedding 请求失败等异常事件）───
  const DIAG_ALERT_POLL_MS = 15000;
  let diagAlertPollTimer = null;
  let diagAlertsLoading = false;

  function describeDiagAlertCode(code, category) {
    const llmCodes = {
      rate_limited: "限流 429",
      auth_failed: "鉴权失败",
      timeout: "请求超时",
      bad_response: "响应异常",
      provider_error: "请求失败",
      all_providers_failed: "全部实例失败",
    };
    const embeddingCodes = {
      breaker_open: "熔断触发",
      provider_error: "请求失败",
    };
    const table = category === "embedding" ? embeddingCodes : llmCodes;
    return table[code] || code || "未知异常";
  }

  function formatDiagAlertTime(epochSeconds) {
    const ts = Number(epochSeconds || 0) * 1000;
    if (!Number.isFinite(ts) || ts <= 0) return "";
    try {
      return new Date(ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return "";
    }
  }

  function renderDiagAlerts(payload) {
    const listEl = document.getElementById("cfgDiagAlertList");
    const emptyEl = document.getElementById("cfgDiagAlertsEmpty");
    const summaryEl = document.getElementById("cfgDiagAlertSummary");
    if (!(listEl instanceof HTMLElement) || !(emptyEl instanceof HTMLElement)) return;
    const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
    if (summaryEl instanceof HTMLElement) {
      const errors = Number(payload?.summary?.errors || 0);
      const warnings = Number(payload?.summary?.warnings || 0);
      summaryEl.textContent =
        errors + warnings > 0
          ? `${alerts.length} 条记录 · ${errors} 错误 / ${warnings} 警告`
          : "";
    }
    if (!alerts.length) {
      listEl.hidden = true;
      listEl.replaceChildren();
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    listEl.hidden = false;
    listEl.replaceChildren(
      ...alerts.map((alert) => {
        const severity = alert?.severity === "error" ? "error" : "warning";
        const categoryLabel = alert?.category === "embedding" ? "Embedding" : "LLM";
        const source = String(alert?.source || "").trim();
        const count = Number(alert?.count || 1);
        const timeLabel = formatDiagAlertTime(alert?.last_seen);
        const item = document.createElement("li");
        item.className = "diag-alert-item";
        item.dataset.severity = severity;

        const top = document.createElement("div");
        top.className = "diag-alert-item-top";
        const badge = document.createElement("span");
        badge.className = "diag-alert-badge";
        badge.textContent = severity === "error" ? "错误" : "警告";
        const sourceSpan = document.createElement("span");
        sourceSpan.className = "diag-alert-source";
        sourceSpan.textContent = source ? `${categoryLabel} · ${source}` : categoryLabel;
        top.append(badge, sourceSpan);

        const message = document.createElement("div");
        message.className = "diag-alert-message";
        message.textContent = String(alert?.message || "");
        item.append(top, message);

        const metaLabel = [
          describeDiagAlertCode(alert?.code, alert?.category),
          count > 1 ? `×${count}` : "",
          timeLabel,
        ]
          .filter(Boolean)
          .join(" · ");
        if (metaLabel) {
          const meta = document.createElement("div");
          meta.className = "diag-alert-meta";
          meta.textContent = metaLabel;
          item.append(meta);
        }
        return item;
      }),
    );
  }

  async function refreshDiagAlerts() {
    if (diagAlertsLoading) return;
    diagAlertsLoading = true;
    try {
      const payload = await fetchDiagnosticsAlerts({ limit: 50 });
      if (payload) renderDiagAlerts(payload);
    } catch {
      // 辅助信息：拉取失败保持现状即可，不打扰用户。
    } finally {
      diagAlertsLoading = false;
    }
  }

  function startDiagAlertFeed() {
    void refreshDiagAlerts();
    if (diagAlertPollTimer !== null) return;
    diagAlertPollTimer = setInterval(() => {
      if (document.hidden) return;
      void refreshDiagAlerts();
    }, DIAG_ALERT_POLL_MS);
  }

  function stopDiagAlertFeed() {
    if (diagAlertPollTimer === null) return;
    clearInterval(diagAlertPollTimer);
    diagAlertPollTimer = null;
  }

  const refreshDiagAlertsBtn = document.getElementById("cfgRefreshDiagAlerts");
  if (refreshDiagAlertsBtn instanceof HTMLButtonElement) {
    refreshDiagAlertsBtn.addEventListener("click", () => {
      void refreshDiagAlerts();
    });
  }

  for (const [name, tab] of settingsTabs) {
    if (tab instanceof HTMLButtonElement) {
      tab.addEventListener("click", () => setActiveSettingsPanel(name));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const tabs = settingsTabs
          .map(([, candidate]) => candidate)
          .filter((candidate) => candidate instanceof HTMLButtonElement);
        const currentIndex = tabs.indexOf(tab);
        if (currentIndex < 0 || !tabs.length) return;
        event.preventDefault();
        const nextIndex = event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        const nextTab = tabs[nextIndex];
        setActiveSettingsPanel(nextTab.dataset.settingsTab || settingsTabs[nextIndex][0]);
        nextTab.focus();
      });
    }
  }

  async function populateBackendEndpoint() {
    try {
      const endpoint = await getBackendEndpointConfig();
      if (backendSchemeInput instanceof HTMLSelectElement) {
        backendSchemeInput.value = endpoint.scheme || "http";
      }
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

  const BACKEND_UPDATE_REASON_TEXT = {
    dirty_worktree: "代码目录有未提交改动，更新被阻止",
    unsupported_install_mode: "当前安装方式不支持自动更新",
    docker_install_mode: "Docker 安装通过拉取新镜像升级，无法就地自更新",
    untrusted_remote: "git 远端不在允许列表，更新被阻止（可在后端日志查看实际远端地址）",
    branch_not_fast_forwardable: "本地代码与发布版本分叉，无法快进更新",
    merge_or_rebase_in_progress: "代码目录正在合并 / 变基，更新暂缓",
    github_rate_limited: "GitHub API 限流，请稍后再试",
    github_unreachable: "无法访问 GitHub 检查更新",
    missing_target_tag: "远端未找到目标版本标签",
    dependency_sync_failed: "更新后依赖安装失败",
    restart_failed: "更新后重启失败",
    no_backend_tag_yet: "远端暂无后端发布标签",
    prerelease_ignored: "仅有预发布版本，已忽略",
    already_applying: "正在更新中",
  };

  function formatBackendUpdateReason(reason) {
    const key = reason && reason !== "none" ? String(reason) : "";
    if (!key) return "";
    return BACKEND_UPDATE_REASON_TEXT[key] || key;
  }

  function formatBackendUpdateError(backend) {
    const key =
      backend.last_error || (backend.reason && backend.reason !== "none" ? backend.reason : "");
    return formatBackendUpdateReason(key) || "—";
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
    setText("backendUpdateError", formatBackendUpdateError(backend));
    setText("extensionVersionValue", getExtensionVersionLabel());

    const installMode = String(backend.install_mode || "");
    const isGitInstall = installMode === "git";
    const isFrozenInstall = installMode === "frozen";
    const isDockerInstall = installMode === "docker";
    const autoApplyUnsupported = ["frozen", "docker", "unsupported"].includes(installMode);
    const autoUpdateToggle = document.getElementById("cfgAutoUpdate");
    if (autoUpdateToggle instanceof HTMLInputElement) {
      autoUpdateToggle.disabled = autoApplyUnsupported;
    }
    const autoUpdateInterval = document.getElementById("cfgAutoUpdateInterval");
    if (autoUpdateInterval instanceof HTMLInputElement) {
      autoUpdateInterval.disabled = autoApplyUnsupported;
    }
    const isDesktopInstallerUpdate = String(backend.latest_tag || "").startsWith("desktop-v");
    const applyBtn = document.getElementById("backendUpdateApply");
    if (applyBtn instanceof HTMLButtonElement) {
      const canApply =
        isGitInstall &&
        backend.state === "update_available" &&
        Boolean(backend.latest_tag) &&
        !isDesktopInstallerUpdate;
      applyBtn.hidden = !canApply;
      applyBtn.disabled = !canApply;
      applyBtn.dataset.tag = backend.latest_tag || "";
    }
    const downloadLink = document.getElementById("backendUpdateDownload");
    if (downloadLink instanceof HTMLAnchorElement) {
      const showDownload =
        (isFrozenInstall || isDesktopInstallerUpdate) && backend.state === "update_available";
      downloadLink.hidden = !showDownload;
      downloadLink.href =
        showDownload && backend.latest_tag
          ? `https://github.com/whiteguo233/OpenBiliClaw/releases/tag/${encodeURIComponent(String(backend.latest_tag))}`
          : "https://github.com/whiteguo233/OpenBiliClaw/releases";
    }
    // Non-git installs never get the apply button; tell the user how their
    // install actually upgrades instead of leaving the card action-less.
    const modeHint = document.getElementById("backendUpdateModeHint");
    if (modeHint instanceof HTMLElement) {
      let hint = "";
      if (isDockerInstall) {
        hint =
          backend.state === "update_available"
            ? "Docker 安装：发现新版镜像，在部署目录执行 docker compose pull && docker compose up -d 完成升级。"
            : "Docker 安装：升级通过拉取新镜像完成（docker compose pull && docker compose up -d）。";
      } else if (isFrozenInstall) {
        hint = "桌面安装包：发现新版时点击上方链接下载新安装包完成升级。";
      } else if (installMode && !isGitInstall) {
        hint = "当前安装方式不支持自动更新；建议使用 git / AI 安装以获得就地升级能力。";
      }
      modeHint.textContent = hint;
      modeHint.hidden = !hint;
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

  // 主/备选 Provider 同名保护（与桌面 Web 对齐）：同名 fallback 永远不会触发
  // （registry 静默丢弃），后端保存也会以 blocking issue 拒绝。禁用备选下拉里
  // 与默认 Provider 同名的选项；旧配置已处于同名状态时只显示警告、不静默改数据。
  function syncLlmFallbackSameState() {
    const fallbackSelect = document.getElementById("cfgLlmFallbackProvider");
    const warning = document.getElementById("cfgLlmFallbackSameWarning");
    if (!(fallbackSelect instanceof HTMLSelectElement)) return;
    const mainValue = providerSelect.value;
    for (const option of fallbackSelect.options) {
      option.disabled = Boolean(option.value) && option.value === mainValue;
    }
    if (warning) {
      warning.hidden = !fallbackSelect.value || fallbackSelect.value !== mainValue;
    }
  }

  providerSelect.addEventListener("change", () => {
    showProviderFields(providerSelect.value);
    syncLlmFallbackSameState();
  });

  const fallbackProviderSelect = document.getElementById("cfgLlmFallbackProvider");
  if (fallbackProviderSelect instanceof HTMLSelectElement) {
    fallbackProviderSelect.addEventListener("change", syncLlmFallbackSameState);
  }

  // ── Embedding section: dynamic visibility + placeholder ──
  // Mirrors the backend resolution order in
  // src/openbiliclaw/llm/registry.py:_build_dedicated_embedding_provider.
  const EMBEDDING_DEFAULT_MODEL = {
    "": "留空 = 自动选择",
    openai: "text-embedding-3-small",
    gemini: "gemini-embedding-001",
    ollama: "bge-m3",
    openai_compatible: "bge-large-en-v1.5",
    dashscope: "qwen3-vl-embedding",
  };
  const EMBEDDING_BASE_URL_HINT = {
    "": "留空使用默认",
    openai: "留空 = https://api.openai.com/v1",
    gemini: "(Gemini SDK 不需要 base_url)",
    ollama: "http://localhost:11434/v1",
    openai_compatible: "https://api.together.xyz/v1 / http://localhost:8000/v1",
    dashscope: "留空 = https://dashscope.aliyuncs.com（国际站 dashscope-intl.aliyuncs.com）",
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
    saveBtn.textContent = mode === "degraded" ? "保存并恢复" : "保存配置";
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
      `AI 服务配置有误（后端暂只保留修复入口），保存有效配置后会原地恢复，无需重启。${issueText}`,
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

  const ZHIHU_SOURCE_MODE_FIELDS = [
    ["search", "cfgZhihuModeSearch"],
    ["hot", "cfgZhihuModeHot"],
    ["feed", "cfgZhihuModeFeed"],
    ["creator", "cfgZhihuModeCreator"],
    ["related", "cfgZhihuModeRelated"],
  ];

  function setZhihuSourceModes(rawModes) {
    const fallbackModes = ZHIHU_SOURCE_MODE_FIELDS.map(([mode]) => mode);
    const selected = new Set(
      (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
        .map((mode) => String(mode).trim())
        .filter(Boolean),
    );
    for (const [mode, id] of ZHIHU_SOURCE_MODE_FIELDS) {
      const el = document.getElementById(id);
      if (el) el.checked = selected.has(mode);
    }
  }

  function collectZhihuSourceModes() {
    const selected = ZHIHU_SOURCE_MODE_FIELDS
      .filter(([, id]) => checked(id))
      .map(([mode]) => mode);
    return selected.length > 0 ? selected : ["search"];
  }

  const REDDIT_SOURCE_MODE_FIELDS = [
    ["search", "cfgRedditModeSearch"],
    ["hot", "cfgRedditModeHot"],
    ["subreddit", "cfgRedditModeSubreddit"],
    ["related", "cfgRedditModeRelated"],
  ];

  function setRedditSourceModes(rawModes) {
    const fallbackModes = REDDIT_SOURCE_MODE_FIELDS.map(([mode]) => mode);
    const selected = new Set(
      (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
        .map((mode) => String(mode).trim())
        .filter(Boolean),
    );
    for (const [mode, id] of REDDIT_SOURCE_MODE_FIELDS) {
      const el = document.getElementById(id);
      if (el) el.checked = selected.has(mode);
    }
  }

  function collectRedditSourceModes() {
    const selected = REDDIT_SOURCE_MODE_FIELDS
      .filter(([, id]) => checked(id))
      .map(([mode]) => mode);
    return selected.length > 0 ? selected : ["search"];
  }

  const BANGUMI_SOURCE_MODE_FIELDS = [
    ["search", "cfgBangumiModeSearch"],
    ["ranked", "cfgBangumiModeRanked"],
    ["latest", "cfgBangumiModeLatest"],
  ];
  const LINUXDO_SOURCE_MODE_FIELDS = [
    ["search", "cfgLinuxdoModeSearch"],
    ["hot", "cfgLinuxdoModeHot"],
    ["feed", "cfgLinuxdoModeFeed"],
    ["creator", "cfgLinuxdoModeCreator"],
    ["related", "cfgLinuxdoModeRelated"],
  ];
  const WEIBO_SOURCE_MODE_FIELDS = [
    ["search", "cfgWeiboModeSearch"],
    ["hot", "cfgWeiboModeHot"],
    ["creator", "cfgWeiboModeCreator"],
  ];
  const BANGUMI_SUBJECT_TYPE_FIELDS = [
    ["anime", "cfgBangumiTypeAnime"],
    ["book", "cfgBangumiTypeBook"],
    ["game", "cfgBangumiTypeGame"],
    ["music", "cfgBangumiTypeMusic"],
    ["real", "cfgBangumiTypeReal"],
  ];
  const V2EX_SOURCE_MODE_FIELDS = [
    ["search", "cfgV2exModeSearch"],
    ["node", "cfgV2exModeNode"],
    ["tab", "cfgV2exModeTab"],
    ["hot", "cfgV2exModeHot"],
    ["latest", "cfgV2exModeLatest"],
  ];

  function setCheckedValues(fields, rawValues) {
    const fallback = fields.map(([value]) => value);
    const selected = new Set(
      (Array.isArray(rawValues) && rawValues.length > 0 ? rawValues : fallback)
        .map((value) => String(value).trim())
        .filter(Boolean),
    );
    for (const [value, id] of fields) {
      const el = document.getElementById(id);
      if (el) el.checked = selected.has(value);
    }
  }

  function collectCheckedValues(fields, fallback) {
    const selected = fields.filter(([, id]) => checked(id)).map(([value]) => value);
    return selected.length > 0 ? selected : fallback;
  }

  function setWeiboSourceModes(rawValues) {
    const selected = Array.isArray(rawValues) ? [...rawValues] : rawValues;
    if (Array.isArray(selected) && selected.length === 1 && selected[0] === "creator") {
      selected.unshift("search");
    }
    setCheckedValues(WEIBO_SOURCE_MODE_FIELDS, selected);
  }

  function collectWeiboSourceModes() {
    const selected = collectCheckedValues(WEIBO_SOURCE_MODE_FIELDS, ["search"]);
    if (selected.length === 1 && selected[0] === "creator") {
      const search = document.getElementById("cfgWeiboModeSearch");
      if (search) search.checked = true;
      return ["search", "creator"];
    }
    return selected;
  }

  // Unified per-source login / cookie status from GET /api/sources/status,
  // rendered as a uniform colored-dot line inside every source card.
  //
  // The verdict, its colour and the strength of the evidence behind it all come
  // from shared/source-status.js, which the desktop page and the setup wizard
  // load too. This panel used to keep its own pair of tables, and they had
  // drifted: `no_auth` and `unverified` were the same grey here while the
  // desktop page told them apart, and an unrecognised state rendered as an
  // empty string instead of "状态未知" (spec D6). Having only one row per source
  // to write into, this surface takes `access.line`, which folds the evidence
  // in parenthetically — 「已验证（◆ 联网验证 · 3 分钟前）」 — where the desktop
  // page gives it a badge of its own. Loaded as
  // a classic script by popup.html, so it is a global rather than an import —
  // MV3's CSP forbids pulling it from the backend over HTTP.
  const SourceStatus = globalThis.OpenBiliClawSourceStatus;
  const SOURCE_STATUS_KEYS = SourceStatus.SOURCE_KEYS;
  const BANGUMI_SAVE_ERROR_MESSAGES = {
    invalid_bangumi_access_token:
      "Bangumi 个人令牌被拒绝（缺失、错误或已过期）。请到 next.bgm.tv/demo/access-token 重新生成后重试。",
    bangumi_token_check_failed: "校验 Bangumi 令牌时无法连接 Bangumi，请稍后重试。",
  };

  // The overseas-egress advisory is authored by the backend
  // (sources/platforms.py -> SourceStatusItem.network_hint) and rendered
  // verbatim. This function must never learn a platform name nor read
  // [network].mode: adding a platform must stay a one-line backend change.
  // Only the `enabled` gate lives here — a disabled source makes no requests,
  // so warning about its egress would be noise.
  function applySourceNetworkHint(row, hint, enabled) {
    const text = enabled ? String(hint || "") : "";
    // The status row is a <p>; the hint is a sibling, never a nested <p>.
    let node = row.nextElementSibling;
    if (!node || !node.classList.contains("source-network-hint")) node = null;
    if (!text) {
      if (node) node.remove();
      return;
    }
    if (!node) {
      node = document.createElement("p");
      node.className = "settings-hint source-network-hint";
      row.insertAdjacentElement("afterend", node);
    }
    node.textContent = text;
  }

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
      const access = SourceStatus.describeAccess(item);
      // Offline wording comes from the shared module too, so a backend the user
      // cannot reach reads the same here as on the desktop page. The rejected-
      // token override now lives in describeAccess() rather than being spelled
      // out again here — this panel and the desktop page each having their own
      // copy of that rule is how the two status tables drifted (spec D6).
      if (detail) detail.textContent = access.present ? access.line : access.detail;
      if (dot) dot.style.color = access.color;
      applySourceNetworkHint(row, access.present ? item.network_hint : "", access.enabled);
      row.style.opacity = access.present && !access.enabled ? "0.6" : "1";
    }
    await renderV2exIdentity();
  }

  const V2EX_IDENTITY_ORIGIN_LABELS = {
    pat: "PAT",
    browser: "浏览器",
    configured: "配置",
    accepted: "已选择",
  };

  function renderV2exIdentityResult(identity) {
    const statusEl = document.getElementById("cfgV2exIdentityStatus");
    const acceptButton = document.getElementById("cfgV2exAcceptBrowserIdentity");
    if (!statusEl || !acceptButton) return;
    const claims = identity?.claims && typeof identity.claims === "object" ? identity.claims : {};
    const browser = String(claims.browser || "").trim();
    const active = String(identity?.active_profile_identity?.username || "").trim();
    acceptButton.dataset.username = browser;
    acceptButton.hidden = !(browser && identity?.status === "identity_mismatch");
    if (!identity) {
      setProbeStatus(statusEl, "muted", "后端不可达，暂时无法读取身份状态。");
      return;
    }
    if (identity.status === "identity_mismatch") {
      const detail = Object.entries(claims)
        .map(([origin, username]) => `${V2EX_IDENTITY_ORIGIN_LABELS[origin] || origin}=${username}`)
        .join(" · ");
      setProbeStatus(statusEl, "error", `身份冲突：${detail}。账号初始化已暂停，公开发现仍可用。`);
      return;
    }
    if (identity.identity_switch_required) {
      setProbeStatus(
        statusEl,
        "warning",
        `当前浏览器账号 ${browser || identity.username}，画像仍属于 ${active}；增量同步已暂停，请运行一次 V2EX 完整初始化完成切换。`,
      );
      return;
    }
    if (identity.status === "resolved") {
      const suffix = identity.private_bootstrap_available ? "，浏览器四 Scope 初始化可用。" : "；公开发现可用。";
      setProbeStatus(statusEl, "success", `当前账号 ${identity.username}${suffix}`);
      return;
    }
    setProbeStatus(statusEl, "muted", "尚未识别账号；匿名公开发现仍可用。");
  }

  async function renderV2exIdentity() {
    try {
      renderV2exIdentityResult(await fetchV2exIdentity());
    } catch {
      renderV2exIdentityResult(null);
    }
  }

  async function acceptCurrentV2exBrowserIdentity(button) {
    const username = String(button?.dataset?.username || "").trim();
    const statusEl = document.getElementById("cfgV2exIdentityStatus");
    if (!username || button.disabled) return;
    button.disabled = true;
    setProbeStatus(statusEl, "pending", `正在采用浏览器账号 ${username}…`);
    try {
      await acceptV2exBrowserIdentity(username);
      await renderV2exIdentity();
    } catch (error) {
      setProbeStatus(statusEl, "error", error?.message || "身份选择失败。");
    } finally {
      button.disabled = false;
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

  const LLM_PROVIDER_LABELS = {
    openai: "OpenAI",
    claude: "Claude",
    gemini: "Gemini",
    deepseek: "DeepSeek",
    openrouter: "OpenRouter",
    orcarouter: "OrcaRouter",
    ollama: "Ollama",
    openai_compatible: "OpenAI-compatible",
  };
  const LLM_PROVIDER_DEFAULTS = {
    openai: { model: "gpt-5-nano", base_url: "" },
    claude: { model: "claude-sonnet-4-6", base_url: "" },
    gemini: { model: "gemini-2.5-flash", base_url: "" },
    deepseek: { model: "deepseek-v4-flash", base_url: "https://api.deepseek.com" },
    openrouter: { model: "openai/gpt-5-nano", base_url: "https://openrouter.ai/api/v1" },
    orcarouter: { model: "openai/gpt-4o", base_url: "https://api.orcarouter.ai/v1" },
    ollama: { model: "qwen2.5:7b", base_url: "http://127.0.0.1:11434/v1" },
    openai_compatible: { model: "", base_url: "" },
  };
  const LLM_MODEL_DISCOVERY_PROVIDERS = new Set([
    "openai",
    "deepseek",
    "openrouter",
    "orcarouter",
    "ollama",
    "openai_compatible",
  ]);
  const LLM_MODULE_LABELS = {
    soul: "画像理解",
    discovery: "内容发现",
    recommendation: "推荐表达",
    evaluation: "内容评估",
  };
  const LLM_INSTANCE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  let llmDialogReturnFocus = null;

  function clonePlain(value) {
    return JSON.parse(JSON.stringify(value ?? null));
  }

  function normalizeLlmDraft(llm) {
    const instances = {};
    const rawInstances = llm?.instances && typeof llm.instances === "object"
      ? llm.instances
      : {};
    for (const [rawId, rawInstance] of Object.entries(rawInstances)) {
      const instanceId = String(rawId || "").trim().toLowerCase();
      if (!instanceId || !rawInstance || typeof rawInstance !== "object") continue;
      instances[instanceId] = {
        name: String(rawInstance.name || instanceId),
        provider_type: String(rawInstance.provider_type || ""),
        enabled: rawInstance.enabled !== false,
        api_key: String(rawInstance.api_key || ""),
        model: String(rawInstance.model || ""),
        base_url: String(rawInstance.base_url || ""),
        auth_mode: String(rawInstance.auth_mode || ""),
        api_flavor: String(rawInstance.api_flavor || ""),
        http_referer: String(rawInstance.http_referer || ""),
        x_title: String(rawInstance.x_title || ""),
        reasoning_effort: String(rawInstance.reasoning_effort || ""),
        num_ctx: parseInt(rawInstance.num_ctx, 10) || 0,
      };
    }
    const defaultChain = Array.from(new Set(
      (Array.isArray(llm?.default_chain) ? llm.default_chain : [])
        .map((item) => String(item || "").trim().toLowerCase())
        .filter(Boolean),
    ));
    const routes = {};
    for (const moduleName of Object.keys(LLM_MODULE_LABELS)) {
      const rawRoute = llm?.routes?.[moduleName] || llm?.[moduleName] || {};
      routes[moduleName] = {
        inherit: rawRoute.inherit !== false,
        chain: Array.from(new Set(
          (Array.isArray(rawRoute.chain) ? rawRoute.chain : [])
            .map((item) => String(item || "").trim().toLowerCase())
            .filter(Boolean),
        )),
      };
    }
    return { instances, default_chain: defaultChain, routes };
  }

  function llmInstanceReferences(instanceId) {
    if (!state.llmDraft) return [];
    const references = [];
    if (state.llmDraft.default_chain.includes(instanceId)) references.push("默认链");
    for (const [moduleName, label] of Object.entries(LLM_MODULE_LABELS)) {
      const route = state.llmDraft.routes[moduleName];
      if (route && route.inherit === false && route.chain.includes(instanceId)) {
        references.push(label);
      }
    }
    return references;
  }

  function llmEndpointSummary(instance) {
    const raw = String(instance?.base_url || "").trim();
    if (!raw) return "官方默认地址";
    try {
      const url = new URL(raw);
      return `${url.host}${url.pathname === "/" ? "" : url.pathname}`;
    } catch {
      return raw;
    }
  }

  function createLlmBadge(text, tone = "") {
    const badge = document.createElement("span");
    badge.className = "settings-llm-badge";
    if (tone) badge.dataset.tone = tone;
    badge.textContent = text;
    return badge;
  }

  function createLlmChainAction(action, label, disabled = false) {
    const paths = {
      up: '<path d="m6 15 6-6 6 6"></path>',
      down: '<path d="m6 9 6 6 6-6"></path>',
      remove: '<path d="M6 6l12 12M18 6 6 18"></path>',
    };
    const button = document.createElement("button");
    button.className = "settings-llm-icon-btn";
    button.type = "button";
    button.dataset.llmChainAction = action;
    button.setAttribute("aria-label", label);
    button.disabled = disabled;
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[action]}</svg>`;
    return button;
  }

  function renderLlmInstances() {
    const container = document.getElementById("cfgLlmInstanceList");
    if (!(container instanceof HTMLElement) || !state.llmDraft) return;
    container.replaceChildren();
    const entries = Object.entries(state.llmDraft.instances);
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "settings-llm-empty";
      empty.textContent = "尚未配置 LLM 实例。新建第一个已启用实例后，它会自动加入默认调用链。";
      container.append(empty);
      return;
    }
    for (const [instanceId, instance] of entries) {
      const references = llmInstanceReferences(instanceId);
      const probe = state.llmProbeResults.get(instanceId);
      const card = document.createElement("article");
      card.className = "settings-llm-instance-card";
      card.dataset.enabled = instance.enabled !== false ? "true" : "false";
      card.dataset.llmInstanceId = instanceId;

      const head = document.createElement("div");
      head.className = "settings-llm-instance-head";
      const title = document.createElement("div");
      title.className = "settings-llm-instance-title";
      const name = document.createElement("strong");
      name.textContent = instance.name || instanceId;
      const id = document.createElement("code");
      id.textContent = instanceId;
      title.append(name, id);
      head.append(
        title,
        createLlmBadge(instance.enabled !== false ? "已启用" : "已停用", instance.enabled !== false ? "success" : ""),
      );

      const badges = document.createElement("div");
      badges.className = "settings-llm-badges";
      badges.append(createLlmBadge(LLM_PROVIDER_LABELS[instance.provider_type] || instance.provider_type || "未知类型"));
      for (const reference of references) badges.append(createLlmBadge(reference));

      const meta = document.createElement("p");
      meta.className = "settings-llm-instance-meta";
      const model = document.createElement("span");
      model.textContent = `模型：${instance.model || "未填写"}`;
      const endpoint = document.createElement("span");
      endpoint.textContent = `地址：${llmEndpointSummary(instance)}`;
      meta.append(model, endpoint);

      const probeStatus = document.createElement("p");
      probeStatus.className = "settings-llm-instance-probe";
      probeStatus.setAttribute("aria-live", "polite");
      if (probe?.pending) {
        probeStatus.dataset.tone = "pending";
        probeStatus.textContent = "正在测试真实连通性…";
      } else if (probe) {
        probeStatus.dataset.tone = probe.ok ? "success" : "error";
        probeStatus.textContent = formatConfigProbeResult(probe);
      } else {
        probeStatus.textContent = "尚未测试";
      }

      const actions = document.createElement("div");
      actions.className = "settings-llm-instance-actions";
      for (const [action, label] of [["probe", "测试"], ["edit", "编辑"], ["delete", "删除"]]) {
        const button = document.createElement("button");
        button.className = "settings-secondary-btn";
        button.type = "button";
        button.dataset.llmInstanceAction = action;
        button.dataset.instanceId = instanceId;
        button.textContent = label;
        if (action === "probe") {
          button.disabled = Boolean(probe?.pending) || instance.enabled === false;
          if (instance.enabled === false) button.title = "请先启用实例再测试";
        }
        actions.append(button);
      }
      actions.querySelectorAll("[data-llm-instance-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const action = button.dataset.llmInstanceAction;
          if (action === "probe") void runLlmInstanceProbe(instanceId);
          if (action === "edit") openLlmInstanceDialog(instanceId);
          if (action === "delete") deleteLlmInstance(instanceId);
        });
      });
      card.append(head, badges, meta, probeStatus, actions);
      container.append(card);
    }
  }

  function renderLlmDefaultChain() {
    const list = document.getElementById("cfgLlmDefaultChain");
    const picker = document.getElementById("cfgLlmDefaultChainPicker");
    const addButton = document.getElementById("cfgAddLlmDefaultChainItem");
    if (!(list instanceof HTMLElement) || !(picker instanceof HTMLSelectElement) || !state.llmDraft) return;
    list.replaceChildren();
    const chain = state.llmDraft.default_chain;
    if (!chain.length) {
      const empty = document.createElement("li");
      empty.className = "settings-llm-empty";
      empty.textContent = "默认调用链为空。请创建或从下方加入一个已启用实例。";
      list.append(empty);
    }
    chain.forEach((instanceId, index) => {
      const instance = state.llmDraft.instances[instanceId];
      const item = document.createElement("li");
      item.className = "settings-llm-chain-item";
      item.dataset.instanceId = instanceId;
      const position = document.createElement("span");
      position.className = "settings-llm-chain-position";
      position.setAttribute("aria-label", `优先级 ${index + 1}`);
      position.textContent = String(index + 1);
      const copy = document.createElement("span");
      copy.className = "settings-llm-chain-copy";
      const title = document.createElement("strong");
      title.textContent = instance?.name || instanceId;
      const detail = document.createElement("small");
      detail.textContent = instance
        ? `${LLM_PROVIDER_LABELS[instance.provider_type] || instance.provider_type} · ${instance.model || "未填写模型"}`
        : "实例不存在";
      copy.append(title, detail);
      const actions = document.createElement("span");
      actions.className = "settings-llm-chain-actions";
      const up = createLlmChainAction("up", `上移 ${title.textContent}`, index === 0);
      const down = createLlmChainAction("down", `下移 ${title.textContent}`, index === chain.length - 1);
      const remove = createLlmChainAction("remove", `从默认链移除 ${title.textContent}`, chain.length <= 1);
      for (const button of [up, down, remove]) {
        button.addEventListener("click", () => {
          const next = [...state.llmDraft.default_chain];
          const currentIndex = next.indexOf(instanceId);
          if (currentIndex < 0) return;
          const action = button.dataset.llmChainAction;
          if (action === "up" && currentIndex > 0) {
            [next[currentIndex - 1], next[currentIndex]] = [next[currentIndex], next[currentIndex - 1]];
          }
          if (action === "down" && currentIndex < next.length - 1) {
            [next[currentIndex + 1], next[currentIndex]] = [next[currentIndex], next[currentIndex + 1]];
          }
          if (action === "remove" && next.length > 1) next.splice(currentIndex, 1);
          state.llmDraft.default_chain = next;
          setProbeStatus(document.getElementById("cfgProbeLlmChainStatus"), "", "");
          renderLlmRoutingSummary();
          markSettingsDirty();
        });
      }
      actions.append(up, down, remove);
      item.append(position, copy, actions);
      list.append(item);
    });

    picker.replaceChildren();
    const candidates = Object.entries(state.llmDraft.instances)
      .filter(([instanceId, instance]) => instance.enabled !== false && !chain.includes(instanceId));
    if (!candidates.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "没有可添加的实例";
      picker.append(option);
    } else {
      for (const [instanceId, instance] of candidates) {
        const option = document.createElement("option");
        option.value = instanceId;
        option.textContent = `${instance.name || instanceId} · ${instance.model || "未填写模型"}`;
        picker.append(option);
      }
    }
    picker.disabled = candidates.length === 0;
    if (addButton instanceof HTMLButtonElement) addButton.disabled = candidates.length === 0;
  }

  function renderLlmModuleSummary() {
    const container = document.getElementById("cfgLlmModuleSummary");
    if (!(container instanceof HTMLElement) || !state.llmDraft) return;
    container.replaceChildren();
    for (const [moduleName, label] of Object.entries(LLM_MODULE_LABELS)) {
      const route = state.llmDraft.routes[moduleName];
      const row = document.createElement("div");
      row.className = "settings-llm-module-row";
      const name = document.createElement("strong");
      name.textContent = label;
      const detail = document.createElement("span");
      if (route?.inherit !== false) {
        detail.textContent = "继承默认调用链";
      } else {
        const chainNames = route.chain
          .map((instanceId) => state.llmDraft.instances[instanceId]?.name || instanceId)
          .filter(Boolean);
        detail.textContent = chainNames.join(" → ") || "自定义链尚未配置";
      }
      row.append(name, detail);
      container.append(row);
    }
  }

  function renderLlmRoutingSummary(llm = null) {
    const root = document.getElementById("cfgLlmRoutingSummary");
    if (!(root instanceof HTMLElement)) return;
    if (llm && typeof llm === "object") state.llmDraft = normalizeLlmDraft(llm);
    if (!state.llmDraft) state.llmDraft = normalizeLlmDraft({});
    renderLlmInstances();
    renderLlmDefaultChain();
    renderLlmModuleSummary();
  }

  function addLlmDefaultChainItem() {
    if (!state.llmDraft) return;
    const picker = document.getElementById("cfgLlmDefaultChainPicker");
    const instanceId = picker instanceof HTMLSelectElement ? picker.value : "";
    if (!instanceId || state.llmDraft.default_chain.includes(instanceId)) return;
    state.llmDraft.default_chain.push(instanceId);
    setProbeStatus(document.getElementById("cfgProbeLlmChainStatus"), "", "");
    renderLlmRoutingSummary();
    markSettingsDirty();
  }

  function renderLlmDatalist(id, values, currentValue = "") {
    const list = document.getElementById(id);
    if (!(list instanceof HTMLDataListElement)) return;
    const normalized = [...new Set(
      [...(Array.isArray(values) ? values : []), currentValue]
        .map((value) => String(value || "").trim())
        .filter(Boolean),
    )];
    list.replaceChildren(...normalized.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }));
  }

  function setLlmModelDiscoveryStatus(tone, text) {
    const status = document.getElementById("cfgLlmInstanceModelDiscoveryStatus");
    if (!(status instanceof HTMLElement)) return;
    status.dataset.tone = tone || "neutral";
    status.textContent = text;
  }

  function resetLlmModelDiscovery() {
    renderLlmDatalist("cfgLlmInstanceModelOptions", []);
    const providerType = getVal("cfgLlmInstanceProviderType");
    const supported = LLM_MODEL_DISCOVERY_PROVIDERS.has(providerType);
    const button = document.getElementById("cfgRefreshLlmInstanceModels");
    if (button instanceof HTMLButtonElement) {
      button.hidden = !supported;
      button.disabled = false;
      button.textContent = "获取模型";
    }
    setLlmModelDiscoveryStatus(
      "neutral",
      supported
        ? "可从 OpenAI 兼容 /models 获取；接口不支持时仍可手填。"
        : "该 Provider 没有 OpenAI /models 发现契约，模型名请手填。",
    );
  }

  function buildLlmModelDiscoveryRequest() {
    if (!state.llmDraft) return null;
    const existingId = state.llmEditingInstanceId;
    const instanceId = String(
      existingId || getVal("cfgLlmInstanceId") || "model-discovery-draft"
    ).trim().toLowerCase();
    const current = state.llmDraft.instances[existingId] || {};
    const providerType = getVal("cfgLlmInstanceProviderType").trim();
    const typedKey = getVal("cfgLlmInstanceApiKey");
    const apiKey = checked("cfgLlmInstanceClearApiKey")
      ? ""
      : typedKey || current.api_key || "";
    const instance = {
      ...current,
      name: getVal("cfgLlmInstanceName").trim() || current.name || instanceId,
      provider_type: providerType,
      enabled: true,
      api_key: apiKey,
      model: getVal("cfgLlmInstanceModel").trim(),
      base_url: getVal("cfgLlmInstanceBaseUrl").trim(),
      auth_mode: providerType === "openai"
        ? getVal("cfgLlmInstanceAuthMode") || "api_key"
        : "",
      api_flavor: ["openai", "openai_compatible"].includes(providerType)
        ? getVal("cfgLlmInstanceApiFlavor")
        : "",
      http_referer: providerType === "openrouter"
        ? getVal("cfgLlmInstanceReferer").trim()
        : "",
      x_title: providerType === "openrouter"
        ? getVal("cfgLlmInstanceTitle").trim()
        : "",
      reasoning_effort: ["openai", "claude", "gemini", "deepseek", "openrouter", "orcarouter", "openai_compatible"].includes(providerType)
        ? getVal("cfgLlmInstanceReasoning").trim()
        : "",
      num_ctx: providerType === "ollama"
        ? Math.max(0, getInt("cfgLlmInstanceNumCtx", 0))
        : 0,
    };
    return {
      instanceId,
      config: {
        llm: {
          routing_version: 2,
          instances: {
            ...clonePlain(state.llmDraft.instances),
            [instanceId]: instance,
          },
          default_chain: [...state.llmDraft.default_chain],
          routes: clonePlain(state.llmDraft.routes),
        },
      },
    };
  }

  async function discoverLlmInstanceModels() {
    const request = buildLlmModelDiscoveryRequest();
    const button = document.getElementById("cfgRefreshLlmInstanceModels");
    if (!request || !(button instanceof HTMLButtonElement) || button.disabled) return;
    button.disabled = true;
    button.textContent = "获取中…";
    setLlmModelDiscoveryStatus("pending", "正在向当前端点请求 /models…");
    try {
      const result = await discoverConfigModels(request.config, request.instanceId);
      if (Array.isArray(result?.reasoning_efforts) && result.reasoning_efforts.length) {
        renderLlmDatalist(
          "cfgLlmInstanceReasoningOptions",
          result.reasoning_efforts,
          getVal("cfgLlmInstanceReasoning"),
        );
      }
      if (!result?.ok) {
        throw new Error(result?.error || "端点没有返回模型列表");
      }
      const models = Array.isArray(result.models) ? result.models : [];
      renderLlmDatalist(
        "cfgLlmInstanceModelOptions",
        models,
        getVal("cfgLlmInstanceModel"),
      );
      setLlmModelDiscoveryStatus(
        "success",
        models.length
          ? `已获取 ${models.length} 个模型；可从下拉选择，也可继续手填。`
          : "接口返回了空列表；保留当前手填值。",
      );
    } catch (error) {
      setLlmModelDiscoveryStatus(
        "error",
        `获取失败：${error?.message || "未知错误"}；当前输入未改动，仍可手填。`,
      );
    } finally {
      button.disabled = false;
      button.textContent = "获取模型";
    }
  }

  function syncLlmInstanceConditionalFields() {
    const dialog = document.getElementById("cfgLlmInstanceDialog");
    if (!(dialog instanceof HTMLElement)) return;
    const providerType = getVal("cfgLlmInstanceProviderType");
    dialog.querySelectorAll("[data-llm-instance-field]").forEach((field) => {
      const kind = field.dataset.llmInstanceField;
      const visible =
        (kind === "openai-auth" && providerType === "openai")
        || (kind === "openai-protocol" && ["openai", "openai_compatible"].includes(providerType))
        || (kind === "reasoning" && ["openai", "claude", "gemini", "deepseek", "openrouter", "orcarouter", "openai_compatible"].includes(providerType))
        || (kind === "ollama" && providerType === "ollama")
        || (kind === "openrouter" && providerType === "openrouter");
      field.hidden = !visible;
    });
    resetLlmModelDiscovery();
  }

  function applyLlmProviderDefaults() {
    const providerType = getVal("cfgLlmInstanceProviderType");
    const defaults = LLM_PROVIDER_DEFAULTS[providerType] || {};
    const dialog = document.getElementById("cfgLlmInstanceDialog");
    const previousType = String(dialog?.dataset.providerType || "");
    const previousDefaults = LLM_PROVIDER_DEFAULTS[previousType] || {};
    const isNew = !state.llmEditingInstanceId;
    const model = getVal("cfgLlmInstanceModel");
    const baseUrl = getVal("cfgLlmInstanceBaseUrl");
    if (!model || (isNew && model === (previousDefaults.model || ""))) {
      setVal("cfgLlmInstanceModel", defaults.model || "");
    }
    if (!baseUrl || (isNew && baseUrl === (previousDefaults.base_url || ""))) {
      setVal("cfgLlmInstanceBaseUrl", defaults.base_url || "");
    }
    const previousBaseId = previousType.replace(/_/g, "-");
    const currentId = getVal("cfgLlmInstanceId");
    if (!currentId || (isNew && currentId === previousBaseId)) {
      let candidate = providerType.replace(/_/g, "-");
      let suffix = 2;
      while (state.llmDraft?.instances[candidate]) {
        candidate = `${providerType.replace(/_/g, "-")}-${suffix++}`;
      }
      setVal("cfgLlmInstanceId", candidate);
    }
    const previousLabel = LLM_PROVIDER_LABELS[previousType] || previousType;
    const currentName = getVal("cfgLlmInstanceName");
    if (!currentName || (isNew && currentName === previousLabel)) {
      setVal("cfgLlmInstanceName", LLM_PROVIDER_LABELS[providerType] || providerType);
    }
    if (dialog instanceof HTMLElement) dialog.dataset.providerType = providerType;
    syncLlmInstanceConditionalFields();
  }

  function openLlmInstanceDialog(instanceId = "") {
    if (!state.llmDraft) return;
    const dialog = document.getElementById("cfgLlmInstanceDialog");
    if (!(dialog instanceof HTMLElement)) return;
    const instance = instanceId ? state.llmDraft.instances[instanceId] : null;
    state.llmEditingInstanceId = instanceId;
    llmDialogReturnFocus = document.activeElement;
    dialog.dataset.providerType = instance?.provider_type || "";
    const title = document.getElementById("cfgLlmInstanceDialogTitle");
    if (title) title.textContent = instance ? "编辑 LLM 实例" : "新建 LLM 实例";
    setVal("cfgLlmInstanceName", instance?.name || "");
    setVal("cfgLlmInstanceId", instanceId);
    const idInput = document.getElementById("cfgLlmInstanceId");
    if (idInput instanceof HTMLInputElement) idInput.disabled = Boolean(instance);
    setVal("cfgLlmInstanceProviderType", instance?.provider_type || "openai");
    setVal("cfgLlmInstanceEnabled", instance?.enabled === false ? "off" : "on");
    setVal("cfgLlmInstanceModel", instance?.model || "");
    setVal("cfgLlmInstanceBaseUrl", instance?.base_url || "");
    setVal("cfgLlmInstanceApiKey", "");
    const keyInput = document.getElementById("cfgLlmInstanceApiKey");
    if (keyInput instanceof HTMLInputElement) {
      keyInput.disabled = false;
      keyInput.placeholder = instance?.api_key ? "已配置；留空保留原密钥" : "输入 API Key";
    }
    const clearKey = document.getElementById("cfgLlmInstanceClearApiKey");
    if (clearKey instanceof HTMLInputElement) clearKey.checked = false;
    const clearKeyField = document.getElementById("cfgLlmInstanceClearApiKeyField");
    if (clearKeyField instanceof HTMLElement) clearKeyField.hidden = !instance?.api_key;
    setVal("cfgLlmInstanceAuthMode", instance?.auth_mode || "api_key");
    setVal("cfgLlmInstanceApiFlavor", instance?.api_flavor || "");
    setVal("cfgLlmInstanceReasoning", instance?.reasoning_effort || "");
    setVal("cfgLlmInstanceNumCtx", instance?.num_ctx || 0);
    setVal("cfgLlmInstanceReferer", instance?.http_referer || "");
    setVal("cfgLlmInstanceTitle", instance?.x_title || "");
    const error = document.getElementById("cfgLlmInstanceFormError");
    if (error) error.textContent = "";
    renderLlmDatalist(
      "cfgLlmInstanceReasoningOptions",
      ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
      instance?.reasoning_effort || "",
    );
    if (instance) syncLlmInstanceConditionalFields();
    else applyLlmProviderDefaults();
    dialog.hidden = false;
    overlay.classList.add("has-instance-dialog");
    window.setTimeout(() => document.getElementById("cfgLlmInstanceName")?.focus(), 0);
  }

  function closeLlmInstanceDialog() {
    const dialog = document.getElementById("cfgLlmInstanceDialog");
    if (!(dialog instanceof HTMLElement) || dialog.hidden) return;
    dialog.hidden = true;
    overlay.classList.remove("has-instance-dialog");
    state.llmEditingInstanceId = "";
    if (llmDialogReturnFocus?.focus) llmDialogReturnFocus.focus();
    llmDialogReturnFocus = null;
  }

  function saveLlmInstanceDraft() {
    if (!state.llmDraft) return;
    const existingId = state.llmEditingInstanceId;
    const instanceId = String(existingId || getVal("cfgLlmInstanceId")).trim().toLowerCase();
    const name = getVal("cfgLlmInstanceName").trim();
    const providerType = getVal("cfgLlmInstanceProviderType").trim();
    const enabled = getVal("cfgLlmInstanceEnabled") !== "off";
    const model = getVal("cfgLlmInstanceModel").trim();
    const baseUrl = getVal("cfgLlmInstanceBaseUrl").trim();
    const error = document.getElementById("cfgLlmInstanceFormError");
    const fail = (message, focusId = "") => {
      if (error) error.textContent = message;
      if (focusId) document.getElementById(focusId)?.focus();
    };
    if (!LLM_INSTANCE_ID_PATTERN.test(instanceId)) {
      fail("实例 ID 只能使用小写字母、数字、下划线和连字符，且最长 64 个字符。", "cfgLlmInstanceId");
      return;
    }
    if (!existingId && state.llmDraft.instances[instanceId]) {
      fail("这个实例 ID 已经存在。", "cfgLlmInstanceId");
      return;
    }
    if (!name) {
      fail("请填写实例名称。", "cfgLlmInstanceName");
      return;
    }
    if (enabled && !model) {
      fail("启用的实例必须明确填写模型。", "cfgLlmInstanceModel");
      return;
    }
    if (enabled && providerType === "openai_compatible" && !baseUrl) {
      fail("OpenAI-compatible 实例必须填写 Base URL。", "cfgLlmInstanceBaseUrl");
      return;
    }
    const current = state.llmDraft.instances[existingId] || {};
    const typedKey = getVal("cfgLlmInstanceApiKey");
    const authMode = providerType === "openai"
      ? getVal("cfgLlmInstanceAuthMode") || "api_key"
      : "";
    const effectiveKey = checked("cfgLlmInstanceClearApiKey")
      ? ""
      : typedKey || current.api_key || "";
    const keyOptional = ["ollama", "gemini"].includes(providerType)
      || (providerType === "openai" && authMode === "codex_oauth");
    if (enabled && !keyOptional && !effectiveKey) {
      fail("启用的远端实例需要 API Key。", "cfgLlmInstanceApiKey");
      return;
    }
    const references = existingId ? llmInstanceReferences(existingId) : [];
    if (!enabled && references.length) {
      fail(`请先从这些调用链移除实例：${references.join("、")}。`);
      return;
    }
    state.llmDraft.instances[instanceId] = {
      name,
      provider_type: providerType,
      enabled,
      api_key: effectiveKey,
      model,
      base_url: baseUrl,
      auth_mode: authMode,
      api_flavor: ["openai", "openai_compatible"].includes(providerType)
        ? getVal("cfgLlmInstanceApiFlavor")
        : "",
      http_referer: providerType === "openrouter" ? getVal("cfgLlmInstanceReferer").trim() : "",
      x_title: providerType === "openrouter" ? getVal("cfgLlmInstanceTitle").trim() : "",
      reasoning_effort: ["openai", "claude", "gemini", "deepseek", "openrouter", "orcarouter", "openai_compatible"].includes(providerType)
        ? getVal("cfgLlmInstanceReasoning")
        : "",
      num_ctx: providerType === "ollama" ? Math.max(0, getInt("cfgLlmInstanceNumCtx", 0)) : 0,
    };
    if (enabled && !state.llmDraft.default_chain.length) {
      state.llmDraft.default_chain.push(instanceId);
    }
    state.llmProbeResults.delete(instanceId);
    closeLlmInstanceDialog();
    renderLlmRoutingSummary();
    markSettingsDirty();
    showToast("实例草稿已更新；点击底部“保存配置”后生效。", "success");
  }

  function deleteLlmInstance(instanceId) {
    if (!state.llmDraft?.instances[instanceId]) return;
    const references = llmInstanceReferences(instanceId);
    if (references.length) {
      showToast(`无法删除：仍被 ${references.join("、")} 引用。`, "error");
      return;
    }
    const name = state.llmDraft.instances[instanceId].name || instanceId;
    if (!window.confirm(`删除 LLM 实例「${name}」？`)) return;
    delete state.llmDraft.instances[instanceId];
    state.llmProbeResults.delete(instanceId);
    renderLlmRoutingSummary();
    markSettingsDirty();
    showToast("实例已从草稿删除；保存配置后生效。", "success");
  }

  async function runLlmInstanceProbe(instanceId) {
    if (!state.llmDraft?.instances[instanceId]) return;
    state.llmProbeResults.set(instanceId, { pending: true });
    renderLlmInstances();
    try {
      const result = await probeConfigService("llm_instance", collectForm(), instanceId);
      state.llmProbeResults.set(instanceId, result);
    } catch (err) {
      state.llmProbeResults.set(instanceId, {
        ok: false,
        error: err?.details?.message || err?.message || "实例探测失败",
      });
    }
    renderLlmInstances();
  }

  function syncBiliDateFields() {
    const presetEl = document.getElementById("cfgBiliDatePreset");
    const customFields = document.getElementById("cfgBiliDateCustomFields");
    if (customFields) customFields.hidden = presetEl?.value !== "custom";
  }

  const POPUP_SOURCE_DATE_SLUGS = [
    "bilibili",
    "xiaohongshu",
    "douyin",
    "weibo",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
  ];

  function ensurePopupSourceDateFields() {
    for (const slug of POPUP_SOURCE_DATE_SLUGS) {
      if (slug === "bilibili") continue;
      const body = document.getElementById("sourceCardBody-" + slug);
      if (!body || body.querySelector('[data-date-source="' + slug + '"]')) continue;
      const html = '<section class="settings-field" data-date-source="' + slug + '">'
        + '<label for="cfg' + slug + 'DatePreset">发布日期范围</label>'
        + '<select id="cfg' + slug + 'DatePreset">'
        + '<option value="all">全部日期</option>'
        + '<option value="last_7_days">最近一周</option>'
        + '<option value="last_30_days">最近一个月</option>'
        + '<option value="last_6_months">最近半年</option>'
        + '<option value="last_1_year">最近一年</option>'
        + '<option value="custom">自定义</option>'
        + '</select>'
        + '<div id="cfg' + slug + 'DateCustomFields" hidden>'
        + '<label for="cfg' + slug + 'DateStart">开始日期（YYYY-MM-DD，留空不限）</label>'
        + '<input id="cfg' + slug + 'DateStart" type="date">'
        + '<label for="cfg' + slug + 'DateEnd">结束日期（YYYY-MM-DD，留空不限）</label>'
        + '<input id="cfg' + slug + 'DateEnd" type="date">'
        + '</div>'
        + '<label for="cfg' + slug + 'DateWeight">范围外权重（0 到 1；1 = 严格排除）</label>'
        + '<input id="cfg' + slug + 'DateWeight" type="number" min="0" max="1" step="0.01" inputmode="decimal" placeholder="0.5">'
        + '</section>';
      body.insertAdjacentHTML("beforeend", html);
      const presetEl = document.getElementById("cfg" + slug + "DatePreset");
      presetEl?.addEventListener("change", () => {
        syncPopupSourceDateFields(slug);
        markSettingsDirty();
      });
    }
  }

  function syncPopupSourceDateFields(slug) {
    const customFields = document.getElementById("cfg" + slug + "DateCustomFields");
    const preset = getVal("cfg" + slug + "DatePreset");
    if (customFields) customFields.hidden = preset !== "custom";
  }

  function popupSourceDateFieldsForUpdate(slug) {
    return {
      recommendation_date_preset: getVal("cfg" + slug + "DatePreset") || "all",
      recommendation_date_start: getVal("cfg" + slug + "DateStart"),
      recommendation_date_end: getVal("cfg" + slug + "DateEnd"),
      recommendation_date_weight: Math.min(
        1,
        Math.max(0, getFloat("cfg" + slug + "DateWeight", 0.5))
      ),
    };
  }

  function populateForm(cfg) {
    applyRuntimeConfig(cfg);
    ensurePopupSourceDateFields();
    // LLM
    providerSelect.value = cfg.llm?.default_provider || "openai";
    showProviderFields(providerSelect.value);
    setVal("cfgLlmConcurrency", cfg.llm?.concurrency ?? 3);
    setVal("cfgLlmTimeout", cfg.llm?.timeout ?? 1200);
    setVal("cfgLlmConcurrencyV2", cfg.llm?.concurrency ?? 3);
    setVal("cfgLlmTimeoutV2", cfg.llm?.timeout ?? 1200);
    state.llmProbeResults.clear();
    renderLlmRoutingSummary(cfg.llm || {});
    setVal("cfgLlmFallbackProvider", cfg.llm?.fallback_provider);
    syncLlmFallbackSameState();

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
    setVal("cfgOrcarouterKey", cfg.llm?.orcarouter?.api_key);
    setVal("cfgOrcarouterModel", cfg.llm?.orcarouter?.model);
    setVal("cfgOrcarouterBaseUrl", cfg.llm?.orcarouter?.base_url);
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
    const embMultimodal = document.getElementById("cfgEmbeddingMultimodalEnabled");
    if (embMultimodal) embMultimodal.checked = cfg.llm?.embedding?.multimodal_enabled === true;
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
    setVal("cfgBilibiliMinInterval", cfg.sources?.bilibili?.min_interval_minutes);
    const biliDatePreset = document.getElementById("cfgBiliDatePreset");
    if (biliDatePreset) biliDatePreset.value = cfg.sources?.bilibili?.recommendation_date_preset || "all";
    setVal("cfgBiliDateStart", cfg.sources?.bilibili?.recommendation_date_start);
    setVal("cfgBiliDateEnd", cfg.sources?.bilibili?.recommendation_date_end);
    setVal("cfgBiliDateWeight", cfg.sources?.bilibili?.recommendation_date_weight ?? 0.5);
    syncBiliDateFields();
    for (const slug of POPUP_SOURCE_DATE_SLUGS) {
      if (slug === "bilibili") continue;
      const sourceCfg = cfg.sources?.[slug] || {};
      const presetEl = document.getElementById("cfg" + slug + "DatePreset");
      if (presetEl) presetEl.value = sourceCfg.recommendation_date_preset || "all";
      setVal("cfg" + slug + "DateStart", sourceCfg.recommendation_date_start);
      setVal("cfg" + slug + "DateEnd", sourceCfg.recommendation_date_end);
      setVal("cfg" + slug + "DateWeight", sourceCfg.recommendation_date_weight ?? 0.5);
      syncPopupSourceDateFields(slug);
    }

    // Sources
    setVal("cfgSourcesBrowserCdp", cfg.sources?.browser?.cdp_url);
    const sourcesBrowserHeaded = document.getElementById("cfgSourcesBrowserHeaded");
    if (sourcesBrowserHeaded) {
      sourcesBrowserHeaded.checked = cfg.sources?.browser?.headed === true;
    }
    const xhsEnabled = document.getElementById("cfgXhsEnabled");
    if (xhsEnabled) xhsEnabled.checked = cfg.sources?.xiaohongshu?.enabled === true;
    const xhsIncremental = document.getElementById("cfgXhsIncremental");
    if (xhsIncremental) xhsIncremental.checked = cfg.sources?.xiaohongshu?.incremental_enabled === true;
    setVal("cfgXhsDailySearchBudget", cfg.sources?.xiaohongshu?.daily_search_budget);
    setVal("cfgXhsDailyCreatorBudget", cfg.sources?.xiaohongshu?.daily_creator_budget);
    setVal("cfgXhsTaskInterval", cfg.sources?.xiaohongshu?.task_interval_seconds);
    setVal("cfgXhsMinInterval", cfg.sources?.xiaohongshu?.min_interval_minutes);
    const douyinEnabled = document.getElementById("cfgDouyinEnabled");
    if (douyinEnabled) douyinEnabled.checked = cfg.sources?.douyin?.enabled === true;
    const douyinIncremental = document.getElementById("cfgDouyinIncremental");
    if (douyinIncremental) douyinIncremental.checked = cfg.sources?.douyin?.incremental_enabled === true;
    setVal("cfgDouyinCookie", cfg.sources?.douyin?.cookie);
    setVal("cfgDouyinCookieEnv", cfg.sources?.douyin?.cookie_env);
    setVal("cfgDouyinDailySearchBudget", cfg.sources?.douyin?.daily_search_budget);
    setVal("cfgDouyinDailyHotBudget", cfg.sources?.douyin?.daily_hot_budget);
    setVal("cfgDouyinDailyFeedBudget", cfg.sources?.douyin?.daily_feed_budget);
    setVal("cfgDouyinRequestInterval", cfg.sources?.douyin?.request_interval_seconds);
    setVal("cfgDouyinMinInterval", cfg.sources?.douyin?.min_interval_minutes);
    const weiboEnabled = document.getElementById("cfgWeiboEnabled");
    if (weiboEnabled) weiboEnabled.checked = cfg.sources?.weibo?.enabled === true;
    setWeiboSourceModes(cfg.sources?.weibo?.source_modes);
    setVal("cfgWeiboDailySearchBudget", cfg.sources?.weibo?.daily_search_budget);
    setVal("cfgWeiboDailyHotBudget", cfg.sources?.weibo?.daily_hot_budget);
    setVal("cfgWeiboDailyCreatorBudget", cfg.sources?.weibo?.daily_creator_budget);
    setVal("cfgWeiboRequestInterval", cfg.sources?.weibo?.request_interval_seconds);
    setVal("cfgWeiboMinInterval", cfg.sources?.weibo?.min_interval_minutes);
    const youtubeEnabled = document.getElementById("cfgYoutubeEnabled");
    if (youtubeEnabled) youtubeEnabled.checked = cfg.sources?.youtube?.enabled === true;
    const youtubeIncremental = document.getElementById("cfgYoutubeIncremental");
    if (youtubeIncremental) youtubeIncremental.checked = cfg.sources?.youtube?.incremental_enabled === true;
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
    const zhihuEnabled = document.getElementById("cfgZhihuEnabled");
    if (zhihuEnabled) zhihuEnabled.checked = cfg.sources?.zhihu?.enabled === true;
    const zhihuIncremental = document.getElementById("cfgZhihuIncremental");
    if (zhihuIncremental) zhihuIncremental.checked = cfg.sources?.zhihu?.incremental_enabled === true;
    setZhihuSourceModes(cfg.sources?.zhihu?.source_modes);
    setVal("cfgZhihuDailySearchBudget", cfg.sources?.zhihu?.daily_search_budget);
    setVal("cfgZhihuDailyHotBudget", cfg.sources?.zhihu?.daily_hot_budget);
    setVal("cfgZhihuDailyFeedBudget", cfg.sources?.zhihu?.daily_feed_budget);
    setVal("cfgZhihuDailyCreatorBudget", cfg.sources?.zhihu?.daily_creator_budget);
    setVal("cfgZhihuDailyRelatedBudget", cfg.sources?.zhihu?.daily_related_budget);
    setVal("cfgZhihuRequestInterval", cfg.sources?.zhihu?.request_interval_seconds);
    setVal("cfgZhihuMinInterval", cfg.sources?.zhihu?.min_interval_minutes);
    const redditEnabled = document.getElementById("cfgRedditEnabled");
    if (redditEnabled) redditEnabled.checked = cfg.sources?.reddit?.enabled === true;
    const redditIncremental = document.getElementById("cfgRedditIncremental");
    if (redditIncremental) redditIncremental.checked = cfg.sources?.reddit?.incremental_enabled === true;
    setVal("cfgRedditBackend", cfg.sources?.reddit?.backend || "rdt");
    setRedditSourceModes(cfg.sources?.reddit?.source_modes);
    setVal("cfgRedditDailySearchBudget", cfg.sources?.reddit?.daily_search_budget);
    setVal("cfgRedditDailyHotBudget", cfg.sources?.reddit?.daily_hot_budget);
    setVal("cfgRedditDailySubredditBudget", cfg.sources?.reddit?.daily_subreddit_budget);
    setVal("cfgRedditDailyRelatedBudget", cfg.sources?.reddit?.daily_related_budget);
    setVal("cfgRedditRequestInterval", cfg.sources?.reddit?.request_interval_seconds);
    setVal("cfgRedditMinInterval", cfg.sources?.reddit?.min_interval_minutes);
    const bangumiEnabled = document.getElementById("cfgBangumiEnabled");
    if (bangumiEnabled) bangumiEnabled.checked = cfg.sources?.bangumi?.enabled === true;
    setVal("cfgBangumiUsername", cfg.sources?.bangumi?.username);
    {
      // Token is a secret and never returned by GET; access_token_set only
      // signals whether one is stored. Keep the field empty and reflect the
      // stored state in the placeholder so an untouched save never clobbers it.
      const bangumiToken = document.getElementById("cfgBangumiAccessToken");
      if (bangumiToken) {
        bangumiToken.value = "";
        bangumiToken.placeholder = cfg.sources?.bangumi?.access_token_set
          ? "已配置（留空保持不变；填写新令牌以替换）"
          : "填写以自动识别当前用户并读取私密收藏";
      }
      // Clear-token is a per-save action; never leave it pre-checked after a
      // reload, and disable it when nothing is stored to clear.
      const bangumiClearToken = document.getElementById("cfgBangumiClearToken");
      if (bangumiClearToken) {
        bangumiClearToken.checked = false;
        bangumiClearToken.disabled = cfg.sources?.bangumi?.access_token_set !== true;
      }
    }
    setCheckedValues(BANGUMI_SOURCE_MODE_FIELDS, cfg.sources?.bangumi?.source_modes);
    setCheckedValues(BANGUMI_SUBJECT_TYPE_FIELDS, cfg.sources?.bangumi?.subject_types);
    setVal("cfgBangumiDailySearchBudget", cfg.sources?.bangumi?.daily_search_budget);
    setVal("cfgBangumiDailyRankedBudget", cfg.sources?.bangumi?.daily_ranked_budget);
    setVal("cfgBangumiDailyLatestBudget", cfg.sources?.bangumi?.daily_latest_budget);
    setVal("cfgBangumiRequestInterval", cfg.sources?.bangumi?.request_interval_seconds);
    setVal("cfgBangumiMinInterval", cfg.sources?.bangumi?.min_interval_minutes);
    setVal("cfgBangumiBootstrapLimit", cfg.sources?.bangumi?.bootstrap_limit);
    const linuxdoEnabled = document.getElementById("cfgLinuxdoEnabled");
    if (linuxdoEnabled) linuxdoEnabled.checked = cfg.sources?.linuxdo?.enabled === true;
    const linuxdoIncremental = document.getElementById("cfgLinuxdoIncremental");
    if (linuxdoIncremental) linuxdoIncremental.checked = cfg.sources?.linuxdo?.incremental_enabled === true;
    setCheckedValues(LINUXDO_SOURCE_MODE_FIELDS, cfg.sources?.linuxdo?.source_modes);
    setVal("cfgLinuxdoDailySearchBudget", cfg.sources?.linuxdo?.daily_search_budget);
    setVal("cfgLinuxdoDailyHotBudget", cfg.sources?.linuxdo?.daily_hot_budget);
    setVal("cfgLinuxdoDailyFeedBudget", cfg.sources?.linuxdo?.daily_feed_budget);
    setVal("cfgLinuxdoDailyCreatorBudget", cfg.sources?.linuxdo?.daily_creator_budget);
    setVal("cfgLinuxdoDailyRelatedBudget", cfg.sources?.linuxdo?.daily_related_budget);
    setVal("cfgLinuxdoRequestInterval", cfg.sources?.linuxdo?.request_interval_seconds);
    setVal("cfgLinuxdoMinInterval", cfg.sources?.linuxdo?.min_interval_minutes);
    setVal("cfgLinuxdoBootstrapLimit", cfg.sources?.linuxdo?.bootstrap_limit);
    const v2exEnabled = document.getElementById("cfgV2exEnabled");
    if (v2exEnabled) v2exEnabled.checked = cfg.sources?.v2ex?.enabled === true;
    const v2exIncremental = document.getElementById("cfgV2exIncremental");
    if (v2exIncremental) v2exIncremental.checked = cfg.sources?.v2ex?.incremental_enabled === true;
    setVal("cfgV2exUsername", cfg.sources?.v2ex?.username);
    {
      const v2exToken = document.getElementById("cfgV2exAccessToken");
      if (v2exToken) {
        v2exToken.value = "";
        v2exToken.placeholder = cfg.sources?.v2ex?.access_token_set
          ? "已配置（留空保持不变；填写新 PAT 以替换）"
          : "可留空；匿名公开发现可直接使用";
      }
      const v2exClearToken = document.getElementById("cfgV2exClearToken");
      if (v2exClearToken) {
        v2exClearToken.checked = false;
        v2exClearToken.disabled = cfg.sources?.v2ex?.access_token_set !== true;
      }
    }
    setCheckedValues(V2EX_SOURCE_MODE_FIELDS, cfg.sources?.v2ex?.source_modes);
    setVal("cfgV2exDailySearchBudget", cfg.sources?.v2ex?.daily_search_budget);
    setVal("cfgV2exDailyNodeBudget", cfg.sources?.v2ex?.daily_node_budget);
    setVal("cfgV2exDailyTabBudget", cfg.sources?.v2ex?.daily_tab_budget);
    setVal("cfgV2exDailyHotBudget", cfg.sources?.v2ex?.daily_hot_budget);
    setVal("cfgV2exDailyLatestBudget", cfg.sources?.v2ex?.daily_latest_budget);
    setVal("cfgV2exRequestInterval", cfg.sources?.v2ex?.request_interval_seconds);
    setVal("cfgV2exMinInterval", cfg.sources?.v2ex?.min_interval_minutes);
    void renderSourcesStatus();

    // General
    const lang = document.getElementById("cfgLanguage");
    if (lang) lang.value = cfg.language || "zh";
    setVal("cfgDataDir", cfg.data_dir);
    setVal("cfgStorageDbPath", cfg.storage?.db_path);
    // Mirrors the [network].mode backend default (system since v0.3.175);
    // only reached if /api/config omits the field.
    setVal("cfgNetworkProxyMode", cfg.network?.mode || "system");
    setVal("cfgNetworkProxy", cfg.network?.proxy || "");
    const savedAutoSync = document.getElementById("cfgSavedAutoSync");
    if (savedAutoSync instanceof HTMLInputElement) {
      savedAutoSync.checked = cfg.saved_sync?.auto_sync_enabled === true;
      savedAutoSync.dataset.confirmed = savedAutoSync.checked ? "true" : "false";
    }

    // Scheduler
    const schedEnabled = document.getElementById("cfgSchedulerEnabled");
    if (schedEnabled) schedEnabled.checked = cfg.scheduler?.enabled === false;
    const pauseOnDisconnect = document.getElementById("cfgPauseOnDisconnect");
    if (pauseOnDisconnect) {
      pauseOnDisconnect.checked = cfg.scheduler?.pause_on_extension_disconnect === true;
    }
    const sourceIncrementalEnabled = document.getElementById("cfgSourceIncrementalEnabled");
    if (sourceIncrementalEnabled) {
      sourceIncrementalEnabled.checked = cfg.scheduler?.source_incremental_enabled === true;
    }
    setVal("cfgExtensionDisconnectGrace", cfg.scheduler?.extension_disconnect_grace_seconds);
    setVal("cfgPoolTarget", cfg.scheduler?.pool_target_count);
    setVal("cfgAccountSyncInterval", cfg.scheduler?.account_sync_interval_hours);
    setVal("cfgRefreshCheckInterval", cfg.scheduler?.refresh_check_interval_seconds);
    setVal("cfgSignalEventThreshold", cfg.scheduler?.signal_event_threshold);
    setVal("cfgFeedbackBatchThreshold", cfg.scheduler?.feedback_batch_threshold);
    setVal("cfgTrendingRefreshMinutes", cfg.scheduler?.trending_refresh_minutes);
    setVal("cfgExploreRefreshMinutes", cfg.scheduler?.explore_refresh_minutes);
    setVal("cfgDiscoveryLimit", cfg.scheduler?.discovery_limit);
    setVal("cfgEvalScorer", cfg.discovery?.eval_scorer || "llm");
    setVal("cfgKeywordGenerationMode", cfg.discovery?.keyword_generation_mode || "hybrid");
    const visualProfile = document.getElementById("cfgVisualProfileEnabled");
    if (visualProfile) visualProfile.checked = cfg.discovery?.visual_profile_enabled === true;
    const keyframe = document.getElementById("cfgKeyframeEnabled");
    if (keyframe) keyframe.checked = cfg.discovery?.keyframe_enabled === true;
    setVal("cfgKeyframeMaxFrames", cfg.discovery?.keyframe_max_frames ?? 4);
    setVal("cfgKeyframeFetchLimit", cfg.discovery?.keyframe_fetch_limit ?? 50);
    const danmaku = document.getElementById("cfgDanmakuEnabled");
    if (danmaku) danmaku.checked = cfg.discovery?.danmaku_enabled === true;
    setVal("cfgDanmakuFetchLimit", cfg.discovery?.danmaku_fetch_limit ?? 50);
    setVal("cfgDanmakuMaxChars", cfg.discovery?.danmaku_max_chars ?? 500);
    setVal("cfgCandidateEvalConcurrency", cfg.discovery?.candidate_eval_concurrency ?? 3);
    const multimodalEvaluation = document.getElementById("cfgMultimodalEvaluationEnabled");
    if (multimodalEvaluation) {
      multimodalEvaluation.checked = cfg.discovery?.multimodal_evaluation_enabled === true;
    }
    setVal("cfgMultimodalBatchSize", cfg.discovery?.multimodal_batch_size ?? 8);
    setVal("cfgMultimodalImageMaxPx", cfg.discovery?.multimodal_image_max_px ?? 384);
    setVal("cfgMultimodalImageQuality", cfg.discovery?.multimodal_image_quality ?? 72);
    setVal("cfgMultimodalImageTimeout", cfg.discovery?.multimodal_image_timeout_seconds ?? 6);
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
    setVal("cfgPoolShareZhihu", cfg.scheduler?.pool_source_shares?.zhihu);
    setVal("cfgPoolShareReddit", cfg.scheduler?.pool_source_shares?.reddit);
    setVal("cfgPoolShareBangumi", cfg.scheduler?.pool_source_shares?.bangumi);
    setVal("cfgPoolShareLinuxdo", cfg.scheduler?.pool_source_shares?.linuxdo);
    setVal("cfgPoolShareV2ex", cfg.scheduler?.pool_source_shares?.v2ex);
    setVal("cfgSpeculationInterval", cfg.scheduler?.speculation_interval_minutes);
    setVal("cfgSpeculationTtl", cfg.scheduler?.speculation_ttl_days);
    setVal("cfgSpeculationCooldown", cfg.scheduler?.speculation_cooldown_days);
    setVal("cfgSpeculationThreshold", cfg.scheduler?.speculation_confirmation_threshold);
    setVal("cfgSpeculationMaxActive", cfg.scheduler?.speculation_max_active);
    setVal("cfgSpeculationMaxPrimary", cfg.scheduler?.speculation_max_primary_interests);
    setVal("cfgSpeculationMaxSecondary", cfg.scheduler?.speculation_max_secondary_interests);

    // Soul cognition budgets (issue #169)
    setVal("cfgAwarenessEventBatchSize", cfg.soul?.awareness_event_batch_size ?? 300);
    setVal("cfgInsightNoteBatchSize", cfg.soul?.insight_note_batch_size ?? 150);
    setVal("cfgCognitionMaxTokens", cfg.soul?.cognition_max_tokens ?? 32768);

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
    // The enable checkboxes were just repopulated, so the cards' collapsed /
    // disabled state has to be recomputed from the new values, and the form now
    // mirrors the backend snapshot — nothing is pending.
    syncSourceCardEnabledState();
    clearSettingsDirty();
  }

  function collectForm() {
    const logPath = splitLogPath(getVal("cfgLogPath"), state.runtimeConfig?.logging);
    const embeddingFallbackProvider = getVal("cfgEmbeddingFallbackProvider");
    const llmDraft = state.llmDraft || normalizeLlmDraft(state.runtimeConfig?.llm || {});
    return {
      language: getVal("cfgLanguage"),
      data_dir: getVal("cfgDataDir"),
      llm: {
        routing_version: 2,
        instances: clonePlain(llmDraft.instances),
        default_chain: [...llmDraft.default_chain],
        routes: Object.fromEntries(
          Object.entries(llmDraft.routes).map(([moduleName, route]) => [
            moduleName,
            {
              inherit: route.inherit !== false,
              chain: route.inherit !== false ? [] : [...route.chain],
            },
          ]),
        ),
        concurrency: getInt("cfgLlmConcurrencyV2", 3),
        timeout: getInt("cfgLlmTimeoutV2", 1200),
        embedding: {
          ...(state.runtimeConfig?.llm?.embedding || {}),
          provider: getVal("cfgEmbeddingProvider"),
          api_key: getVal("cfgEmbeddingApiKey"),
          base_url: getVal("cfgEmbeddingBaseUrl"),
          model: getVal("cfgEmbeddingModel"),
          similarity_threshold: getFloat("cfgEmbeddingSimilarity", 0.82),
          fallback_enabled: Boolean(embeddingFallbackProvider),
          fallback_provider: embeddingFallbackProvider,
          multimodal_enabled: checked("cfgEmbeddingMultimodalEnabled"),
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
          min_interval_minutes: getInt("cfgBilibiliMinInterval", 3),
          recommendation_date_preset: getVal("cfgBiliDatePreset") || "all",
          recommendation_date_start: getVal("cfgBiliDateStart"),
          recommendation_date_end: getVal("cfgBiliDateEnd"),
          recommendation_date_weight: Math.min(
            1,
            Math.max(0, getFloat("cfgBiliDateWeight", 0.5))
          ),
        },
        // Empty-field fallbacks mirror the backend dataclass defaults
        // (budgets: 0 = uncapped) so the popup and the web settings page
        // write identical values for an untouched form.
        xiaohongshu: {
          enabled: checked("cfgXhsEnabled"),
          incremental_enabled: checked("cfgXhsIncremental"),
          daily_search_budget: getInt("cfgXhsDailySearchBudget", 20),
          daily_creator_budget: getInt("cfgXhsDailyCreatorBudget", 0),
          task_interval_seconds: getInt("cfgXhsTaskInterval", 1200),
          min_interval_minutes: getInt("cfgXhsMinInterval", 20),
          ...popupSourceDateFieldsForUpdate("xiaohongshu")
        },
        douyin: {
          enabled: checked("cfgDouyinEnabled"),
          incremental_enabled: checked("cfgDouyinIncremental"),
          mode: "direct",
          ...(getVal("cfgDouyinCookie") ? { cookie: getVal("cfgDouyinCookie") } : {}),
          cookie_env: getVal("cfgDouyinCookieEnv"),
          daily_search_budget: getInt("cfgDouyinDailySearchBudget", 0),
          daily_hot_budget: getInt("cfgDouyinDailyHotBudget", 0),
          daily_feed_budget: getInt("cfgDouyinDailyFeedBudget", 0),
          request_interval_seconds: getInt("cfgDouyinRequestInterval", 2),
          min_interval_minutes: getInt("cfgDouyinMinInterval", 3),
          ...popupSourceDateFieldsForUpdate("douyin")
        },
        weibo: {
          enabled: checked("cfgWeiboEnabled"),
          source_modes: collectWeiboSourceModes(),
          daily_search_budget: getInt("cfgWeiboDailySearchBudget", 60),
          daily_hot_budget: getInt("cfgWeiboDailyHotBudget", 10),
          daily_creator_budget: getInt("cfgWeiboDailyCreatorBudget", 30),
          request_interval_seconds: getInt("cfgWeiboRequestInterval", 3),
          min_interval_minutes: getInt("cfgWeiboMinInterval", 10),
          ...popupSourceDateFieldsForUpdate("weibo")
        },
        youtube: {
          enabled: checked("cfgYoutubeEnabled"),
          incremental_enabled: checked("cfgYoutubeIncremental"),
          daily_search_budget: getInt("cfgYoutubeDailySearchBudget", 0),
          daily_trending_budget: getInt("cfgYoutubeDailyTrendingBudget", 0),
          daily_channel_budget: getInt("cfgYoutubeDailyChannelBudget", 0),
          request_interval_seconds: getInt("cfgYoutubeRequestInterval", 2),
          min_interval_minutes: getInt("cfgYoutubeMinInterval", 3),
          ...popupSourceDateFieldsForUpdate("youtube")
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
          min_interval_minutes: getInt("cfgTwitterMinInterval", 3),
          ...popupSourceDateFieldsForUpdate("twitter")
        },
        zhihu: {
          enabled: checked("cfgZhihuEnabled"),
          incremental_enabled: checked("cfgZhihuIncremental"),
          source_modes: collectZhihuSourceModes(),
          daily_search_budget: getInt("cfgZhihuDailySearchBudget", 0),
          daily_hot_budget: getInt("cfgZhihuDailyHotBudget", 0),
          daily_feed_budget: getInt("cfgZhihuDailyFeedBudget", 0),
          daily_creator_budget: getInt("cfgZhihuDailyCreatorBudget", 0),
          daily_related_budget: getInt("cfgZhihuDailyRelatedBudget", 0),
          request_interval_seconds: getInt("cfgZhihuRequestInterval", 3),
          min_interval_minutes: getInt("cfgZhihuMinInterval", 3),
          ...popupSourceDateFieldsForUpdate("zhihu")
        },
        reddit: {
          enabled: checked("cfgRedditEnabled"),
          incremental_enabled: checked("cfgRedditIncremental"),
          backend: getVal("cfgRedditBackend") || "rdt",
          ...(getVal("cfgRedditCookie") ? { cookie: getVal("cfgRedditCookie") } : {}),
          source_modes: collectRedditSourceModes(),
          daily_search_budget: getInt("cfgRedditDailySearchBudget", 300),
          daily_hot_budget: getInt("cfgRedditDailyHotBudget", 300),
          daily_subreddit_budget: getInt("cfgRedditDailySubredditBudget", 300),
          daily_related_budget: getInt("cfgRedditDailyRelatedBudget", 300),
          request_interval_seconds: getInt("cfgRedditRequestInterval", 3),
          min_interval_minutes: getInt("cfgRedditMinInterval", 3),
          ...popupSourceDateFieldsForUpdate("reddit")
        },
        bangumi: {
          enabled: checked("cfgBangumiEnabled"),
          username: getVal("cfgBangumiUsername"),
          // Precedence: an explicit "clear token" checkbox sends access_token:""
          // (backend clears the stored token + rejection marker). Otherwise send
          // the token only when the user typed one; an empty field means "leave
          // the stored token unchanged", so omit the key rather than clobbering
          // the saved token with "".
          ...(checked("cfgBangumiClearToken")
            ? { access_token: "" }
            : (getVal("cfgBangumiAccessToken") || "") !== ""
              ? { access_token: getVal("cfgBangumiAccessToken") }
              : {}),
          subject_types: collectCheckedValues(BANGUMI_SUBJECT_TYPE_FIELDS, ["anime"]),
          source_modes: collectCheckedValues(BANGUMI_SOURCE_MODE_FIELDS, ["search"]),
          daily_search_budget: getInt("cfgBangumiDailySearchBudget", 300),
          daily_ranked_budget: getInt("cfgBangumiDailyRankedBudget", 100),
          daily_latest_budget: getInt("cfgBangumiDailyLatestBudget", 100),
          request_interval_seconds: getInt("cfgBangumiRequestInterval", 1),
          min_interval_minutes: getInt("cfgBangumiMinInterval", 3),
          bootstrap_limit: getInt("cfgBangumiBootstrapLimit", 300),
          ...popupSourceDateFieldsForUpdate("bangumi")
        },
        linuxdo: {
          enabled: checked("cfgLinuxdoEnabled"),
          incremental_enabled: checked("cfgLinuxdoIncremental"),
          source_modes: collectCheckedValues(LINUXDO_SOURCE_MODE_FIELDS, ["search"]),
          daily_search_budget: getInt("cfgLinuxdoDailySearchBudget", 0),
          daily_hot_budget: getInt("cfgLinuxdoDailyHotBudget", 0),
          daily_feed_budget: getInt("cfgLinuxdoDailyFeedBudget", 0),
          daily_creator_budget: getInt("cfgLinuxdoDailyCreatorBudget", 0),
          daily_related_budget: getInt("cfgLinuxdoDailyRelatedBudget", 0),
          request_interval_seconds: getInt("cfgLinuxdoRequestInterval", 3),
          min_interval_minutes: getInt("cfgLinuxdoMinInterval", 3),
          bootstrap_limit: getInt("cfgLinuxdoBootstrapLimit", 300),
          ...popupSourceDateFieldsForUpdate("linuxdo")
        },
        v2ex: {
          enabled: checked("cfgV2exEnabled"),
          incremental_enabled: checked("cfgV2exIncremental"),
          username: getVal("cfgV2exUsername"),
          ...(checked("cfgV2exClearToken")
            ? { access_token: "" }
            : (getVal("cfgV2exAccessToken") || "") !== ""
              ? { access_token: getVal("cfgV2exAccessToken") }
              : {}),
          source_modes: collectCheckedValues(V2EX_SOURCE_MODE_FIELDS, ["search"]),
          daily_search_budget: getInt("cfgV2exDailySearchBudget", 120),
          daily_node_budget: getInt("cfgV2exDailyNodeBudget", 180),
          daily_tab_budget: getInt("cfgV2exDailyTabBudget", 80),
          daily_hot_budget: getInt("cfgV2exDailyHotBudget", 40),
          daily_latest_budget: getInt("cfgV2exDailyLatestBudget", 40),
          request_interval_seconds: getInt("cfgV2exRequestInterval", 2),
          min_interval_minutes: getInt("cfgV2exMinInterval", 5),
          ...popupSourceDateFieldsForUpdate("v2ex")
        },
      },
      discovery: {
        ...(state.runtimeConfig?.discovery || {}),
        eval_scorer: getVal("cfgEvalScorer") || "llm",
        keyword_generation_mode: getVal("cfgKeywordGenerationMode"),
        candidate_eval_concurrency: getInt("cfgCandidateEvalConcurrency", 3),
        multimodal_evaluation_enabled: checked("cfgMultimodalEvaluationEnabled"),
        multimodal_batch_size: getInt("cfgMultimodalBatchSize", 8),
        multimodal_image_max_px: getInt("cfgMultimodalImageMaxPx", 384),
        multimodal_image_quality: getInt("cfgMultimodalImageQuality", 72),
        multimodal_image_timeout_seconds: getInt("cfgMultimodalImageTimeout", 6),
        visual_profile_enabled: checked("cfgVisualProfileEnabled"),
        keyframe_enabled: checked("cfgKeyframeEnabled"),
        keyframe_max_frames: getInt("cfgKeyframeMaxFrames", 4),
        keyframe_fetch_limit: getInt("cfgKeyframeFetchLimit", 50),
        danmaku_enabled: checked("cfgDanmakuEnabled"),
        danmaku_fetch_limit: getInt("cfgDanmakuFetchLimit", 50),
        danmaku_max_chars: getInt("cfgDanmakuMaxChars", 500),
      },
      scheduler: {
        enabled: !checked("cfgSchedulerEnabled"),
        pause_on_extension_disconnect: checked("cfgPauseOnDisconnect"),
        source_incremental_enabled: checked("cfgSourceIncrementalEnabled"),
        extension_disconnect_grace_seconds: getInt("cfgExtensionDisconnectGrace", 90),
        pool_target_count: getInt("cfgPoolTarget", 300),
        account_sync_interval_hours: getInt("cfgAccountSyncInterval", 6),
        refresh_check_interval_seconds: getInt("cfgRefreshCheckInterval", 60),
        signal_event_threshold: getInt("cfgSignalEventThreshold", 6),
        feedback_batch_threshold: getInt("cfgFeedbackBatchThreshold", 3),
        trending_refresh_minutes: getInt("cfgTrendingRefreshMinutes", 3),
        explore_refresh_minutes: getInt("cfgExploreRefreshMinutes", 3),
        discovery_limit: getInt("cfgDiscoveryLimit", 30),
        proactive_push_interval_seconds: getInt("cfgProactivePushInterval", 120),
        speculator_idle_interval_minutes: getInt("cfgSpeculatorIdleInterval", 30),
        pool_source_shares: {
          bilibili: getInt("cfgPoolShareBilibili", 5),
          xiaohongshu: getInt("cfgPoolShareXhs", 1),
          douyin: getInt("cfgPoolShareDouyin", 1),
          youtube: getInt("cfgPoolShareYoutube", 1),
          twitter: getInt("cfgPoolShareTwitter", 1),
          zhihu: getInt("cfgPoolShareZhihu", 1),
          reddit: getInt("cfgPoolShareReddit", 1),
          bangumi: getInt("cfgPoolShareBangumi", 1),
          linuxdo: getInt("cfgPoolShareLinuxdo", 1),
          v2ex: getInt("cfgPoolShareV2ex", 1),
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
      soul: {
        awareness_event_batch_size: getInt("cfgAwarenessEventBatchSize", 300),
        insight_note_batch_size: getInt("cfgInsightNoteBatchSize", 150),
        cognition_max_tokens: getInt("cfgCognitionMaxTokens", 32768)
      },
      saved_sync: {
        auto_sync_enabled: checked("cfgSavedAutoSync"),
      },
      storage: {
        db_path: getVal("cfgStorageDbPath"),
      },
      network: {
        mode: getVal("cfgNetworkProxyMode"),
        proxy: getVal("cfgNetworkProxy"),
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

  // One DOM convention for every "click, wait, read a verdict" strip in this
  // panel: tone in the dataset, verdict in the text. The LLM/embedding probes
  // and the per-source 测试连接 buttons share it instead of each keeping a
  // private copy — two independent copies of one rendering rule is exactly the
  // drift that left this codebase with two divergent source status maps.
  function setProbeStatus(statusEl, tone, text) {
    if (!statusEl) return;
    statusEl.dataset.tone = tone;
    statusEl.textContent = text;
  }

  function formatConfigProbeResult(result) {
    const ok = Boolean(result?.ok);
    const instance = result?.instance_id ? ` ${result.instance_id}` : "";
    const provider = result?.provider ? ` ${result.provider}` : "";
    const model = result?.model ? ` / ${result.model}` : "";
    const latency = Number.isFinite(Number(result?.latency_ms)) && Number(result.latency_ms) > 0
      ? ` (${Math.round(Number(result.latency_ms))}ms)`
      : "";
    const detail = result?.message || result?.error || (ok ? "服务可用" : "服务不可用");
    return `${ok ? "可用" : "不可用"}${instance}${provider}${model}${latency}: ${detail}`;
  }

  function renderProbeResult(statusEl, result) {
    if (!statusEl) return;
    setProbeStatus(statusEl, result?.ok ? "success" : "error", formatConfigProbeResult(result));
  }

  function renderProbePending(statusEl, label) {
    setProbeStatus(statusEl, "pending", `${label} 探测中...`);
  }

  // Three outcomes, three tones. The third one is the whole point: a dead proxy,
  // a closed browser, a throttled platform or YouTube (which needs no login at
  // all) all mean "could not tell", and showing that in red would send a user
  // off to delete a credential that works. The backend picks the outcome — this
  // map is a rendering detail, not a second opinion derived from
  // `auth.verification` (invariant I4).
  function renderVerifyResult(statusEl, result) {
    const view = SourceStatus.describeVerifyResult(result);
    setProbeStatus(statusEl, view.tone, view.text);
  }

  // ---- 平台源卡片：展开/折叠、停用态 --------------------------------------
  const SOURCE_CARD_ENABLE_IDS = {
    bilibili: "cfgBilibiliEnabled",
    xiaohongshu: "cfgXhsEnabled",
    douyin: "cfgDouyinEnabled",
    weibo: "cfgWeiboEnabled",
    youtube: "cfgYoutubeEnabled",
    twitter: "cfgTwitterEnabled",
    zhihu: "cfgZhihuEnabled",
    reddit: "cfgRedditEnabled",
    bangumi: "cfgBangumiEnabled",
    linuxdo: "cfgLinuxdoEnabled",
    v2ex: "cfgV2exEnabled",
  };

  function setSourceCardOpen(card, open) {
    if (!card) return;
    card.dataset.open = open ? "1" : "0";
    card.querySelector(".source-card-face")?.setAttribute("aria-expanded", open ? "true" : "false");
  }

  // A card whose source is switched off keeps its inputs in the DOM (the save
  // payload still reads them) but stops advertising them as actionable.
  function syncSourceCardEnabledState() {
    Object.entries(SOURCE_CARD_ENABLE_IDS).forEach(([key, inputId]) => {
      const card = document.querySelector(`[data-source-card="${key}"]`);
      if (!card) return;
      const input = document.getElementById(inputId);
      const on = input ? input.checked : true;
      const face = card.querySelector(".source-card-face");
      card.dataset.sourceOff = on ? "false" : "true";
      if (face instanceof HTMLElement) {
        face.tabIndex = on ? 0 : -1;
        face.setAttribute("aria-disabled", on ? "false" : "true");
      }
      if (!on) setSourceCardOpen(card, false);
    });
  }

  function initSourceCards() {
    const panel = document.getElementById("settingsPanelSources");
    if (!panel) return;

    panel.addEventListener("click", (event) => {
      // The enable checkbox and the verify button live on/inside the card but
      // must not double as a toggle for the body.
      if (event.target.closest(".source-card-body, input, label, button, select, textarea")) return;
      const face = event.target.closest(".source-card-face");
      const card = face?.closest("[data-source-card]");
      if (!card || card.dataset.sourceOff === "true") return;
      setSourceCardOpen(card, card.dataset.open !== "1");
    });

    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const face = event.target.closest(".source-card-face");
      if (!face || event.target !== face) return;
      event.preventDefault();
      const card = face.closest("[data-source-card]");
      if (!card || card.dataset.sourceOff === "true") return;
      setSourceCardOpen(card, card.dataset.open !== "1");
    });

    panel.addEventListener("change", (event) => {
      const id = event.target?.id;
      if (id && Object.values(SOURCE_CARD_ENABLE_IDS).includes(id)) syncSourceCardEnabledState();
    });

    syncSourceCardEnabledState();
  }

  // ---- 设置页吸底保存栏：未保存修改计数 ------------------------------------
  // Counts distinct touched fields, not events, so retyping one input does not
  // inflate the number.
  const settingsDirtyFields = new Set();
  let settingsSaveInFlight = false;

  function renderSettingsDirty() {
    const bar = document.getElementById("settingsSaveBar");
    const msg = document.getElementById("settingsSaveMsg");
    const count = settingsDirtyFields.size;
    if (bar) bar.dataset.dirty = count > 0 ? "true" : "false";
    if (msg) msg.textContent = count > 0 ? `已修改 ${count} 项，未保存` : "没有未保存的修改";
    saveBtn.disabled = settingsSaveInFlight || count === 0;
  }

  function markSettingsDirty(target) {
    const el = target instanceof Element ? target : null;
    settingsDirtyFields.add(el?.id || el?.name || `anon:${settingsDirtyFields.size}`);
    renderSettingsDirty();
  }

  function clearSettingsDirty() {
    settingsDirtyFields.clear();
    renderSettingsDirty();
  }

  function initSettingsDirtyTracking() {
    const root = document.getElementById("settingsOverlay") || document;
    ["input", "change"].forEach((type) => {
      root.addEventListener(type, (event) => {
        const el = event.target;
        if (!(el instanceof Element)) return;
        if (!el.closest(".settings-panel")) return;
        if (el.hasAttribute("readonly")) return;
        if (el.hasAttribute("data-settings-ignore-dirty")) return;
        markSettingsDirty(el);
      });
    });
    renderSettingsDirty();
  }

  // popup.js is a deferred module script, so the settings markup is already
  // parsed by the time this runs.
  initSourceCards();
  initSettingsDirtyTracking();
  document.getElementById("cfgBiliDatePreset")?.addEventListener("change", syncBiliDateFields);

  const sourceVerifyInFlight = new Set();

  async function runSourceVerify(button) {
    const slug = button?.closest("[data-source-card]")?.dataset?.sourceCard || "";
    if (!slug || sourceVerifyInFlight.has(slug)) return;
    const statusEl = button.parentElement?.querySelector(".source-verify-status");
    sourceVerifyInFlight.add(slug);
    button.disabled = true;
    renderProbePending(statusEl, "连接");
    let cooldown = 0;
    try {
      const result = await verifySource(slug);
      renderVerifyResult(statusEl, result);
      cooldown = Number(result?.retry_after_seconds) || 0;
      // Only a verification that actually moved the credential or the verdict
      // makes the status line above it stale; a refreshed timestamp does not.
      if (result?.changed) void renderSourcesStatus();
    } catch (err) {
      const view = SourceStatus.describeVerifyError(err);
      setProbeStatus(statusEl, view.tone, view.text);
    } finally {
      sourceVerifyInFlight.delete(slug);
      SourceStatus.startVerifyCooldown(button, cooldown);
    }
  }

  document.getElementById("settingsPanelSources")?.addEventListener("click", (event) => {
    const identityButton = event.target?.closest?.("#cfgV2exAcceptBrowserIdentity");
    if (identityButton instanceof HTMLButtonElement) {
      void acceptCurrentV2exBrowserIdentity(identityButton);
      return;
    }
    if (event.target?.closest?.("#cfgV2exRefreshIdentity")) {
      void renderV2exIdentity();
      return;
    }
    const button = event.target?.closest?.(".source-verify-btn");
    if (!(button instanceof HTMLButtonElement) || button.disabled) return;
    void runSourceVerify(button);
  });

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

  const addLlmInstanceBtn = document.getElementById("cfgAddLlmInstance");
  if (addLlmInstanceBtn instanceof HTMLButtonElement) {
    addLlmInstanceBtn.addEventListener("click", () => openLlmInstanceDialog());
  }
  const addLlmDefaultChainBtn = document.getElementById("cfgAddLlmDefaultChainItem");
  if (addLlmDefaultChainBtn instanceof HTMLButtonElement) {
    addLlmDefaultChainBtn.addEventListener("click", addLlmDefaultChainItem);
  }
  const llmInstanceProviderType = document.getElementById("cfgLlmInstanceProviderType");
  if (llmInstanceProviderType instanceof HTMLSelectElement) {
    llmInstanceProviderType.addEventListener("change", applyLlmProviderDefaults);
  }
  const refreshLlmInstanceModelsBtn = document.getElementById("cfgRefreshLlmInstanceModels");
  if (refreshLlmInstanceModelsBtn instanceof HTMLButtonElement) {
    refreshLlmInstanceModelsBtn.addEventListener("click", () => {
      void discoverLlmInstanceModels();
    });
  }
  for (const [id, eventName] of [
    ["cfgLlmInstanceBaseUrl", "input"],
    ["cfgLlmInstanceApiKey", "input"],
    ["cfgLlmInstanceAuthMode", "change"],
  ]) {
    document.getElementById(id)?.addEventListener(eventName, resetLlmModelDiscovery);
  }
  const saveLlmInstanceBtn = document.getElementById("cfgSaveLlmInstance");
  if (saveLlmInstanceBtn instanceof HTMLButtonElement) {
    saveLlmInstanceBtn.addEventListener("click", saveLlmInstanceDraft);
  }
  const llmInstanceDialog = document.getElementById("cfgLlmInstanceDialog");
  const closeLlmDialogBtn = document.getElementById("cfgCloseLlmInstanceDialog");
  const cancelLlmDialogBtn = document.getElementById("cfgCancelLlmInstance");
  closeLlmDialogBtn?.addEventListener("click", closeLlmInstanceDialog);
  cancelLlmDialogBtn?.addEventListener("click", closeLlmInstanceDialog);
  llmInstanceDialog?.querySelector("[data-close-llm-instance-dialog]")
    ?.addEventListener("click", closeLlmInstanceDialog);
  llmInstanceDialog?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeLlmInstanceDialog();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(llmInstanceDialog.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]',
    )).filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  const clearLlmInstanceKey = document.getElementById("cfgLlmInstanceClearApiKey");
  if (clearLlmInstanceKey instanceof HTMLInputElement) {
    clearLlmInstanceKey.addEventListener("change", () => {
      const keyInput = document.getElementById("cfgLlmInstanceApiKey");
      if (!(keyInput instanceof HTMLInputElement)) return;
      if (clearLlmInstanceKey.checked) keyInput.value = "";
      keyInput.disabled = clearLlmInstanceKey.checked;
    });
  }

  const openDesktopModelsBtn = document.getElementById("cfgOpenDesktopModels");
  if (openDesktopModelsBtn instanceof HTMLButtonElement) {
    openDesktopModelsBtn.addEventListener("click", async () => {
      openDesktopModelsBtn.disabled = true;
      try {
        const origin = await getBackendOrigin();
        openMobileWebUrl(`${origin}/web?settings=models`);
      } finally {
        openDesktopModelsBtn.disabled = false;
      }
    });
  }

  const probeLlmChainBtn = document.getElementById("cfgProbeLlmChain");
  const probeLlmChainStatus = document.getElementById("cfgProbeLlmChainStatus");
  if (probeLlmChainBtn instanceof HTMLButtonElement) {
    probeLlmChainBtn.addEventListener("click", async () => {
      probeLlmChainBtn.disabled = true;
      renderProbePending(probeLlmChainStatus, "默认调用链");
      try {
        const result = await probeConfigService("llm_chain", collectForm());
        renderProbeResult(probeLlmChainStatus, result);
      } catch (err) {
        renderProbeResult(probeLlmChainStatus, {
          ok: false,
          error: err?.message || "默认调用链探测失败",
        });
      } finally {
        probeLlmChainBtn.disabled = false;
      }
    });
  }

  async function runLlmFallbackConfigProbe(button, statusEl) {
    if (!button) return;
    button.disabled = true;
    renderProbePending(statusEl, "备选 Provider");
    try {
      const result = await probeConfigService("llm_fallback", collectForm());
      renderProbeResult(statusEl, result);
    } catch (err) {
      renderProbeResult(statusEl, {
        ok: false,
        error: err?.message || "备选 Provider 探测失败",
      });
    } finally {
      button.disabled = false;
    }
  }

  const probeLlmFallbackBtn = document.getElementById("cfgProbeLlmFallback");
  const probeLlmFallbackStatus = document.getElementById("cfgProbeLlmFallbackStatus");
  if (probeLlmFallbackBtn instanceof HTMLButtonElement) {
    probeLlmFallbackBtn.addEventListener("click", () => {
      void runLlmFallbackConfigProbe(probeLlmFallbackBtn, probeLlmFallbackStatus);
    });
  }

  const probeEmbeddingBtn = document.getElementById("cfgProbeEmbedding");
  const probeEmbeddingStatus = document.getElementById("cfgProbeEmbeddingStatus");
  if (probeEmbeddingBtn instanceof HTMLButtonElement) {
    probeEmbeddingBtn.addEventListener("click", () => {
      void runEmbeddingConfigProbe(probeEmbeddingBtn, probeEmbeddingStatus);
    });
  }

  async function runNetworkProxyConfigProbe(button, statusEl) {
    if (!button) return;
    button.disabled = true;
    renderProbePending(statusEl, "代理");
    try {
      const proxy = getVal("cfgNetworkProxy");
      const mode = getVal("cfgNetworkProxyMode");
      const result = await probeConfigService("network_proxy", { network: { mode, proxy } });
      renderProbeResult(statusEl, result);
    } catch (err) {
      renderProbeResult(statusEl, {
        ok: false,
        error: err?.message || "代理探测失败",
      });
    } finally {
      button.disabled = false;
    }
  }

  const probeNetworkProxyBtn = document.getElementById("cfgProbeNetworkProxy");
  const probeNetworkProxyStatus = document.getElementById("cfgProbeNetworkProxyStatus");
  if (probeNetworkProxyBtn instanceof HTMLButtonElement) {
    probeNetworkProxyBtn.addEventListener("click", () => {
      void runNetworkProxyConfigProbe(probeNetworkProxyBtn, probeNetworkProxyStatus);
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
      } catch (error) {
        const details = error?.details;
        if (details && typeof details === "object") {
          renderBackendUpdateStatus(details);
        }
        const reason = details?.reason || error?.message || "未知原因";
        showToast(`后端更新未能开始：${formatBackendUpdateReason(reason) || reason}`, "error");
      } finally {
        await loadBackendUpdateStatus();
        backendApplyBtn.disabled = false;
      }
    });
  }

  const savedAutoSync = document.getElementById("cfgSavedAutoSync");
  const savedAutoSyncStatus = document.getElementById("cfgSavedAutoSyncStatus");
  if (savedAutoSync instanceof HTMLInputElement) {
    savedAutoSync.addEventListener("change", () => {
      if (!savedAutoSync.checked || savedAutoSync.dataset.confirmed === "true") return;
      const warning = "开启后，在 OpenBiliClaw 点击收藏或稍后再看会修改对应平台账号中的收藏、书签、Saved、播放列表或稍后观看。";
      if (!window.confirm(warning)) {
        savedAutoSync.checked = false;
        savedAutoSync.dataset.confirmed = "false";
        if (savedAutoSyncStatus) savedAutoSyncStatus.textContent = "已取消，自动同步仍为关闭。";
        return;
      }
      savedAutoSync.dataset.confirmed = "true";
      if (savedAutoSyncStatus) savedAutoSyncStatus.textContent = "已确认；保存配置后开启。";
    });
  }

  // The degraded empty state's "去设置修复" button routes through the gear so
  // the overlay opens with the same banners / degraded save mode as always.
  document.getElementById("emptyAction")?.addEventListener("click", () => gearBtn.click());

  gearBtn.addEventListener("click", async () => {
    closeLlmInstanceDialog();
    openPopupOverlay(overlay, { trigger: gearBtn, initialFocus: backBtn });
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
    void refreshReinitStatus();
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
    closeLlmInstanceDialog();
    closePopupOverlay(overlay);
  });
  bindPopupOverlayKeyboard(overlay, () => backBtn.click());

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
            weibo: checked("cfgWeiboEnabled"),
            youtube: checked("cfgYoutubeEnabled"),
            twitter: checked("cfgTwitterEnabled"),
            zhihu: checked("cfgZhihuEnabled"),
            reddit: checked("cfgRedditEnabled"),
            bangumi: checked("cfgBangumiEnabled"),
            linuxdo: checked("cfgLinuxdoEnabled"),
            v2ex: checked("cfgV2exEnabled"),
          },
          configured_shares: {
            bilibili: getInt("cfgPoolShareBilibili", 5),
            xiaohongshu: getInt("cfgPoolShareXhs", 1),
            douyin: getInt("cfgPoolShareDouyin", 1),
            weibo: getInt("cfgPoolShareWeibo", 1),
            youtube: getInt("cfgPoolShareYoutube", 1),
            twitter: getInt("cfgPoolShareTwitter", 1),
            zhihu: getInt("cfgPoolShareZhihu", 1),
            reddit: getInt("cfgPoolShareReddit", 1),
            bangumi: getInt("cfgPoolShareBangumi", 1),
            linuxdo: getInt("cfgPoolShareLinuxdo", 1),
            v2ex: getInt("cfgPoolShareV2ex", 1),
          },
        });
        const shares = suggestion?.suggested_shares || {};
        if (shares.bilibili !== undefined) setVal("cfgPoolShareBilibili", shares.bilibili);
        if (shares.xiaohongshu !== undefined) setVal("cfgPoolShareXhs", shares.xiaohongshu);
        if (shares.douyin !== undefined) setVal("cfgPoolShareDouyin", shares.douyin);
        if (shares.weibo !== undefined) setVal("cfgPoolShareWeibo", shares.weibo);
        if (shares.youtube !== undefined) setVal("cfgPoolShareYoutube", shares.youtube);
        if (shares.twitter !== undefined) setVal("cfgPoolShareTwitter", shares.twitter);
        if (shares.zhihu !== undefined) setVal("cfgPoolShareZhihu", shares.zhihu);
        if (shares.reddit !== undefined) setVal("cfgPoolShareReddit", shares.reddit);
        if (shares.bangumi !== undefined) setVal("cfgPoolShareBangumi", shares.bangumi);
        if (shares.linuxdo !== undefined) setVal("cfgPoolShareLinuxdo", shares.linuxdo);
        if (shares.v2ex !== undefined) setVal("cfgPoolShareV2ex", shares.v2ex);
        markSettingsDirty(suggestBtn);
        showToast("已按已有信号填入建议比例，保存后生效。", "success");
      } catch (err) {
        showToast(`生成建议失败: ${err.message}`, "error");
      } finally {
        suggestBtn.disabled = false;
      }
    });
  }

  // ── 重新初始化 / 重建画像 (gui-init §4) ─────────────────────────
  // The recommend-tab CTA is first-run-only; once initialized the only
  // re-init entry lives in settings and calls POST /api/init {force:true}.
  const reinitBtn = document.getElementById("cfgReinitBtn");
  const reinitStatusEl = document.getElementById("cfgReinitStatus");

  async function refreshReinitStatus() {
    let status = null;
    try {
      status = await fetchInitStatus();
    } catch {
      if (reinitStatusEl) reinitStatusEl.textContent = "无法读取初始化状态（后端不可达）。";
      if (reinitBtn) reinitBtn.disabled = false;
      return;
    }
    if (reinitBtn) reinitBtn.disabled = Boolean(status?.running);
    if (!reinitStatusEl) return;
    if (status?.running) {
      reinitStatusEl.textContent =
        `初始化进行中（阶段 ${status.current_stage || "?"}/${status.total_stages || 4}）。` +
        "请等待本轮完成后再重新初始化。";
    } else if (status?.initialized) {
      reinitStatusEl.textContent = "系统已初始化。重新初始化会重新拉取数据并重建画像，现有事件与收藏保留。";
    } else {
      reinitStatusEl.textContent = "系统尚未初始化完成；正常流程请到「推荐」页点击开始初始化。";
    }
  }

  if (reinitBtn) {
    reinitBtn.addEventListener("click", async () => {
      let status = null;
      try {
        status = await fetchInitStatus();
      } catch {
        if (reinitStatusEl) reinitStatusEl.textContent = "无法读取初始化状态（后端不可达）。";
        return;
      }
      if (status?.running) {
        if (reinitStatusEl) reinitStatusEl.textContent = "初始化正在进行中，请等待完成后再重新初始化。";
        return;
      }
      if (!status?.initialized) {
        if (reinitStatusEl) reinitStatusEl.textContent = "系统尚未初始化完成；请先到「推荐」页完成初始化。";
        return;
      }
      const resetCognition = document.getElementById("cfgReinitResetCognition")?.checked === true;
      if (!window.confirm(
        "将重新拉取所选平台的数据、重建完整画像并补足首轮发现池。现有推荐池会按新画像清空重建；现有事件、收藏、对话历史与手动编辑保留。重新初始化前会自动创建备份（数据库 + 画像/认知层）到 data/backups/。并消耗较多 AI 调用。继续吗？" +
        (resetCognition
          ? "\n\n已勾选「同时清空旧认知观察与洞察」：旧的 LLM 观察笔记与洞察将被删除（已包含在自动备份中），本轮重新生成。"
          : "")
      )) {
        return;
      }
      reinitBtn.disabled = true;
      if (reinitStatusEl) reinitStatusEl.textContent = "正在启动重新初始化…";
      try {
        const payload = { force: true };
        if (resetCognition) payload.reset_cognition = true;
        const reinitLlmConcurrency = Number(document.getElementById("cfgReinitLlmConcurrency")?.value || 3);
        if (Number.isFinite(reinitLlmConcurrency) && reinitLlmConcurrency >= 1 && reinitLlmConcurrency <= 16) {
          payload.llm_concurrency = reinitLlmConcurrency;
        }
        await startInit(payload);
        showToast("重新初始化已开始，正在重新拉取数据并重建画像", "success");
        closePopupOverlay(overlay);
        setActiveTab("recommend");
        renderInitProgress({ running: true, current_stage: 1, total_stages: 4, stages: [] });
        _startInitProgressPoll();
      } catch (err) {
        if (reinitStatusEl) {
          reinitStatusEl.textContent =
            describeInitStartError(err) || err?.message || "重新初始化没能启动，请稍后重试。";
        }
        reinitBtn.disabled = false;
      }
    });
  }

  saveBtn.addEventListener("click", async () => {
    if (settingsSaveInFlight || settingsDirtyFields.size === 0) {
      renderSettingsDirty();
      return;
    }
    settingsSaveInFlight = true;
    renderSettingsDirty();
    saveBtn.textContent = "保存中...";
    toast.hidden = true;
    try {
      // Backend endpoint lives in chrome.storage, not the backend's
      // config.toml — persist it locally first so the subsequent
      // updateConfig() PUT targets the new origin.
      let endpointChanged = false;
      let newEndpointLabel = null;
      const schemeRaw = backendSchemeInput instanceof HTMLSelectElement
        ? backendSchemeInput.value : "http";
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
        const next = await updateBackendEndpoint(schemeRaw, hostRaw, portRaw || "8420");
        newEndpointLabel = `${next.scheme}://${next.host}:${next.port}`;
        endpointChanged = next.scheme !== previous.scheme
          || next.host !== previous.host || next.port !== previous.port;
      }

      const data = collectForm();
      try {
        const result = await updateConfig(data);
        if (result.config) {
          populateForm(result.config);
        } else {
          clearSettingsDirty();
        }
        const queued = result.apply_state === "queued";
        const tone = result.restart_required || queued
          ? "warning"
          : result.reloaded ? "success" : "warning";
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
        // new port these will retry on the fixed liveness cadence and the popup
        // status will stay reconnecting or flip to offline — exactly the signal
        // the user needs to remember to start the daemon with --port.
        await clearPopupSession();
        connectRuntimeStream();
        const online = await checkBackendStatus();
        if (online) {
          backendConnectionCoordinator.markHttpReachable();
        } else {
          backendConnectionCoordinator.markOffline();
        }
      }
    } catch (err) {
      if (err?.message === "https_required") {
        showToast("公网后端必须使用 HTTPS。", "error");
      } else if (err?.message === "backend_permission_denied") {
        showToast("未授予该后端地址的访问权限，地址未保存。", "error");
      } else if (err?.message === "invalid_backend_scheme") {
        showToast("后端协议无效。", "error");
      } else if (BANGUMI_SAVE_ERROR_MESSAGES[err?.details?.error]) {
        // Config PUT rejects a bad/expired Bangumi token live via /v0/me.
        showToast(
          err.details.message || BANGUMI_SAVE_ERROR_MESSAGES[err.details.error],
          "error",
        );
      } else if (!renderStructuredConfigError(err)) {
        showToast(`保存失败: ${err.message}`, "error");
      }
    } finally {
      settingsSaveInFlight = false;
      setSaveButtonMode(state.runtimeConfig?.degraded ? "degraded" : "");
      renderSettingsDirty();
    }
  });
}

// Session-scoped dismissal so we don't nag on every popup open after the
// user explicitly closes the banner. Re-appears next session if embedding
// is still disabled.
const EMBEDDING_BANNER_DISMISS_KEY = "embeddingBannerDismissed";

// Repair polling: a bge-m3 pull is ~568MB, so allow up to 20 minutes. The
// pull continues server-side even if the panel closes; the banner's
// auto-refresh clears it once embedding recovers.
const EMBEDDING_REPAIR_POLL_MS = 1_500;
const EMBEDDING_REPAIR_POLL_LIMIT = Math.ceil((20 * 60 * 1_000) / EMBEDDING_REPAIR_POLL_MS);

function formatRepairProgress(repair) {
  if (repair && repair.total > 0) {
    const pct = Math.min(99, Math.round((repair.completed / repair.total) * 100));
    return `拉取中 ${pct}%`;
  }
  return "拉取中…";
}

// Wait for the server-side pull to finish, mirroring progress onto the
// button. Returns the final repair state (or null if the backend vanished).
async function waitForEmbeddingRepair(enableBtn) {
  for (let i = 0; i < EMBEDDING_REPAIR_POLL_LIMIT; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, EMBEDDING_REPAIR_POLL_MS));
    const repair = await fetchEmbeddingRepairStatus();
    if (!repair) return null;
    if (repair.done) return repair;
    if (enableBtn) enableBtn.textContent = formatRepairProgress(repair);
  }
  return { done: false, ok: false, error: "拉取超时" };
}

async function enableLocalOllamaEmbedding(enableBtn) {
  const failBtn = (label) => {
    if (enableBtn) {
      enableBtn.disabled = false;
      enableBtn.textContent = label;
    }
  };
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
          // Don't hardcode base_url: Docker deployments use
          // http://ollama:11434/v1 (sidecar), local deployments use
          // the default http://localhost:11434/v1.  Omitting it
          // preserves whatever the backend already has configured.
        },
      },
    });
    // Re-check: hot-reload rebuilds the embedding service in-process and
    // /api/health probes it live, so embedding_ready only flips true once
    // Ollama actually serves a vector. Don't claim success on a config
    // write alone.
    let health = await fetchHealth();
    const banner = document.getElementById("embeddingBanner");
    if (health && health.embedding_ready) {
      if (banner) banner.hidden = true;
      setHint("已启用本地 Ollama 语义去重，重复内容会少很多。", "success");
      return;
    }
    // Not ready → let the backend classify the cause and, when the fix is
    // "pull the model", do it server-side with real progress (v0.3.155+).
    const kicked = await startEmbeddingRepair();
    if (kicked.status === 409 && kicked.error === "not_running") {
      failBtn("重试");
      setHint(kicked.detail || "Ollama 没有在运行，请先启动 Ollama（或运行 `ollama serve`）。", "error");
      return;
    }
    if (kicked.status === 409 && kicked.error === "unsupported_provider") {
      failBtn("重试");
      setHint(kicked.detail || "一键修复只支持本地 Ollama embedding。", "error");
      return;
    }
    if (kicked.status === 409 && kicked.error !== "already_running" && kicked.detail) {
      failBtn("重试");
      setHint(kicked.detail, "error");
      return;
    }
    if (kicked.status === 403) {
      failBtn("重试");
      setHint("只能在本机操作 embedding 修复；请在装有后端的电脑上打开扩展。", "error");
      return;
    }
    if (
      kicked.status === 202 ||
      kicked.already_ok ||
      (kicked.status === 409 && kicked.error === "already_running")
    ) {
      if (!kicked.already_ok) {
        setHint("正在拉取 bge-m3（约 568MB）。关闭面板下载也会继续。");
        const repair = await waitForEmbeddingRepair(enableBtn);
        if (repair && repair.done && !repair.ok) {
          failBtn("重试");
          setHint(`bge-m3 拉取失败：${repair.error || "未知错误"}`, "error");
          return;
        }
      }
      // Health TTL is short (3s client / server-side cache expired on
      // success), so one more read reflects the repaired state.
      health = await fetchHealth();
      if (health && health.embedding_ready) {
        if (banner) banner.hidden = true;
        setHint("已启用本地 Ollama 语义去重，重复内容会少很多。", "success");
        return;
      }
      failBtn("重试");
      setHint("模型已就绪但探测还没通过，稍等几秒后重试。", "error");
      return;
    }
    // Older backend without /api/embedding/repair (404) or unreachable (0).
    failBtn("重试");
    setHint(
      "配置已写入，但 Ollama 还没就绪。请确认已运行 `ollama serve` 并 `ollama pull bge-m3`。",
      "error",
    );
  } catch {
    failBtn("重试");
    setHint("启用失败，请检查后端连接后重试。", "error");
  }
}

async function maybeShowEmbeddingBanner() {
  const banner = document.getElementById("embeddingBanner");
  if (!banner) return;
  if (sessionStorage.getItem(EMBEDDING_BANNER_DISMISS_KEY) === "1") return;
  const health = await fetchHealth();
  if (!shouldShowEmbeddingBanner(health, state.runtimeStatus)) {
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
  const requestedLibraryTab = params.get("section") || params.get("library") || "";
  state.delightHighlightBvid = params.get("delight")?.trim() || "";
  bindTabs();
  bindContentHistory();
  bindProfileHistoryLoading();
  initRecommendationAutoLoadIntent();
  bindRefreshButton();
  bindActivityToggle();
  bindChat();
  bindDialogueConfirmations();
  bindOpenWeb();
  bindMobileQr();
  bindSettings();
  bindStarButton();

  bindMessages();
  setActiveTab(
    ["recommend", "library", "watchLater", "favorites", "history", "profile", "chat"].includes(requestedTab)
      ? requestedTab
      : "recommend",
    { libraryTab: requestedLibraryTab },
  );
  setHint("先看看本地后端连上没。");
  await initializeRecommendations();
  void maybeShowEmbeddingBanner();
  // Re-check when the panel regains visibility/focus so a stale "semantic
  // dedup off" banner clears itself once embedding recovers — the one-shot
  // call above never re-runs while a side panel stays open.
  installEmbeddingBannerAutoRefresh(maybeShowEmbeddingBanner);
  await hydrateChatHistory();
  await refreshPendingConfirmations();
  startChatHistorySync();
  // Always fetch profile-summary on startup so the messages inbox is
  // populated regardless of which tab the user lands on.  Without this
  // the inbox stays empty until the user manually opens the profile
  // tab (the place where loadProfileSummary historically fired).
  void loadProfileSummary();
  connectRuntimeStream();
}

void initializePopup();
