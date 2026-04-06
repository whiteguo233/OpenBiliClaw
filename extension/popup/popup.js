import {
  getActivityCardState,
  buildFeedbackPayload,
  buildNextCognitionHistoryState,
  buildVideoUrl,
  getCommentSubmitUiState,
  getCognitionHistoryUiState,
  getConnectionBadgeState,
  getDisplayedPoolStatusSummary,
  getNextExpandedCognitionIndex,
  getReadyRecommendationHint,
  getHintBannerState,
  getRuntimeRefreshSubmissionState,
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
  appendRecommendations,
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
  activityFeed: null,
  activityExpanded: false,
  activeFeedbackProgress: null,
  refreshStatusMessage: "",
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
    onConnect() {
      if (!state.online) {
        state.online = true;
        setStatus(true);
        setHint("后端重新连上了，正在刷新。", "success");
        void initializeRecommendations();
      }
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

function renderSpeculativeInterests(container, items) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  if (!items || items.length === 0) {
    const fallback = document.createElement("p");
    fallback.className = "is-fallback";
    fallback.textContent = "暂时没有在试探的方向，过一阵会有的。";
    container.append(fallback);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "speculative-item";

    const header = document.createElement("div");
    header.className = "spec-header";

    const domain = document.createElement("span");
    domain.className = "spec-domain";
    domain.textContent = item.domain;
    header.append(domain);

    const progress = document.createElement("span");
    progress.className = "spec-progress";
    progress.textContent = `${item.confirmation_count}/${item.confirmation_threshold} 次确认`;
    header.append(progress);

    row.append(header);

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
  const typeLabel = document.createElement("div");
  typeLabel.className = "mbti-type-label";
  typeLabel.textContent = mbti.type;
  container.append(typeLabel);

  const dims = document.createElement("div");
  dims.className = "mbti-dimensions";
  const dimLabels = { E_I: "E/I", S_N: "S/N", T_F: "T/F", J_P: "J/P" };
  for (const [key, label] of Object.entries(dimLabels)) {
    const dim = mbti.dimensions?.[key];
    if (!dim) continue;
    const row = document.createElement("div");
    row.className = "mbti-dim-row";
    const pole = document.createElement("span");
    pole.className = "mbti-dim-pole";
    pole.textContent = dim.pole || label;
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

function renderRecommendations(items, { append = false } = {}) {
  if (!(elements.list instanceof HTMLElement)) {
    return;
  }
  if (!append) {
    elements.list.replaceChildren();
  }

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

    const stateBadge = document.createElement("span");
    stateBadge.className = `recommendation-state${item.presented ? " is-presented" : ""}`;
    stateBadge.textContent = item.presented ? "你应该刷到过" : "刚给你翻出来";

    if (item.topic_label) {
      const badge = document.createElement("span");
      badge.className = "topic-badge";
      badge.textContent = item.topic_label;
      top.append(badge);
    }
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
  }
}

function maybeLoadMoreRecommendations() {
  if (
    state.activeTab !== "recommend" ||
    !(elements.content instanceof HTMLElement) ||
    elements.viewRecommend.hidden ||
    state.loadingMore ||
    !state.hasMoreRecommendations
  ) {
    return;
  }

  const remaining = elements.content.scrollHeight - elements.content.scrollTop - elements.content.clientHeight;
  if (remaining <= 96) {
    void loadMoreRecommendations();
  }
}

function renderRecommendationState(stateShape) {
  if (stateShape.kind === "ready") {
    hideRecommendationEmptyState();
    renderRecommendations(stateShape.items);
    const hint = getReadyRecommendationHint(stateShape.runtime);
    setHint(hint.message, hint.tone);
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
    state.recommendations = [];
    state.hasMoreRecommendations = false;
    state.loadingMore = false;
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
  setRefreshButtonState(true, "正在给你换一批…");
  try {
    const result = await reshuffleRecommendations();
    if (!Array.isArray(result.items)) {
      setHint("先执行 openbiliclaw init，再回来刷新。", "error");
      return;
    }
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
      maybeLoadMoreRecommendations();
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
