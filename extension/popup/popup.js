import {
  buildFeedbackPayload,
  buildVideoUrl,
  getConnectionBadgeState,
  getHintBannerState,
  getRealtimePoolStatusSummary,
  getPoolStatusSummary,
  getPopupState,
  getTabButtonState,
  mergeRuntimeStatusEvent,
  normalizeProfileSummary,
  validateCommentInput,
  shouldFetchProfileSummary,
} from "./popup-helpers.js";
import { createRuntimeStreamClient } from "./popup-stream.js";
import {
  checkBackendStatus,
  fetchProfileSummary,
  fetchRecommendations,
  fetchRuntimeStatus,
  reshuffleRecommendations,
  refreshRecommendations,
  sendChatMessage,
  submitFeedback,
} from "./popup-api.js";

const state = {
  activeTab: "recommend",
  online: false,
  recommendations: [],
  profile: null,
  profileLoaded: false,
  runtimeStatus: null,
  runtimeEvent: null,
};

const elements = {
  statusBadge: document.getElementById("statusBadge"),
  statusDot: document.getElementById("statusDot"),
  statusLabel: document.getElementById("statusLabel"),
  footer: document.getElementById("footerHintBar"),
  hintText: document.getElementById("hintText"),
  emptyState: document.getElementById("emptyState"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyText: document.getElementById("emptyText"),
  list: document.getElementById("recommendationList"),
  refreshRecommendationsButton: document.getElementById("refreshRecommendationsButton"),
  refreshRecommendationsStatus: document.getElementById("refreshRecommendationsStatus"),
  poolStatus: document.getElementById("poolStatus"),
  poolAvailable: document.getElementById("poolAvailable"),
  poolReplenished: document.getElementById("poolReplenished"),
  poolTopics: document.getElementById("poolTopics"),
  tabRecommend: document.getElementById("tabRecommend"),
  tabProfile: document.getElementById("tabProfile"),
  tabChat: document.getElementById("tabChat"),
  viewRecommend: document.getElementById("viewRecommend"),
  viewProfile: document.getElementById("viewProfile"),
  viewChat: document.getElementById("viewChat"),
  profileEmpty: document.getElementById("profileEmpty"),
  profileEmptyTitle: document.getElementById("profileEmptyTitle"),
  profileEmptyText: document.getElementById("profileEmptyText"),
  profileCard: document.getElementById("profileCard"),
  profilePortrait: document.getElementById("profilePortrait"),
  profileTraits: document.getElementById("profileTraits"),
  profileNeeds: document.getElementById("profileNeeds"),
  profileInterests: document.getElementById("profileInterests"),
  profileRecentMemory: document.getElementById("profileRecentMemory"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  chatSendButton: document.getElementById("chatSendButton"),
};

function setRefreshButtonState(loading, message = "") {
  if (elements.refreshRecommendationsButton instanceof HTMLButtonElement) {
    elements.refreshRecommendationsButton.disabled = loading;
    elements.refreshRecommendationsButton.textContent = loading ? "正在换一批…" : "换一批";
  }
  if (elements.refreshRecommendationsStatus instanceof HTMLElement) {
    elements.refreshRecommendationsStatus.hidden = !message;
    elements.refreshRecommendationsStatus.textContent = message;
  }
}

function setHint(message, tone = "info") {
  if (elements.hintText instanceof HTMLElement) {
    elements.hintText.textContent = message;
  }
  if (elements.footer instanceof HTMLElement) {
    elements.footer.dataset.tone = getHintBannerState(tone).tone;
  }
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

function setActiveTab(tabName) {
  state.activeTab = tabName;

  const tabs = [
    ["recommend", elements.tabRecommend, elements.viewRecommend],
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
}

function hideRecommendationEmptyState() {
  if (elements.emptyState instanceof HTMLElement) {
    elements.emptyState.hidden = true;
  }
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

  const summary = getRealtimePoolStatusSummary(runtimeStatus, state.runtimeEvent);
  if (summary == null) {
    elements.poolStatus.hidden = true;
    return;
  }

  elements.poolStatus.hidden = false;
  elements.poolAvailable.textContent = summary.available;
  elements.poolReplenished.textContent = summary.replenished;
  elements.poolTopics.textContent = summary.topics;
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

function connectRuntimeStream() {
  const client = createRuntimeStreamClient({
    onEvent(event) {
      state.runtimeEvent = event;
      state.runtimeStatus = mergeRuntimeStatusEvent(state.runtimeStatus, event);
      renderPoolStatus(state.runtimeStatus);
      if (typeof event?.message === "string" && event.message.trim()) {
        setHint(event.message, getRuntimeEventTone(event));
      }
    },
  });
  client.connect();
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
    elements.profileEmptyText.textContent = "先跑一遍 openbiliclaw init，再回来看看。";
    return;
  }

  elements.profileEmpty.hidden = true;
  elements.profileCard.hidden = false;
  elements.profilePortrait.textContent = summary.personality_portrait;
  renderChipList(elements.profileTraits, summary.core_traits, "这部分还在慢慢补");
  renderChipList(elements.profileNeeds, summary.deep_needs, "这块还要再多看一点");
  renderChipList(elements.profileInterests, summary.top_interests, "再刷一阵，这里会更准");
  renderChipList(
    elements.profileRecentMemory,
    summary.recent_cognition_updates,
    "阿B 还在继续观察，过一阵这里会更具体。",
  );
}

function appendChatMessage(role, content) {
  if (!(elements.chatMessages instanceof HTMLElement)) {
    return;
  }
  const item = document.createElement("div");
  item.className = `chat-message${role === "你" ? " user" : ""}`;

  const label = document.createElement("span");
  label.className = "chat-role";
  label.textContent = role;

  const text = document.createElement("p");
  text.className = "chat-content";
  text.textContent = content;

  item.append(label, text);
  elements.chatMessages.append(item);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

function setFeedbackStatus(statusLine, message) {
  statusLine.textContent = message;
  statusLine.hidden = !message;
}

async function openRecommendation(bvid) {
  if (!bvid) {
    setHint("这条卡片还没挂上 BV 号，稍后再试。", "error");
    return;
  }
  await chrome.tabs.create({ url: buildVideoUrl(bvid) });
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

function createCommentComposer(item, statusLine) {
  const wrapper = document.createElement("div");
  wrapper.className = "comment-composer";
  wrapper.hidden = true;

  const input = document.createElement("textarea");
  input.className = "comment-input";
  input.rows = 3;
  input.placeholder = "写一句你为什么想看，或者为什么不想看";

  const submit = createActionButton("发出去", "action-button action-primary", async () => {
    const validation = validateCommentInput(input.value);
    if (!validation.valid) {
      setHint(validation.message, "error");
      input.focus();
      return;
    }
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "comment", input.value));
      setHint("这句记下了。", "success");
      setFeedbackStatus(statusLine, "记下了，这句会影响后面的推荐。");
      wrapper.hidden = true;
      input.value = "";
      void refreshProfileSummaryAfterInteraction();
    } catch {
      setHint("这句没发出去，先看看本地后端是不是开着。", "error");
    }
  });

  wrapper.append(input, submit);
  return { wrapper, input };
}

function renderRecommendations(items) {
  if (!(elements.list instanceof HTMLElement)) {
    return;
  }
  elements.list.replaceChildren();

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "recommendation-card";

    const preview = document.createElement("button");
    preview.className = "recommendation-preview";
    preview.type = "button";
    preview.addEventListener("click", () => {
      void openRecommendation(item.bvid);
    });

    const cover = document.createElement("div");
    cover.className = "recommendation-cover";
    if (item.cover_url) {
      const image = document.createElement("img");
      image.src = item.cover_url;
      image.alt = `${item.title} 的封面`;
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => {
        cover.replaceChildren();
        cover.classList.add("is-fallback");
        cover.textContent = "封面加载慢了一下";
      });
      cover.append(image);
    } else {
      cover.classList.add("is-fallback");
      cover.textContent = "先看标题也行";
    }

    const content = document.createElement("div");
    content.className = "recommendation-content";

    const top = document.createElement("div");
    top.className = "recommendation-top";

    const badge = document.createElement("span");
    badge.className = "topic-badge";
    badge.textContent = item.topic_label || "这条给你留着";

    const title = document.createElement("h3");
    title.className = "recommendation-title";
    title.textContent = item.title;

    const stateBadge = document.createElement("span");
    stateBadge.className = `recommendation-state${item.presented ? " is-presented" : ""}`;
    stateBadge.textContent = item.presented ? "你应该刷到过" : "刚给你翻出来";

    const meta = document.createElement("p");
    meta.className = "recommendation-meta";
    meta.textContent = `这位 UP：${item.up_name}`;

    top.append(badge, stateBadge);

    const expression = document.createElement("p");
    expression.className = "recommendation-expression";
    expression.textContent = item.expression;

    content.append(top, title, expression, meta);
    preview.append(cover, content);

    const feedbackStatus = document.createElement("p");
    feedbackStatus.className = "feedback-status";
    setFeedbackStatus(feedbackStatus, item.presented ? "这条你应该已经眼熟了。" : "");

    const actions = document.createElement("div");
    actions.className = "recommendation-actions";
    const composer = createCommentComposer(item, feedbackStatus);
    actions.append(
      createActionButton("去看看", "action-button action-primary", () => {
        void openRecommendation(item.bvid);
      }),
      createActionButton("多来点", "action-button action-secondary", async () => {
        try {
          await submitFeedback(buildFeedbackPayload(item.id, "like"));
          setHint("记下了，这类可以多来点。", "success");
          setFeedbackStatus(feedbackStatus, "记下了，这类内容会多给你一点。");
          void refreshProfileSummaryAfterInteraction();
        } catch {
          setHint("这条反馈没记上，先看看本地后端是不是开着。", "error");
        }
      }),
      createActionButton("少来点", "action-button action-secondary", async () => {
        try {
          await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
          setHint("记下了，这路子先少来点。", "success");
          setFeedbackStatus(feedbackStatus, "记下了，这个方向先往后放。");
          void refreshProfileSummaryAfterInteraction();
        } catch {
          setHint("这条反馈没记上，先看看本地后端是不是开着。", "error");
        }
      }),
      createActionButton("说说原因", "action-button action-secondary", () => {
        composer.wrapper.hidden = !composer.wrapper.hidden;
        if (!composer.wrapper.hidden) {
          composer.input.focus();
        }
      }),
    );

    card.append(preview, actions, composer.wrapper, feedbackStatus);
    elements.list.append(card);
  }
}

function renderRecommendationState(stateShape) {
  if (stateShape.kind === "ready") {
    hideRecommendationEmptyState();
    renderRecommendations(stateShape.items);
    const unreadCount = Number(stateShape.runtime?.unread_count ?? 0);
    if (unreadCount > 0) {
      setHint(`刚补进 ${unreadCount} 条还没看过的新内容，想看就点，不想看就直说。`, "success");
    } else {
      setHint("想看就点，不想看就直说。");
    }
    return;
  }

  if (elements.list instanceof HTMLElement) {
    elements.list.replaceChildren();
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
    showRecommendationEmptyState("还没完成初始化", stateShape.message);
    setHint("先跑一遍 openbiliclaw init，把画像和候选池攒起来。");
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
      renderProfileSummary(state.profile);
    }
    return;
  }

  try {
    const summary = await fetchProfileSummary();
    state.profile = normalizeProfileSummary(summary);
  } catch {
    state.profile = normalizeProfileSummary({ initialized: false });
  }
  state.profileLoaded = true;
  renderProfileSummary(state.profile);
}

async function refreshProfileSummaryAfterInteraction() {
  if (!state.online) {
    return;
  }
  if (!state.profileLoaded && state.activeTab !== "profile") {
    return;
  }
  await loadProfileSummary({ force: true });
}

async function initializeRecommendations() {
  const online = await checkBackendStatus();
  state.online = online;
  setStatus(online);

  if (!online) {
    state.runtimeStatus = null;
    renderRecommendationState(getPopupState({ online, items: [], runtimeStatus: null }));
    renderProfileSummary(normalizeProfileSummary({ initialized: false }));
    return;
  }

  const [runtimeResult, recommendationResult] = await Promise.allSettled([
    fetchRuntimeStatus(),
    fetchRecommendations(),
  ]);

  state.runtimeStatus = runtimeResult.status === "fulfilled" ? runtimeResult.value : null;
  renderPoolStatus(state.runtimeStatus);

  if (recommendationResult.status === "fulfilled") {
    state.recommendations = recommendationResult.value;
    renderRecommendationState(
      getPopupState({
        online,
        items: state.recommendations,
        runtimeStatus: state.runtimeStatus,
      }),
    );
    return;
  }

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
  setRefreshButtonState(true, "正在给你换一批…");
  try {
    const result = await reshuffleRecommendations();
    if (!Array.isArray(result.items)) {
      setHint("先执行 openbiliclaw init，再回来刷新。", "error");
      return;
    }
    state.recommendations = result.items;
    state.runtimeStatus = await fetchRuntimeStatus().catch(() => state.runtimeStatus);
    renderPoolStatus(state.runtimeStatus);
    renderRecommendationState(
      getPopupState({
        online: state.online,
        items: state.recommendations,
        runtimeStatus: state.runtimeStatus,
      }),
    );
    setHint(
      result.items.length > 0 ? "先给你换了一批新的，后台还在继续补货。" : "池子里这会儿还没刷出新的，稍后再试。",
      result.items.length > 0 ? "success" : "error",
    );
    void refreshRecommendations().catch(() => undefined);
  } catch {
    setHint("这次没换出来新的，稍后再试。", "error");
  } finally {
    setRefreshButtonState(false);
  }
}

function bindTabs() {
  const bindings = [
    [elements.tabRecommend, "recommend"],
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

function bindRefreshButton() {
  if (!(elements.refreshRecommendationsButton instanceof HTMLButtonElement)) {
    return;
  }
  elements.refreshRecommendationsButton.addEventListener("click", () => {
    void handleManualRefresh();
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

  elements.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = elements.chatInput.value.trim();
    if (!message) {
      setHint("先说一句你最近老点开什么。", "error");
      elements.chatInput.focus();
      return;
    }
    if (!state.online) {
      setHint("后端还没连上，现在还发不出去。", "error");
      return;
    }

    appendChatMessage("你", message);
    elements.chatInput.value = "";
    elements.chatSendButton.disabled = true;
    elements.chatSendButton.textContent = "发送中...";

    try {
      const payload = await sendChatMessage(message);
      appendChatMessage("助手", payload.reply);
      setHint("收到，这句记下了。", "success");
      void refreshProfileSummaryAfterInteraction();
    } catch {
      appendChatMessage("助手", "刚刚没发出去，换个说法再试试。");
      setHint("聊天接口这会儿没接上，先看看本地后端是不是开着。", "error");
    } finally {
      elements.chatSendButton.disabled = false;
      elements.chatSendButton.textContent = "发出去";
    }
  });
}

async function initializePopup() {
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  bindTabs();
  bindRefreshButton();
  bindChat();
  setActiveTab(
    requestedTab === "profile" || requestedTab === "chat" || requestedTab === "recommend"
      ? requestedTab
      : "recommend",
  );
  setHint("先看看本地后端连上没。");
  await initializeRecommendations();
  connectRuntimeStream();
}

void initializePopup();
