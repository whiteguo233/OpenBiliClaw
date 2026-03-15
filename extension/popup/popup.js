import {
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildVideoUrl,
  getCommentSubmitUiState,
  getCognitionHistoryUiState,
  getConnectionBadgeState,
  getNextExpandedCognitionIndex,
  getHintBannerState,
  getRuntimeRefreshSubmissionState,
  getRealtimePoolStatusSummary,
  getPoolStatusSummary,
  getPopupState,
  getSubmissionProgressMessage,
  getTabButtonState,
  mergeRuntimeStatusEvent,
  normalizeActivityFeed,
  normalizeProfileSummary,
  shouldFetchProfileSummary,
  shouldSubmitChatOnEnter,
  validateCommentInput,
} from "./popup-helpers.js";
import { createRuntimeStreamClient } from "./popup-stream.js";
import {
  checkBackendStatus,
  fetchActivityFeed,
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
  activityFeed: null,
  activityExpanded: false,
  activeFeedbackProgress: null,
};

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
  profileRecentMemoryStatus: document.getElementById("profileRecentMemoryStatus"),
  profileRecentMemoryMore: document.getElementById("profileRecentMemoryMore"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  chatSendButton: document.getElementById("chatSendButton"),
  chatStatus: document.getElementById("chatStatus"),
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
      state.activeFeedbackProgress?.handle?.(event);
      if (elements.footer instanceof HTMLElement) {
        elements.footer.dataset.tone = getHintBannerState(getRuntimeEventTone(event)).tone;
      }
      renderActivityCard();
    },
  });
  client.connect();
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
    state.activityFeed = normalizeActivityFeed(await fetchActivityFeed());
  } catch {
    state.activityFeed = normalizeActivityFeed({
      live_summary: "阿B 这会儿先替你盯着。",
      headline: "最近还没新动静，先多刷一阵。",
      items: [],
    });
  }
  renderActivityCard();
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
    const source = document.createElement("span");
    source.className = "cognition-source";
    source.textContent = item.sourceLabel;

    const stateLabel = document.createElement("span");
    stateLabel.className = "cognition-state";
    stateLabel.textContent = isExpanded ? "收起" : item.expandLabel;

    if (item.sourceLabel) {
      meta.append(source);
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
    renderCognitionHistoryControls({
      items: [],
      hasMore: false,
      nextCursor: "",
      loadingMore: false,
      loadMoreError: "",
    });
    return;
  }

  elements.profileEmpty.hidden = true;
  elements.profileCard.hidden = false;
  elements.profilePortrait.textContent = summary.personality_portrait;
  renderChipList(elements.profileTraits, summary.core_traits, "这部分还在慢慢补");
  renderChipList(elements.profileNeeds, summary.deep_needs, "这块还要再多看一点");
  renderChipList(elements.profileInterests, summary.top_interests, "再刷一阵，这里会更准");
  renderCognitionCards(
    elements.profileRecentMemory,
    getProfileCognitionItems(summary),
    "阿B 还在继续观察，过一阵这里会更具体。",
  );
  renderCognitionHistoryControls(state.profileCognitionHistory);
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
    const summary = normalizeProfileSummary(await fetchProfileSummary({ limit: 3 }));
    state.profile = normalizeProfileSummary(summary);
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
  state.profileLoaded = true;
  renderProfileSummary(state.profile);
  maybeLoadMoreCognitionHistory();
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
  maybeLoadMoreCognitionHistory();
}

function maybeLoadMoreCognitionHistory() {
  if (
    state.activeTab !== "profile" ||
    !(elements.content instanceof HTMLElement) ||
    elements.viewProfile.hidden ||
    !state.profileCognitionHistory.hasMore ||
    state.profileCognitionHistory.loadingMore
  ) {
    return;
  }

  const remaining = elements.content.scrollHeight - elements.content.scrollTop - elements.content.clientHeight;
  if (remaining <= 96) {
    void loadMoreCognitionHistory();
  }
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
  await loadActivityFeed();

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
    await loadActivityFeed();
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

function bindProfileHistoryLoading() {
  if (elements.content instanceof HTMLElement) {
    elements.content.addEventListener("scroll", () => {
      maybeLoadMoreCognitionHistory();
    });
  }

  if (elements.profileRecentMemoryMore instanceof HTMLButtonElement) {
    elements.profileRecentMemoryMore.addEventListener("click", () => {
      void loadMoreCognitionHistory();
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
    setChatStatus(getSubmissionProgressMessage("chat", "waiting_reply"), "info");
    clearSlowStatusTimer();
    slowStatusTimer = window.setTimeout(() => {
      if (elements.chatSendButton.disabled) {
        setChatStatus(getSubmissionProgressMessage("chat", "waiting_slow"), "info");
      }
    }, 2500);

    try {
      const payload = await sendChatMessage(message);
      clearSlowStatusTimer();
      appendChatMessage("助手", payload.reply);
      setHint("收到，这句记下了。", "success");
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
    } catch {
      clearSlowStatusTimer();
      appendChatMessage("助手", "刚刚没发出去，换个说法再试试。");
      setChatStatus(getSubmissionProgressMessage("chat", "error"), "error");
      setHint("聊天接口这会儿没接上，先看看本地后端是不是开着。", "error");
    } finally {
      clearSlowStatusTimer();
      elements.chatSendButton.disabled = false;
      elements.chatSendButton.textContent = "发出去";
    }
  });
}

async function initializePopup() {
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  bindTabs();
  bindProfileHistoryLoading();
  bindRefreshButton();
  bindActivityToggle();
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
