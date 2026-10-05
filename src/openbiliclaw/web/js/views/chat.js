/**
 * Chat view — message history, input with placeholder carousel,
 * AI thinking state, messages overlay (probe + delight notifications),
 * contextual chat entry from delight/probe.
 */

import {
  startChatTurn,
  fetchChatContext,
  fetchChatTurn,
  fetchChatTurns,
  fetchChatSessionDetail,
  fetchChatSessions,
  createChatSession,
  updateChatSession,
  fetchChatSkills,
  fetchChatPersonas,
  streamAgentChatTurn,
  streamChatTurnLegacy,
  fetchChatApprovals,
  approveChatApproval,
  rejectChatApproval,
  createAgentTask,
  fetchAgentTasks,
  fetchAgentTask,
  cancelAgentTask,
  fetchPendingConfirmations,
  openPendingConfirmation,
  actOnChatCard,
  fetchProfileSummary,
  fetchActivityFeed,
  fetchPendingNotifications,
  fetchPendingProbes,
  fetchPendingAvoidanceProbes,
  ackNotification,
  fetchDelightBatch,
  respondToDelight,
  markDelightSent,
  respondToProbe,
  respondToAvoidanceProbe,
} from "../api.js";
import { setUnreadCount, navigateToTab } from "../app.js";
import {
  forgetHandledProbe,
  mergeProbeNotifications,
  probeNotificationKey,
  rememberHandledProbe,
  removeProbeFromNotifications,
  shouldDisplayProbeFromWebSocket,
} from "./probe-notification-helpers.js";
import {
  normalizeChatTurn,
  normalizeProfileSummary,
  normalizeActivityFeed,
  normalizeDelightCandidate,
  getDelightActionState,
  getDelightMessageActions,
  getProbeMessageActions,
  getAvoidanceProbeMessageActions,
  getMobileChatSession,
  getChatHistoryViewState,
  getCoverImageAttrs,
  getSourceLabel,
  buildContentUrl,
} from "../view-models.js";
import { openContentUrl } from "../app-launch.js";
import { state, patchState, subscribe } from "../state.js";

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

const agentChat = globalThis.OpenBiliClawAgentChat;
if (!agentChat) {
  throw new Error("agent-chat shared helper did not load");
}
const {
  applyAgentEvent,
  applyApprovalRecordToRun,
  agentEventsFromTurn,
  agentRunFromEvents,
  createAgentRun,
  captureApprovalDrafts,
  restoreApprovalDrafts,
  isAgentTaskActive,
  isAgentTaskSummaryTurn,
  isApprovalTerminalStatus,
  normalizeApproveResponse,
  renderAgentRunMarkup,
  renderAgentTaskDetailMarkup,
  renderAgentTaskRowMarkup,
  renderAgentTaskSummaryMarkup,
  renderApprovalCardMarkup,
  skillDisplayTitle,
} = agentChat;

let $root = null;
let loaded = false;
let turns = [];
let historyLoaded = false;
let sending = false;
let pendingTurnId = null;
let pollTimer = null;
let userScrolledUp = false;
const CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500;
let historyRefreshTimer = null;
let historyRefreshInFlight = false;
let historyRefreshGeneration = 0;
let visibilityResumeBound = false;
let chatViewportBound = false;
let lastHistorySignature = null;
let pendingConfirmationRefreshTimer = null;
let dialogueStatus = { message: "", tone: "info" };
let retainedDraft = "";
let dialogueContextSelection = readContextSelection(
  (() => {
    try { return globalThis.localStorage; } catch { return null; }
  })(),
  "mobile-web",
);
const dialogueTurnsById = new Map();
const dialogueCardActionAbortController = new AbortController();
let pendingConfirmations = {
  count: 0,
  items: [],
  expanded: false,
};

// ── Agent loop state (M9) ────────────────────────────────────
// Live process-flow runs keyed by turn_id. Replayed turns reduce
// payload.agent_events on the fly; settled live runs stay here until the
// durable history snapshot replaces them.
let agentLoopAvailable = true;
const agentRunsByTurnId = new Map();
const streamingTurnIds = new Set();
const legacyStreamReplies = new Map();
// Live token deltas of the current agent-stream hop, keyed by turn_id.
// Deltas render into the turn's thinking bubble as they arrive; a
// ``thinking`` event (intermediate hop done) resets the buffer, and the
// authoritative ``final`` / ``done`` text replaces it.
const agentDeltaBuffers = new Map();

const CHAT_SESSION_STORAGE_KEY = "openbiliclaw.mobile.chatSessionId";
const CHAT_SESSION_SKILLS_STORAGE_KEY = "openbiliclaw.mobile.chatSessionSkills";

function readStoredJson(key, fallback) {
  try {
    const parsed = JSON.parse(globalThis.localStorage?.getItem(key) || "");
    return parsed ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStoredJson(key, value) {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  } catch { /* storage unavailable */ }
}

let activeSessionId = (() => {
  try {
    return String(globalThis.localStorage?.getItem(CHAT_SESSION_STORAGE_KEY) || "") || "default";
  } catch {
    return "default";
  }
})();
let chatSessions = [];
const sessionDrafts = new Map();
let sessionsDrawerOpen = false;
let sessionRenameId = "";
let sessionSkillMap = (() => {
  const stored = readStoredJson(CHAT_SESSION_SKILLS_STORAGE_KEY, {});
  return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
})();
let chatSkills = [];
let skillsSheetOpen = false;
let chatPreferenceTab = "persona";
let chatPersonas = [];
let chatPersonaExamplePrompt = "";
let personaCatalogLoading = false;
let personaCatalogError = "";
let personaRevision = 0;
const personaSaveRequests = new Map();
const personaSaveErrors = new Map();
let pendingApprovals = [];
let approvalsExpanded = false;
// 异步审批执行（approve 只入队）：本页批准、等待终态的审批（id → turnId），
// 以及后端 executing 记录（刷新/回放时把 pending 卡恢复成「执行中…」）。
const executingApprovals = new Map();
let approvalExecutingOverrides = new Map();
// 本页已观察到终态的记录：approval_result 事件落进 turn 回放前，
// 轮询重渲染也用这份快照保持终态展示。
const approvalTerminalOverrides = new Map();
let tasksOverlayOpen = false;
let agentTasks = [];
let agentTaskDetail = null;
let tasksPollTimer = null;

function currentSkillName() {
  return String(sessionSkillMap[activeSessionId] || "");
}

function setSessionSkill(sessionId, skillName) {
  if (skillName) sessionSkillMap[sessionId] = skillName;
  else delete sessionSkillMap[sessionId];
  writeStoredJson(CHAT_SESSION_SKILLS_STORAGE_KEY, sessionSkillMap);
}

// Messages overlay state
let overlayOpen = false;
let notifications = [];
let delightMsgs = [];
const pendingProbeActions = new Map();

function pendingProbeAction(type, domain) {
  return pendingProbeActions.get(probeNotificationKey(type, domain)) || null;
}

function setProbeCardBusy(card, busy) {
  if (!card) return;
  card.classList.toggle("is-processing", busy);
  card.setAttribute("aria-busy", busy ? "true" : "false");
  for (const actionBtn of card.querySelectorAll("[data-probe]")) {
    actionBtn.disabled = busy;
  }
}

// Placeholder carousel
const PLACEHOLDERS = [
  "\u6700\u8FD1\u6709\u4EC0\u4E48\u60F3\u804A\u7684\uFF1F",
  "\u5BF9\u54EA\u6761\u63A8\u8350\u6709\u60F3\u6CD5\uFF1F",
  "\u60F3\u63A2\u7D22\u4EC0\u4E48\u65B0\u9886\u57DF\uFF1F",
  "\u89C9\u5F97\u753B\u50CF\u51C6\u4E0D\u51C6\uFF1F",
  "\u6709\u4EC0\u4E48\u4E0D\u60F3\u518D\u770B\u5230\u7684\uFF1F",
];
let placeholderIdx = 0;
let placeholderTimer = null;
let inputFocused = false;

function chatSession(scope = "chat") {
  return getMobileChatSession(scope);
}

function contextStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

function storeDialogueContext(selection) {
  dialogueContextSelection = writeContextSelection(contextStorage(), "mobile-web", selection);
  return dialogueContextSelection;
}

async function validateDialogueContext({ announce = false } = {}) {
  const current = normalizeContextPreview(dialogueContextSelection);
  if (!current) return null;
  const contextTarget = turns.find((turn) => turn?.turn_id === current.reply_to_turn_id);
  if (isTerminalCardTurn(contextTarget)) {
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
      if (announce) setDialogueStatus(contextErrorMessage(error), "error");
    } else if (announce && code === "reply_target_processing") {
      setDialogueStatus(contextErrorMessage(error), "info");
    }
    return code === "reply_target_processing" ? current : null;
  }
}

async function selectDialogueContext(turnId, preview = null) {
  const turn = turns.find((item) => item?.turn_id === turnId) || { turn_id: turnId };
  const candidate = contextSelectionFromTurn(turn, preview);
  if (candidate) {
    storeDialogueContext(candidate);
    render();
    return candidate;
  }
  try {
    const fetched = normalizeContextPreview(await fetchChatContext(turnId));
    const fetchedCandidate = contextSelectionFromTurn(turn, fetched);
    if (!fetchedCandidate) throw new Error("invalid_context_preview");
    storeDialogueContext(fetchedCandidate);
    render();
    return fetchedCandidate;
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
    return null;
  }
}

// ── Escape helper ────────────────────────────────────────────
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

function isChallengeProbe(item) {
  const mode = String(item?.probe_mode || "").toLowerCase();
  return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
}

// ── Render Chat ──────────────────────────────────────────────
function isNearChatBottom(element) {
  if (!element) return true;
  return element.scrollHeight - element.clientHeight - element.scrollTop <= 40;
}

function openEvidenceTurnIds(element) {
  if (!element) return new Set();
  return new Set(
    Array.from(element.querySelectorAll(".dialogue-evidence[open]"))
      .map((details) => details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "")
      .filter(Boolean),
  );
}

function setDialogueStatus(message = "", tone = "info") {
  dialogueStatus = { message, tone };
  const status = $root?.querySelector(".chat-status");
  if (status instanceof HTMLElement) {
    status.textContent = message;
    status.hidden = !message;
    status.dataset.tone = tone;
  }
}

function createPendingPanel(previousScrollTop = 0) {
  const panel = document.createElement("section");
  panel.className = "chat-pending";
  panel.setAttribute("aria-label", "待聊确认");

  const toggle = document.createElement("button");
  toggle.className = `chat-pending-toggle${pendingConfirmations.expanded ? " is-expanded" : ""}`;
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(pendingConfirmations.expanded));
  toggle.setAttribute("aria-controls", "mobile-chat-pending-list");
  const countText = pendingConfirmations.count > 99 ? "99+" : String(pendingConfirmations.count);
  toggle.innerHTML = `<span>待聊确认 <span class="chat-pending-count">${countText}</span></span>`;
  toggle.addEventListener("click", () => {
    pendingConfirmations.expanded = !pendingConfirmations.expanded;
    render();
    if (pendingConfirmations.expanded) void refreshPendingConfirmations();
  });

  const list = document.createElement("div");
  list.id = "mobile-chat-pending-list";
  list.className = "chat-pending-list";
  list.hidden = !pendingConfirmations.expanded;
  list.setAttribute("aria-label", "待聊确认列表");
  list.innerHTML = renderPendingListMarkup(pendingConfirmations.items);
  list.addEventListener("click", (event) => {
    const button = event.target instanceof Element
      ? event.target.closest("[data-confirmation-ref]")
      : null;
    if (button instanceof HTMLButtonElement) void handlePendingConfirmationOpen(button);
  });

  panel.append(toggle, list);
  requestAnimationFrame(() => {
    list.scrollTop = Math.min(previousScrollTop, Math.max(0, list.scrollHeight - list.clientHeight));
  });
  return panel;
}

// ── Agent loop UI builders (M9) ──────────────────────────────
function agentRunForTurn(turn) {
  if (!turn?.turn_id) return null;
  const live = agentRunsByTurnId.get(turn.turn_id);
  const events = agentEventsFromTurn(turn);
  const run = live || (events.length > 0 ? agentRunFromEvents(events) : null);
  // 回放只归约 approval_request → pending；用 executing 列表恢复中间态，
  // 避免刷新后露出可重复点击的批准按钮。
  if (run) {
    for (const record of approvalTerminalOverrides.values()) applyApprovalRecordToRun(run, record);
    for (const record of approvalExecutingOverrides.values()) applyApprovalRecordToRun(run, record);
  }
  return run;
}

function agentRunHasContent(run) {
  return Boolean(
    run && (run.steps.length > 0 || run.error || run.skillSuggestion || run.taskProposal),
  );
}

function createChatTopbar() {
  const bar = document.createElement("div");
  bar.className = "chat-agent-topbar";

  const sessionsBtn = document.createElement("button");
  sessionsBtn.type = "button";
  sessionsBtn.className = "chat-agent-topbar-btn";
  sessionsBtn.setAttribute("aria-label", "会话列表");
  sessionsBtn.textContent = "☰";
  sessionsBtn.addEventListener("click", () => {
    sessionsDrawerOpen = !sessionsDrawerOpen;
    if (sessionsDrawerOpen) void refreshSessions();
    renderAgentOverlays();
  });

  const skillBtn = document.createElement("button");
  skillBtn.type = "button";
  skillBtn.className = "chat-agent-skill-chip";
  skillBtn.setAttribute("aria-label", "选择角色与聊天风格");
  const skillName = currentSkillName();
  skillBtn.innerHTML = `<span>🎭 ${esc(skillDisplayTitle(skillName, chatSkills))}</span>
    <span class="chat-agent-persona-label">${esc(currentPersonaTitle())}</span>`;
  skillBtn.addEventListener("click", () => {
    skillsSheetOpen = !skillsSheetOpen;
    if (skillsSheetOpen) {
      void refreshSkills();
      void refreshPersonas();
      void loadHistory();
    }
    renderAgentOverlays();
  });

  const session = chatSessions.find((item) => item?.session_id === activeSessionId);
  const title = document.createElement("span");
  title.className = "chat-agent-session-title";
  title.textContent = session?.title || (activeSessionId === "default" ? "默认会话" : "当前会话");

  const tasksBtn = document.createElement("button");
  tasksBtn.type = "button";
  tasksBtn.className = "chat-agent-topbar-btn chat-agent-tasks-btn";
  tasksBtn.textContent = "任务";
  const activeTaskCount = agentTasks.filter((task) => isAgentTaskActive(task?.status)).length;
  if (activeTaskCount > 0) {
    const badge = document.createElement("span");
    badge.className = "chat-agent-badge";
    badge.textContent = String(activeTaskCount);
    tasksBtn.appendChild(badge);
  }
  tasksBtn.addEventListener("click", () => {
    tasksOverlayOpen = !tasksOverlayOpen;
    if (tasksOverlayOpen) void refreshAgentTasks();
    renderAgentOverlays();
    syncTasksPolling();
  });

  bar.append(sessionsBtn, skillBtn, title, tasksBtn);
  return bar;
}

function createApprovalsPanel() {
  const panel = document.createElement("section");
  panel.className = "chat-pending chat-approvals";
  panel.setAttribute("aria-label", "待审批操作");
  // 面板同时列出执行中的审批（无按钮，只显示「执行中…」状态）。
  const visibleApprovals = [...pendingApprovals, ...approvalExecutingOverrides.values()];
  panel.hidden = visibleApprovals.length === 0 && !approvalsExpanded;

  const toggle = document.createElement("button");
  toggle.className = `chat-pending-toggle${approvalsExpanded ? " is-expanded" : ""}`;
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(approvalsExpanded));
  toggle.setAttribute("aria-controls", "mobile-chat-approvals-list");
  toggle.innerHTML = `<span>待审批操作 <span class="chat-pending-count">${pendingApprovals.length}</span></span>`;
  toggle.addEventListener("click", () => {
    approvalsExpanded = !approvalsExpanded;
    render();
    if (approvalsExpanded) void refreshApprovals();
  });

  const list = document.createElement("div");
  list.id = "mobile-chat-approvals-list";
  list.className = "chat-pending-list chat-approvals-list";
  list.hidden = !approvalsExpanded;
  list.setAttribute("aria-label", "待审批操作列表");
  list.innerHTML = visibleApprovals
    .map((approval) => renderApprovalCardMarkup(approval))
    .join("");
  list.addEventListener("click", (event) => {
    handleAgentActionClick(event);
  });

  panel.append(toggle, list);
  return panel;
}

// Targeted DOM update for the live process flow of one streaming turn, so
// streaming updates never rebuild the whole shell (input focus survives).
function updateAgentRunDom(turnId) {
  const messages = document.getElementById("chat-messages");
  if (!(messages instanceof HTMLElement)) return;
  const container = messages.querySelector(
    `[data-dialogue-turn-container="${CSS.escape(turnId)}"]`,
  );
  if (!(container instanceof HTMLElement)) return;
  const run = agentRunsByTurnId.get(turnId);
  let slot = container.querySelector(":scope > .agent-run-live-slot");
  if (!agentRunHasContent(run)) {
    slot?.remove();
    return;
  }
  if (!(slot instanceof HTMLElement)) {
    slot = document.createElement("div");
    slot.className = "agent-run-live-slot";
    const thinkingBubble = container.querySelector(".chat-bubble.thinking");
    container.insertBefore(slot, thinkingBubble || null);
  }
  const approvalDrafts = captureApprovalDrafts(slot);
  slot.innerHTML = renderAgentRunMarkup(run, { collapsed: run.settled });
  restoreApprovalDrafts(slot, approvalDrafts);
  if (!userScrolledUp || isNearChatBottom(messages)) {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  }
}

// Delegated handler for all shared agent markup action hooks.
function handleAgentActionClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const approvalBtn = target.closest("[data-agent-approval-action]");
  if (approvalBtn instanceof HTMLElement) {
    void handleApprovalAction(approvalBtn);
    return;
  }
  const skillSwitch = target.closest("[data-agent-skill-switch]");
  if (skillSwitch instanceof HTMLElement) {
    handleSkillSwitchCard(skillSwitch);
    return;
  }
  if (target.closest("[data-agent-skill-dismiss]")) {
    target.closest(".agent-skill-card")?.remove();
    return;
  }
  const taskConfirm = target.closest("[data-agent-task-confirm]");
  if (taskConfirm instanceof HTMLElement) {
    void handleTaskProposalConfirm(taskConfirm);
    return;
  }
  if (target.closest("[data-agent-task-dismiss]")) {
    target.closest(".agent-task-proposal")?.remove();
    return;
  }
  const taskOpen = target.closest("[data-agent-task-open]");
  if (taskOpen instanceof HTMLElement) {
    void openTaskDetail(taskOpen.dataset.agentTaskOpen || taskOpen.getAttribute("data-agent-task-open") || "");
    return;
  }
  const taskCancel = target.closest("[data-agent-task-cancel]");
  if (taskCancel instanceof HTMLElement) {
    void handleTaskCancel(taskCancel);
    return;
  }
  const suggestionUse = target.closest("[data-agent-suggestion-use]");
  if (suggestionUse instanceof HTMLElement) {
    const summary = suggestionUse.dataset.summary || "";
    if (summary) {
      retainedDraft = summary;
      tasksOverlayOpen = false;
      renderAgentOverlays();
      render();
      $root?.querySelector("#chat-input")?.focus({ preventScroll: true });
      setDialogueStatus("建议已带入输入框，补充一句再发出去。", "info");
    }
  }
}

async function handleApprovalAction(button) {
  const card = button.closest("[data-approval-id]");
  const approvalId = card?.dataset.approvalId || "";
  const action = button.dataset.agentApprovalAction || "";
  if (!approvalId || !action) return;
  if (action === "reject") {
    card.querySelector(".agent-approval-reject")?.removeAttribute("hidden");
    card.querySelector(".agent-approval-actions")?.setAttribute("hidden", "");
    card.querySelector("input.agent-approval-reason")?.focus();
    return;
  }
  if (action === "reject-cancel") {
    card.querySelector(".agent-approval-reject")?.setAttribute("hidden", "");
    card.querySelector(".agent-approval-actions")?.removeAttribute("hidden");
    return;
  }
  for (const btn of card.querySelectorAll("button")) btn.disabled = true;
  try {
    if (action === "approve") {
      const response = normalizeApproveResponse(await approveChatApproval(approvalId));
      if (response.kind === "queued") {
        // 异步执行协议：批准只入队，卡片进「执行中…」，终态交给
        // refreshApprovals 的 2.5s 轮询落到 executed/failed。
        markApprovalCardExecuting(card);
        executingApprovals.set(approvalId, findApprovalTurnId(approvalId));
        setRunApprovalStatus(approvalId, "executing");
        setDialogueStatus(
          response.alreadyQueued ? "这项改动已在执行中。" : "已批准，正在执行…",
          "info",
        );
      } else {
        // 旧协议（同步返回 ok/result）或幂等终态应答：直接显示结果。
        const ok = response.ok !== false;
        markApprovalCardSettled(card, ok ? "已批准并执行" : `批准了但执行失败：${response.resultText || ""}`, ok);
        setRunApprovalStatus(approvalId, ok ? "executed" : "failed", response.resultText);
        setDialogueStatus(ok ? "已批准并执行。" : "批准了，但执行失败。", ok ? "success" : "error");
      }
    } else if (action === "reject-submit") {
      const reason = card.querySelector("input.agent-approval-reason")?.value?.trim() || "";
      await rejectChatApproval(approvalId, reason);
      markApprovalCardSettled(card, "已拒绝，不会执行。", true);
      setRunApprovalStatus(approvalId, "rejected");
      setDialogueStatus("已拒绝这个操作。", "info");
    }
    void refreshApprovals();
  } catch (error) {
    for (const btn of card.querySelectorAll("button")) btn.disabled = false;
    setDialogueStatus(contextErrorMessage(error), "error");
  }
}

function findApprovalTurnId(approvalId) {
  for (const [turnId, run] of agentRunsByTurnId) {
    if (run.approvals.some((item) => item.approval_id === approvalId)) return turnId;
  }
  return "";
}

function setRunApprovalStatus(approvalId, status, resultText = "") {
  for (const run of agentRunsByTurnId.values()) {
    const approval = run.approvals.find((item) => item.approval_id === approvalId);
    if (approval) {
      approval.status = status;
      if (resultText) approval.resultText = resultText;
    }
  }
}

function markApprovalCardExecuting(card) {
  card.dataset.status = "executing";
  const status = card.querySelector(".agent-approval-status");
  if (status instanceof HTMLElement) {
    status.textContent = "执行中…";
    status.dataset.tone = "executing";
  }
  card.querySelector(".agent-approval-actions")?.remove();
  card.querySelector(".agent-approval-reject")?.remove();
}

function markApprovalCardSettled(card, message, ok) {
  card.dataset.status = ok ? "executed" : "failed";
  const status = card.querySelector(".agent-approval-status");
  if (status instanceof HTMLElement) {
    status.textContent = message;
    status.dataset.tone = ok ? "executed" : "failed";
  }
  card.querySelector(".agent-approval-actions")?.remove();
  card.querySelector(".agent-approval-reject")?.remove();
}

// 轮询发现本页批准的审批到达终态：更新 run 模型、重绘过程流并提示结果。
function settleTrackedApproval(approvalId, turnId, record) {
  const ok = record.status === "executed";
  approvalTerminalOverrides.set(approvalId, record);
  setRunApprovalStatus(approvalId, record.status, record.resultText || "");
  if (turnId) updateAgentRunDom(turnId);
  setDialogueStatus(
    ok ? "已批准并执行。" : `批准了，但执行失败${record.resultText ? `：${record.resultText}` : "。"}`,
    ok ? "success" : "error",
  );
  return true;
}

function handleSkillSwitchCard(button) {
  const skill = button.dataset.agentSkillSwitch || "";
  if (!skill) return;
  setSessionSkill(activeSessionId, skill);
  button.closest(".agent-skill-card")?.remove();
  setDialogueStatus(`已切换到「${skillDisplayTitle(skill, chatSkills)}」，从下一句开始生效。`, "success");
  render();
}

async function handleTaskProposalConfirm(button) {
  const card = button.closest("[data-agent-task-proposal]");
  if (!card || button.disabled) return;
  const prompt = card.dataset.taskPrompt || "";
  const title = card.dataset.taskTitle || "";
  const skill = card.dataset.taskSkill || "";
  if (!prompt) return;
  button.disabled = true;
  button.textContent = "发起中…";
  try {
    const task = await createAgentTask({
      prompt,
      title,
      skill,
      sessionId: activeSessionId === "default" ? "" : activeSessionId,
    });
    card.innerHTML = `<p class="agent-task-proposal-text">后台任务已发起${task?.title ? `「${esc(task.title)}」` : ""}，完成后会把结果带回这里。</p>`;
    setDialogueStatus("后台任务已开始，可在「任务」里查看进度。", "success");
    void refreshAgentTasks();
  } catch (error) {
    button.disabled = false;
    button.textContent = "确认发起";
    setDialogueStatus(contextErrorMessage(error), "error");
  }
}

async function handleTaskCancel(button) {
  const taskId = button.dataset.agentTaskCancel || "";
  if (!taskId || button.disabled) return;
  button.disabled = true;
  try {
    await cancelAgentTask(taskId);
    setDialogueStatus("任务已取消。", "info");
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
  }
  await refreshAgentTasks();
  if (agentTaskDetail?.task_id === taskId) await openTaskDetail(taskId, { silent: true });
}

// ── Agent data loading ───────────────────────────────────────
function currentPersonaId() {
  const session = chatSessions.find((item) => item?.session_id === activeSessionId);
  return session ? String(session.metadata?.persona || "natural") : "";
}

function currentPersonaTitle() {
  const id = currentPersonaId();
  if (!id) return "风格加载中…";
  return chatPersonas.find((persona) => persona.id === id)?.title
    || (id === "natural" ? "自然朋友" : "已保存的风格");
}

function personaRequestError(error) {
  const detail = error?.details?.detail;
  const message = typeof detail === "string" ? detail : detail?.message || error?.details?.message;
  if (message) return String(message).replace(/[。！!]+$/, "");
  if (/[\u3400-\u9fff]/.test(error?.message || "")) return error.message.replace(/[。！!]+$/, "");
  if (error?.name === "AbortError" || /timed?\s*out/i.test(error?.message || "")) return "请求超时";
  if (Number(error?.status) === 401) return "登录已过期";
  if ([404, 405].includes(Number(error?.status))) return "当前服务尚不支持聊天风格";
  return error?.status ? `服务暂不可用（${error.status}）` : "连接中断或服务器未响应";
}

// Keep the server session as the only persona source. A read started before
// a save, or answered while that save is pending, cannot roll back its choice.
function mergeChatSessionSnapshot(session, requestRevision = personaRevision) {
  const previous = chatSessions.find((item) => item?.session_id === session?.session_id);
  if (requestRevision === personaRevision && !personaSaveRequests.has(session?.session_id)) {
    return session;
  }
  const metadata = { ...session.metadata };
  if (previous?.metadata?.persona) metadata.persona = previous.metadata.persona;
  else delete metadata.persona;
  return { ...session, metadata };
}

function applyChatSessionSnapshot(session) {
  if (!session?.session_id) return false;
  const previous = chatSessions.find((item) => item?.session_id === session.session_id);
  const next = mergeChatSessionSnapshot(session);
  chatSessions = previous
    ? chatSessions.map((item) => item.session_id === session.session_id ? next : item)
    : [...chatSessions, next];
  if (previous?.metadata?.persona !== next.metadata?.persona) personaSaveErrors.delete(session.session_id);
  return previous?.metadata?.persona !== next.metadata?.persona || previous?.title !== next.title;
}

async function refreshPersonas() {
  if (personaCatalogLoading) return;
  if (!state.online) {
    personaCatalogError = "当前离线，恢复连接后可重新加载。";
    if (skillsSheetOpen) renderAgentOverlays();
    return;
  }
  personaCatalogLoading = true;
  personaCatalogError = "";
  if (skillsSheetOpen) renderAgentOverlays();
  try {
    const catalog = await fetchChatPersonas();
    chatPersonas = catalog.personas;
    chatPersonaExamplePrompt = catalog.examplePrompt;
    if (!chatPersonas.length) throw new Error("聊天风格暂不可用，请稍后重试。");
  } catch (error) {
    personaCatalogError = personaRequestError(error);
  } finally {
    personaCatalogLoading = false;
    if (skillsSheetOpen) renderAgentOverlays();
  }
}

function invalidatePersonaReads(sessionId) {
  personaRevision += 1;
  if (sessionId === activeSessionId) {
    historyRefreshGeneration += 1;
    historyRefreshInFlight = false;
  }
}

async function saveChatPersona(personaId) {
  const sessionId = activeSessionId;
  if (!chatPersonas.some((persona) => persona.id === personaId)
    || !currentPersonaId() || personaSaveRequests.has(sessionId)) return;
  if (personaId === currentPersonaId()) {
    closeChatPreferences();
    return;
  }
  personaSaveRequests.set(sessionId, personaId);
  personaSaveErrors.delete(sessionId);
  invalidatePersonaReads(sessionId);
  renderAgentOverlays();
  try {
    const session = await updateChatSession(sessionId, { persona: personaId });
    if (session?.session_id !== sessionId || session.metadata?.persona !== personaId) {
      throw new Error("服务端未确认聊天风格，请重试。");
    }
    personaSaveRequests.delete(sessionId);
    invalidatePersonaReads(sessionId);
    applyChatSessionSnapshot(session);
    if (sessionId !== activeSessionId) return;
    render();
    closeChatPreferences();
    setDialogueStatus(`本会话已切换为「${currentPersonaTitle()}」，从下一句开始生效。`, "success");
  } catch (error) {
    personaSaveRequests.delete(sessionId);
    invalidatePersonaReads(sessionId);
    personaSaveErrors.set(sessionId, personaRequestError(error));
    if (sessionId !== activeSessionId) return;
    renderAgentOverlays();
    setDialogueStatus("未能确认聊天风格已保存，请重试。", "error");
  }
}

async function refreshSkills() {
  if (!state.online) return;
  try {
    chatSkills = await fetchChatSkills();
  } catch {
    // Skill list is cosmetic; keep the last snapshot.
  }
  if (skillsSheetOpen) renderAgentOverlays();
}

async function refreshSessions() {
  if (!state.online) return;
  const requestRevision = personaRevision;
  try {
    const sessions = await fetchChatSessions();
    chatSessions = sessions.map((session) => mergeChatSessionSnapshot(session, requestRevision));
  } catch {
    // Keep the last list while offline.
  }
  if (sessionsDrawerOpen || skillsSheetOpen) renderAgentOverlays();
}

async function refreshApprovals() {
  if (!state.online) return false;
  try {
    // pending 之外同时拉 executing（回放恢复中间态），并在有本页批准的
    // 审批时拉全量快照跟踪到终态；挂在既有 2.5s 历史刷新节奏上。
    const [next, executing] = await Promise.all([
      fetchChatApprovals({ status: "pending" }),
      fetchChatApprovals({ status: "executing" }),
    ]);
    let changed = JSON.stringify(next) !== JSON.stringify(pendingApprovals);
    pendingApprovals = next;
    approvalExecutingOverrides = new Map(executing.map((record) => [record.approval_id, record]));
    if (executingApprovals.size) {
      const all = await fetchChatApprovals({ status: "", limit: 100 });
      for (const [approvalId, turnId] of [...executingApprovals]) {
        const record = all.find((item) => item.approval_id === approvalId);
        if (!record || !isApprovalTerminalStatus(record.status)) continue;
        executingApprovals.delete(approvalId);
        changed = settleTrackedApproval(approvalId, turnId, record) || changed;
      }
    }
    return changed;
  } catch {
    // 503 (approval gate unwired) or offline: keep the last snapshot.
    return false;
  }
}

async function refreshAgentTasks() {
  if (!state.online) return;
  try {
    const { items } = await fetchAgentTasks({ limit: 50 });
    agentTasks = items;
  } catch {
    // Keep the last list.
  }
  if (tasksOverlayOpen) renderAgentOverlays();
}

async function openTaskDetail(taskId, { silent = false } = {}) {
  if (!taskId) return;
  if (!silent) {
    tasksOverlayOpen = true;
    renderAgentOverlays();
  }
  try {
    agentTaskDetail = await fetchAgentTask(taskId);
  } catch (error) {
    if (!silent) setDialogueStatus(contextErrorMessage(error), "error");
  }
  if (tasksOverlayOpen) renderAgentOverlays();
  syncTasksPolling();
}

function syncTasksPolling() {
  const shouldPoll = tasksOverlayOpen
    && (agentTasks.some((task) => isAgentTaskActive(task?.status))
      || (agentTaskDetail && isAgentTaskActive(agentTaskDetail.status)));
  if (shouldPoll && tasksPollTimer === null) {
    tasksPollTimer = window.setInterval(() => {
      void refreshAgentTasks();
      if (agentTaskDetail && isAgentTaskActive(agentTaskDetail.status)) {
        void openTaskDetail(agentTaskDetail.task_id, { silent: true });
      }
      syncTasksPolling();
    }, 4000);
  } else if (!shouldPoll && tasksPollTimer !== null) {
    window.clearInterval(tasksPollTimer);
    tasksPollTimer = null;
  }
}

async function switchSession(sessionId) {
  if (!sessionId || sessionId === activeSessionId) {
    sessionsDrawerOpen = false;
    renderAgentOverlays();
    return;
  }
  const input = $root?.querySelector("#chat-input");
  sessionDrafts.set(activeSessionId, input?.value ?? retainedDraft);
  retainedDraft = sessionDrafts.get(sessionId) || "";
  if (input) input.value = retainedDraft;
  activeSessionId = sessionId;
  historyRefreshGeneration += 1;
  historyRefreshInFlight = false;
  try {
    globalThis.localStorage?.setItem(CHAT_SESSION_STORAGE_KEY, sessionId);
  } catch { /* storage unavailable */ }
  turns = [];
  historyLoaded = false;
  lastHistorySignature = null;
  pendingTurnId = null;
  sending = [...streamingTurnIds].some((id) => agentRunsByTurnId.get(id)?.sessionId === sessionId);
  sessionsDrawerOpen = false;
  renderAgentOverlays();
  render();
  await loadHistory();
}

async function handleCreateSession() {
  try {
    const session = await createChatSession({});
    await refreshSessions();
    await switchSession(session?.session_id || "");
    setDialogueStatus("新会话已建好，说点什么吧。", "success");
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
  }
}

async function handleArchiveSession(sessionId) {
  try {
    await updateChatSession(sessionId, { archived: true });
    if (sessionId === activeSessionId) await switchSession("default");
    await refreshSessions();
    setDialogueStatus("会话已归档。", "info");
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
  }
}

async function handleRenameSession(sessionId, title) {
  const trimmed = String(title || "").trim();
  if (!trimmed) return;
  try {
    await updateChatSession(sessionId, { title: trimmed });
    sessionRenameId = "";
    await refreshSessions();
    renderAgentOverlays();
    setDialogueStatus("会话已改名。", "success");
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
  }
}

// ── Agent overlays (sessions drawer / skills sheet / task center) ──
function ensureAgentOverlayHost() {
  let host = document.querySelector(".agent-overlay-host");
  if (!(host instanceof HTMLElement)) {
    host = document.createElement("div");
    host.className = "agent-overlay-host";
    document.body.appendChild(host);
  }
  return host;
}

function closeChatPreferences() {
  skillsSheetOpen = false;
  renderAgentOverlays();
  $root?.querySelector(".chat-agent-skill-chip")?.focus();
}

function renderAgentOverlays() {
  const host = ensureAgentOverlayHost();
  const previousRenameInput = host.querySelector(".agent-session-rename-input");
  const renameHadFocus = previousRenameInput === document.activeElement;
  const renameSelection = previousRenameInput
    ? [previousRenameInput.selectionStart, previousRenameInput.selectionEnd] : null;
  const previousPreferenceFocus = host.querySelector(".agent-preferences-panel :focus");
  const previousPreferenceScroll = host.querySelector(".agent-preferences-panel .agent-drawer-list")?.scrollTop || 0;
  host.innerHTML = "";

  if (sessionsDrawerOpen) {
    const drawer = document.createElement("div");
    drawer.className = "agent-drawer-overlay";
    drawer.innerHTML = `
      <div class="agent-drawer" role="dialog" aria-modal="true" aria-label="会话列表">
        <div class="agent-drawer-head">
          <span class="agent-drawer-title">会话</span>
          <button type="button" class="agent-btn agent-btn-secondary" data-drawer-new>新建会话</button>
          <button type="button" class="agent-drawer-close" data-drawer-close aria-label="关闭">✕</button>
        </div>
        <div class="agent-drawer-list">
          ${chatSessions.length === 0 ? '<p class="agent-drawer-empty">还没有会话，新建一个开始。</p>' : ""}
          ${chatSessions.map((session) => `
            <div class="agent-session-row${session.session_id === activeSessionId ? " is-active" : ""}" data-session-id="${esc(session.session_id)}">
              <button type="button" class="agent-session-main" data-session-switch="${esc(session.session_id)}">
                <span class="agent-session-title">${esc(session.title || "新会话")}</span>
                <span class="agent-session-preview">${esc(session.last_message_preview || "")}</span>
              </button>
              ${Number(session.active_turns) > 0 ? '<span class="agent-session-active" title="正在回复">●</span>' : ""}
              <button type="button" class="agent-btn agent-btn-ghost" data-session-rename="${esc(session.session_id)}">改名</button>
              ${session.session_id !== "default" ? `<button type="button" class="agent-btn agent-btn-ghost" data-session-archive="${esc(session.session_id)}">归档</button>` : ""}
            </div>
            ${sessionRenameId === session.session_id ? `
              <div class="agent-session-rename">
                <input type="text" class="agent-session-rename-input" data-session-rename-input="${esc(session.session_id)}" value="${esc(session.title || "")}" maxlength="60" placeholder="会话名">
                <button type="button" class="agent-btn agent-btn-primary" data-session-rename-submit="${esc(session.session_id)}">保存</button>
              </div>` : ""}
          `).join("")}
        </div>
      </div>`;
    drawer.addEventListener("click", (event) => {
      if (event.target === drawer) {
        sessionsDrawerOpen = false;
        renderAgentOverlays();
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest("[data-drawer-close]")) {
        sessionsDrawerOpen = false;
        renderAgentOverlays();
      } else if (target.closest("[data-drawer-new]")) {
        void handleCreateSession();
      } else {
        const switchBtn = target.closest("[data-session-switch]");
        const renameBtn = target.closest("[data-session-rename]");
        const renameSubmit = target.closest("[data-session-rename-submit]");
        const archiveBtn = target.closest("[data-session-archive]");
        if (renameSubmit instanceof HTMLElement) {
          const input = drawer.querySelector(".agent-session-rename-input");
          void handleRenameSession(renameSubmit.dataset.sessionRenameSubmit || "", input?.value || "");
        } else if (renameBtn instanceof HTMLElement) {
          sessionRenameId = renameBtn.dataset.sessionRename || "";
          renderAgentOverlays();
          host.querySelector(".agent-session-rename-input")?.focus();
        } else if (archiveBtn instanceof HTMLElement) {
          void handleArchiveSession(archiveBtn.dataset.sessionArchive || "");
        } else if (switchBtn instanceof HTMLElement) {
          void switchSession(switchBtn.dataset.sessionSwitch || "");
        }
      }
    });
    host.appendChild(drawer);
    const renameInput = drawer.querySelector(".agent-session-rename-input");
    if (renameInput && previousRenameInput?.dataset.sessionRenameInput === sessionRenameId) {
      // Background session/history refreshes must not replace an unfinished edit.
      renameInput.value = previousRenameInput.value;
      if (renameHadFocus) {
        renameInput.focus({ preventScroll: true });
        renameInput.setSelectionRange(...renameSelection);
      }
    }
  }

  if (skillsSheetOpen) {
    const sheet = document.createElement("div");
    sheet.className = "agent-drawer-overlay";
    const skillName = currentSkillName();
    const personaId = currentPersonaId();
    const savingPersona = personaSaveRequests.get(activeSessionId);
    const personaError = personaSaveErrors.get(activeSessionId);
    sheet.innerHTML = `
      <div class="agent-drawer agent-preferences-panel" role="dialog" aria-modal="true" aria-label="角色与聊天风格">
        <div class="agent-drawer-head">
          <span class="agent-drawer-title">角色与聊天风格</span>
          <button type="button" class="agent-drawer-close" data-sheet-close aria-label="关闭">✕</button>
        </div>
        <div class="agent-preference-tabs" role="tablist" aria-label="设置类型">
          <button type="button" id="chat-persona-tab" role="tab" aria-selected="${chatPreferenceTab === "persona"}" aria-controls="chat-persona-options" data-preference-tab="persona">聊天风格</button>
          <button type="button" id="chat-skill-tab" role="tab" aria-selected="${chatPreferenceTab === "skill"}" aria-controls="chat-skill-options" data-preference-tab="skill">功能角色</button>
        </div>
        ${chatPreferenceTab === "persona" ? `
        <div class="agent-drawer-list" id="chat-persona-options" role="tabpanel" aria-labelledby="chat-persona-tab" aria-busy="${Boolean(savingPersona)}">
          <p class="agent-preference-hint">选择阿B说话的方式。保存后仅对本会话的下一句起生效，功能角色保持不变。</p>
          ${chatPersonaExamplePrompt ? `<p class="agent-preference-hint">示例回应：${esc(chatPersonaExamplePrompt)}</p>` : ""}
          ${savingPersona ? '<p class="agent-preference-status" role="status">正在保存聊天风格…</p>' : ""}
          ${personaError ? `<p class="agent-preference-error" role="alert">尚未确认保存：${esc(personaError)}。请刷新或重试。</p>` : ""}
          ${personaCatalogError ? `<p class="agent-preference-error" role="alert">聊天风格暂不可用：${esc(personaCatalogError)}</p><button type="button" class="agent-btn agent-btn-secondary agent-persona-retry" data-persona-retry>重新加载</button>` : ""}
          ${!personaCatalogError && chatPersonas.length === 0 ? '<p class="agent-drawer-empty" role="status">聊天风格加载中…</p>' : ""}
          ${!personaId && chatPersonas.length > 0 ? '<p class="agent-preference-hint" role="status">正在读取本会话的风格…</p>' : ""}
          ${chatPersonas.map((persona) => `
            <button type="button" class="agent-skill-row agent-persona-row${persona.id === personaId ? " is-active" : ""}" data-persona-pick="${esc(persona.id)}" aria-pressed="${persona.id === personaId}" ${savingPersona || !personaId || personaCatalogError ? "disabled" : ""}>
              <span class="agent-skill-row-title">${esc(persona.title)}${persona.id === personaId ? " · 当前" : persona.default ? "（默认）" : ""}</span>
              <span class="agent-skill-row-desc">${esc(persona.description || "")}</span>
              <span class="agent-persona-example">例如：${esc(persona.example || "")}</span>
            </button>
          `).join("")}
        </div>` : `
        <div class="agent-drawer-list" id="chat-skill-options" role="tabpanel" aria-labelledby="chat-skill-tab">
          <p class="agent-preference-hint">选择阿B能帮你做什么，聊天风格保持不变。</p>
          ${chatSkills.length === 0 ? '<p class="agent-drawer-empty">角色列表加载中…</p>' : ""}
          ${chatSkills.map((skill) => `
            <button type="button" class="agent-skill-row${skill.name === skillName || (!skillName && skill.isDefault) ? " is-active" : ""}" data-skill-pick="${esc(skill.name)}">
              <span class="agent-skill-row-title">${esc(skill.title)}${skill.isDefault ? "（默认）" : ""}</span>
              <span class="agent-skill-row-desc">${esc(skill.description)}</span>
            </button>
          `).join("")}
        </div>`}
      </div>`;
    sheet.addEventListener("click", (event) => {
      if (event.target === sheet || (event.target instanceof Element && event.target.closest("[data-sheet-close]"))) {
        closeChatPreferences();
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      const tab = target?.closest("[data-preference-tab]");
      if (tab instanceof HTMLElement) {
        chatPreferenceTab = tab.dataset.preferenceTab === "skill" ? "skill" : "persona";
        renderAgentOverlays();
        host.querySelector(`[data-preference-tab="${chatPreferenceTab}"]`)?.focus();
        return;
      }
      if (target?.closest("[data-persona-retry]")) {
        void refreshPersonas();
        return;
      }
      const personaPick = target?.closest("[data-persona-pick]");
      if (personaPick instanceof HTMLElement) {
        void saveChatPersona(personaPick.dataset.personaPick || "");
        return;
      }
      const pick = event.target instanceof Element ? event.target.closest("[data-skill-pick]") : null;
      if (pick instanceof HTMLElement) {
        setSessionSkill(activeSessionId, pick.dataset.skillPick || "");
        render();
        closeChatPreferences();
        setDialogueStatus(`已切换到「${skillDisplayTitle(pick.dataset.skillPick || "", chatSkills)}」。`, "success");
      }
    });
    sheet.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeChatPreferences();
        return;
      }
      if (event.key === "Tab") {
        const buttons = [...sheet.querySelectorAll("button:not(:disabled)")];
        const first = buttons[0];
        const last = buttons.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
        return;
      }
      const tab = event.target instanceof Element ? event.target.closest("[data-preference-tab]") : null;
      if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      chatPreferenceTab = event.key === "Home" ? "persona" : event.key === "End" ? "skill"
        : chatPreferenceTab === "persona" ? "skill" : "persona";
      renderAgentOverlays();
      host.querySelector(`[data-preference-tab="${chatPreferenceTab}"]`)?.focus();
    });
    host.appendChild(sheet);
    // Refreshes can replace the sheet while the catalog or session arrives.
    // Retain its focused control and reading position; first open enters the
    // active tab so keyboard users stay inside the modal immediately.
    const restoredFocus = previousPreferenceFocus && [...sheet.querySelectorAll("button:not(:disabled)")].find((button) =>
      ["personaPick", "preferenceTab", "skillPick"].some((key) => previousPreferenceFocus.dataset[key]
        && previousPreferenceFocus.dataset[key] === button.dataset[key])
      || (previousPreferenceFocus.hasAttribute("data-sheet-close") && button.hasAttribute("data-sheet-close"))
      || (previousPreferenceFocus.hasAttribute("data-persona-retry") && button.hasAttribute("data-persona-retry")));
    (restoredFocus || sheet.querySelector(`[data-preference-tab="${chatPreferenceTab}"]`))?.focus({ preventScroll: true });
    const list = sheet.querySelector(".agent-drawer-list");
    if (list) list.scrollTop = previousPreferenceScroll;
  }

  if (tasksOverlayOpen) {
    const overlay = document.createElement("div");
    overlay.className = "agent-drawer-overlay";
    const detail = agentTaskDetail;
    overlay.innerHTML = `
      <div class="agent-drawer agent-tasks-panel" role="dialog" aria-modal="true" aria-label="任务中心">
        <div class="agent-drawer-head">
          ${detail ? '<button type="button" class="agent-btn agent-btn-ghost" data-tasks-back>← 列表</button>' : ""}
          <span class="agent-drawer-title">${detail ? "任务详情" : "任务中心"}</span>
          <button type="button" class="agent-drawer-close" data-tasks-close aria-label="关闭">✕</button>
        </div>
        <div class="agent-drawer-list" data-tasks-list>
          ${detail
            ? renderAgentTaskDetailMarkup(detail, { markdown: renderMarkdown })
            : agentTasks.length === 0
              ? '<p class="agent-drawer-empty">还没有后台任务。对话中阿B 会建议把长任务放到这里。</p>'
              : agentTasks.map((task) => renderAgentTaskRowMarkup(task)).join("")}
        </div>
      </div>`;
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || (event.target instanceof Element && event.target.closest("[data-tasks-close]"))) {
        tasksOverlayOpen = false;
        agentTaskDetail = null;
        renderAgentOverlays();
        syncTasksPolling();
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest("[data-tasks-back]")) {
        agentTaskDetail = null;
        renderAgentOverlays();
        return;
      }
      handleAgentActionClick(event);
    });
    host.appendChild(overlay);
  }
}

// Keep the live composer attached while rebuilding history and cards. Removing
// a focused textarea resets selection, scroll and IME composition on mobile.
function mountChatShell(root, shell) {
  const previousShell = root.querySelector(".chat-shell");
  const previousRow = previousShell?.querySelector(".chat-input-row");
  const previousInput = previousRow?.querySelector("#chat-input");
  const nextRow = shell.querySelector(".chat-input-row");
  const nextInput = nextRow?.querySelector("#chat-input");
  if (!previousShell || !previousRow || !previousInput || !nextInput) {
    root.replaceChildren(shell);
    return nextInput;
  }
  if (previousInput.value !== nextInput.value) previousInput.value = nextInput.value;
  previousInput.placeholder = nextInput.placeholder;
  previousRow.querySelector("#chat-send").disabled = nextRow.querySelector("#chat-send").disabled;
  for (const child of [...previousShell.children]) {
    if (child !== previousRow) child.remove();
  }
  let afterComposer = false;
  for (const child of [...shell.children]) {
    if (child === nextRow) afterComposer = true;
    else if (afterComposer) previousShell.appendChild(child);
    else previousShell.insertBefore(child, previousRow);
  }
  return previousInput;
}

function render() {
  if (!$root) return;
  const approvalDrafts = captureApprovalDrafts($root);
  const previousMessages = $root.querySelector("#chat-messages");
  const previousPendingList = $root.querySelector("#mobile-chat-pending-list");
  const previousInput = $root.querySelector("#chat-input");
  const previousScrollTop = previousMessages?.scrollTop || 0;
  const previousPendingScrollTop = previousPendingList?.scrollTop || 0;
  const shouldStickToBottom = !previousMessages || isNearChatBottom(previousMessages);
  const openEvidence = openEvidenceTurnIds(previousMessages);
  const previousDraft = previousInput instanceof HTMLTextAreaElement
    ? previousInput.value || retainedDraft
    : retainedDraft;
  const restoreInputFocus = document.activeElement === previousInput;

  const shell = document.createElement("div");
  shell.className = "chat-shell";

  shell.appendChild(createChatTopbar());
  shell.appendChild(createPendingPanel(previousPendingScrollTop));
  shell.appendChild(createApprovalsPanel());

  // Messages area
  const messages = document.createElement("div");
  messages.className = "chat-messages";
  messages.id = "chat-messages";
  messages.tabIndex = 0;
  messages.setAttribute("role", "region");
  messages.setAttribute("aria-label", "口味对话记录");

  const dialogueTurns = selectDialogueTurns(turns);
  dialogueTurnsById.clear();
  const historyViewState = getChatHistoryViewState({
    historyLoaded,
    turnCount: dialogueTurns.length,
    sending,
  });
  if (historyViewState === "loading") {
    messages.innerHTML = `<div class="chat-history-loading" role="status"><div class="spinner"></div><div class="chat-history-loading-text">正在加载聊天记录…</div></div>`;
  } else if (historyViewState === "empty") {
    messages.innerHTML = `<div class="empty-state"><div class="empty-state-icon">\u{1F4AC}</div><div class="empty-state-text">\u548C AI \u804A\u804A\u4F60\u7684\u5174\u8DA3\u548C\u60F3\u6CD5</div></div>`;
  }

  for (const turn of dialogueTurns) {
    if (turn?.turn_id) dialogueTurnsById.set(turn.turn_id, turn);
    const container = document.createElement("div");
    container.className = "dialogue-turn";
    container.dataset.dialogueTurnContainer = turn?.turn_id || "";
    container.innerHTML = `${replyQuoteMarkup(turn, dialogueTurns)}${renderTurnMarkup(turn, { surface: "desktop" })}`;
    // Agent loop process flow: live runs stream expanded; completed turns
    // replay payload.agent_events as a collapsed, expandable summary.
    const agentRun = agentRunForTurn(turn);
    if (agentRunHasContent(agentRun)) {
      const runSlot = document.createElement("div");
      runSlot.className = "agent-run-live-slot";
      runSlot.innerHTML = renderAgentRunMarkup(agentRun, { collapsed: agentRun.settled });
      const assistantBubble = container.querySelector('[data-part="assistant"]');
      container.insertBefore(runSlot, assistantBubble || null);
    }
    if (isAgentTaskSummaryTurn(turn)) {
      const summarySlot = document.createElement("div");
      summarySlot.innerHTML = renderAgentTaskSummaryMarkup(turn.payload, { markdown: renderMarkdown });
      container.appendChild(summarySlot);
    }
    if (
      !isCardTurn(turn) &&
      !isQuestionTurn(turn) &&
      !turn.response &&
      (turn.status === "pending" || turn.status === "processing")
    ) {
      const thinking = document.createElement("div");
      thinking.className = "chat-bubble thinking";
      thinking.innerHTML = `<div class="spinner" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:6px"></div>\u601D\u8003\u4E2D\u2026`;
      container.appendChild(thinking);
    }
    if (turn.status === "error" || turn.status === "failed") {
      const errBubble = container.querySelector('[data-part="assistant"]');
      if (errBubble) {
        errBubble.textContent = turn.error || "\u56DE\u590D\u5931\u8D25";
      }
      const retryBtn = document.createElement("button");
      retryBtn.className = "chat-retry-btn";
      retryBtn.type = "button";
      retryBtn.textContent = "\u91CD\u8BD5";
      retryBtn.addEventListener("click", () => retryTurn(turn));
      container.appendChild(retryBtn);
    }
    messages.appendChild(container);
  }

  // Scroll tracking
  messages.addEventListener("scroll", () => {
    userScrolledUp = !isNearChatBottom(messages);
  });
  messages.addEventListener("click", (event) => {
    activateReplyQuote(event, messages);
    handleAgentActionClick(event);
    const button = event.target instanceof Element
      ? event.target.closest("[data-card-action]")
      : null;
    if (button instanceof HTMLButtonElement) void handleDialogueCardAction(button);
  });

  shell.appendChild(messages);

  const contextMarkup = contextBarMarkup(dialogueContextSelection);
  if (contextMarkup) {
    const contextBar = document.createElement("div");
    contextBar.innerHTML = contextMarkup;
    const clearButton = contextBar.querySelector("[data-context-clear]");
    clearButton?.addEventListener("click", () => {
      storeDialogueContext(clearContextSelection());
      setDialogueStatus("已清除这条消息的对话上下文。", "info");
      render();
    });
    shell.appendChild(contextBar.firstElementChild || contextBar);
  }

  // Input row
  const inputRow = document.createElement("div");
  inputRow.className = "chat-input-row";

  let textarea = document.createElement("textarea");
  textarea.className = "chat-input";
  textarea.id = "chat-input";
  textarea.placeholder = PLACEHOLDERS[placeholderIdx];
  textarea.rows = 2;
  textarea.value = previousDraft;
  textarea.addEventListener("input", autoGrow);
  textarea.addEventListener("focus", () => { inputFocused = true; });
  textarea.addEventListener("blur", () => { inputFocused = false; });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      handleSend();
    }
  });

  // Pre-fill from contextual chat context
  if (state.pendingChatContext && !sending) {
    const ctx = state.pendingChatContext;
    textarea.value = `\u5173\u4E8E\u300C${ctx.subjectTitle || ctx.subjectId}\u300D\uFF0C\u6211\u60F3\u804A\u804A`;
    patchState({ pendingChatContext: null });
  }

  const sendBtn = document.createElement("button");
  sendBtn.className = "chat-send-btn";
  sendBtn.id = "chat-send";
  sendBtn.type = "button";
  sendBtn.setAttribute("aria-label", "发送消息");
  sendBtn.innerHTML = "\u{1F4E8}";
  sendBtn.disabled = sending;
  sendBtn.addEventListener("click", handleSend);

  inputRow.appendChild(textarea);
  inputRow.appendChild(sendBtn);
  shell.appendChild(inputRow);

  const status = document.createElement("p");
  status.className = "chat-status";
  status.setAttribute("aria-live", "polite");
  status.dataset.tone = dialogueStatus.tone;
  status.textContent = dialogueStatus.message;
  status.hidden = !dialogueStatus.message;
  shell.appendChild(status);

  textarea = mountChatShell($root, shell);
  autoGrow({ target: textarea });
  restoreApprovalDrafts($root, approvalDrafts);

  for (const details of messages.querySelectorAll(".dialogue-evidence")) {
    const turnId = details.closest("[data-dialogue-turn-id]")?.dataset.dialogueTurnId || "";
    if (openEvidence.has(turnId)) details.open = true;
  }

  // Auto-scroll only while the reader is already following the newest turn.
  if (!userScrolledUp || shouldStickToBottom) {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  } else {
    messages.scrollTop = Math.min(
      previousScrollTop,
      Math.max(0, messages.scrollHeight - messages.clientHeight),
    );
  }
  if (restoreInputFocus && document.activeElement !== textarea) {
    requestAnimationFrame(() => textarea.focus({ preventScroll: true }));
  }

  // Start placeholder carousel
  startPlaceholderCarousel();

  // Render overlay if open
  renderOverlay();
  renderAgentOverlays();
}

function autoGrow(e) {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(Math.max(el.scrollHeight, 60), 112) + "px";
}

function startPlaceholderCarousel() {
  if (placeholderTimer) clearInterval(placeholderTimer);
  placeholderTimer = setInterval(() => {
    if (inputFocused) return;
    placeholderIdx = (placeholderIdx + 1) % PLACEHOLDERS.length;
    const input = document.getElementById("chat-input");
    if (input && !input.value) {
      input.placeholder = PLACEHOLDERS[placeholderIdx];
    }
  }, 4000);
}

function chatHistorySignature(nextTurns) {
  return JSON.stringify(nextTurns);
}

function trackPendingHistoryTurn(nextTurns) {
  const last = [...nextTurns].reverse().find((turn) => turn.scope === "chat");
  if (!last || (last.status !== "pending" && last.status !== "processing")) return;
  if (pendingTurnId === last.turn_id || streamingTurnIds.has(last.turn_id)) return;
  // Agent loop turns are re-driven through the streaming endpoint (a pending
  // streaming turn means the previous stream died with the page); the
  // durable scheduler skips them, so polling alone would never settle.
  if (agentLoopAvailable) {
    void driveAgentStream(last.turn_id, last.message || "", last);
    return;
  }
  pendingTurnId = last.turn_id;
  sending = true;
  pollForResponse();
}

// ── Agent loop send path (M9) ────────────────────────────────
function finalizeAgentTurn(turnId, { reply = "", error = "" } = {}) {
  const t = turns.find((item) => item.turn_id === turnId);
  if (t) {
    if (error) {
      t.status = "failed";
      t.error = error;
    } else {
      t.status = "completed";
      t.reply = reply;
      t.response = reply;
    }
  }
  streamingTurnIds.delete(turnId);
  sending = [...streamingTurnIds].some((id) => agentRunsByTurnId.get(id)?.sessionId === activeSessionId);
}

async function finalizeAgentTurnSuccess(turnId, reply) {
  const sessionId = agentRunsByTurnId.get(turnId)?.sessionId;
  finalizeAgentTurn(turnId, { reply });
  if (sessionId && sessionId !== activeSessionId) return;
  setDialogueStatus("这句已经记下了。", "success");
  render();
  void Promise.allSettled([
    refreshAfterChatTurn(),
    refreshPendingConfirmations(),
    refreshApprovals(),
  ]);
  // Session titles generate asynchronously on the first message; refresh the
  // drawer once shortly after the reply lands.
  window.setTimeout(() => void refreshSessions(), 4000);
  await loadHistory();
}

async function driveAgentStream(turnId, message, sourceTurn = null) {
  if (streamingTurnIds.has(turnId)) return;
  streamingTurnIds.add(turnId);
  const run = createAgentRun();
  const turn = sourceTurn || turns.find((item) => item.turn_id === turnId);
  const sessionId = turn?.session_id || activeSessionId;
  if (sessionId === activeSessionId) sending = true;
  run.sessionId = sessionId;
  agentRunsByTurnId.set(turnId, run);
  agentDeltaBuffers.set(turnId, "");
  try {
    const done = await streamAgentChatTurn({
      turnId,
      sessionId,
      skill: turn?.payload?.agent_skill || "",
      session: "popup",
      message,
      onEvent(name, data) {
        if (name === "delta") {
          // Token-level reply streaming: render fragments into the live
          // bubble immediately; ``final``/``done`` replace it wholesale.
          const next = (agentDeltaBuffers.get(turnId) || "") + String(data?.text || "");
          agentDeltaBuffers.set(turnId, next);
          updateLegacyReplyDom(turnId, next);
          return;
        }
        if (name === "thinking") {
          // The streamed hop text was intermediate reasoning, not the
          // reply: it moves into the process flow; reset the live bubble.
          agentDeltaBuffers.set(turnId, "");
          updateLegacyReplyDom(turnId, "");
        }
        applyAgentEvent(run, name, data);
        updateAgentRunDom(turnId);
        if (name === "approval_request") void refreshApprovals().then(render);
      },
    });
    await finalizeAgentTurnSuccess(turnId, run.finalText || done?.reply || "");
  } catch (error) {
    if (Number(error?.status) === 503) {
      // loop_enabled=false: permanent for this page load; fall back to the
      // legacy single-hop stream so the pending turn still completes.
      agentLoopAvailable = false;
      agentRunsByTurnId.delete(turnId);
      agentDeltaBuffers.delete(turnId);
      await driveLegacyStream(turnId, message);
      return;
    }
    const messageText = error?.agentStreamError
      ? String(error.message || "对话失败了，请稍后重试。")
      : "连接中断了，可以重试。";
    finalizeAgentTurn(turnId, { error: messageText });
    if (sessionId !== activeSessionId) return;
    setDialogueStatus(messageText, "error");
    render();
  } finally {
    agentDeltaBuffers.delete(turnId);
  }
}

async function driveLegacyStream(turnId, message) {
  legacyStreamReplies.set(turnId, "");
  try {
    const done = await streamChatTurnLegacy({
      turnId,
      ...chatSession(),
      message,
      onContent(delta) {
        legacyStreamReplies.set(turnId, (legacyStreamReplies.get(turnId) || "") + delta);
        const t = turns.find((item) => item.turn_id === turnId);
        if (t) {
          t.response = legacyStreamReplies.get(turnId);
          updateLegacyReplyDom(turnId, t.response);
        }
      },
    });
    legacyStreamReplies.delete(turnId);
    await finalizeAgentTurnSuccess(turnId, String(done?.reply || turns.find((item) => item.turn_id === turnId)?.response || ""));
  } catch {
    legacyStreamReplies.delete(turnId);
    // Last resort: the background reply worker does not pick up streaming
    // turns, so surface a retryable error instead of polling forever.
    finalizeAgentTurn(turnId, { error: "发送失败了，可以重试。" });
    setDialogueStatus("发送失败了，可以重试。", "error");
    render();
  }
}

function updateLegacyReplyDom(turnId, text) {
  const messages = document.getElementById("chat-messages");
  if (!(messages instanceof HTMLElement)) return;
  const container = messages.querySelector(
    `[data-dialogue-turn-container="${CSS.escape(turnId)}"]`,
  );
  if (!(container instanceof HTMLElement)) return;
  let bubble = container.querySelector(".chat-bubble.thinking");
  if (!(bubble instanceof HTMLElement)) {
    bubble = document.createElement("div");
    bubble.className = "chat-bubble thinking";
    container.appendChild(bubble);
  }
  bubble.innerHTML = renderMarkdown(text || "…");
  if (!userScrolledUp || isNearChatBottom(messages)) {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  }
}

// ── Send ─────────────────────────────────────────────────────
async function handleSend() {
  const input = document.getElementById("chat-input");
  const text = input?.value?.trim();
  if (!text || sending) return;

  sending = true;
  const sessionId = activeSessionId;
  const skill = currentSkillName();
  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const replyToTurnId = dialogueContextSelection?.reply_to_turn_id || "";

  retainedDraft = "";
  input.value = "";
  turns.push({
    turn_id: turnId,
    session_id: sessionId,
    scope: "chat",
    created_at: new Date().toISOString(),
    message: text,
    response: null,
    status: "pending",
    reply_to_turn_id: replyToTurnId,
  });
  userScrolledUp = false;
  setDialogueStatus("阿B 正在整理这句话…", "info");
  render();

  try {
    const turn = await startChatTurn({
      turnId,
      ...chatSession(),
      replyToTurnId,
      message: text,
      sessionId,
      skill,
      streaming: agentLoopAvailable,
    });
    if (agentLoopAvailable) {
      await driveAgentStream(turnId, text, turn);
    } else if (sessionId === activeSessionId) {
      pendingTurnId = turnId;
      pollForResponse();
    }
  } catch (error) {
    if (sessionId !== activeSessionId) return;
    const t = turns.find((t) => t.turn_id === turnId);
    if (t) { t.status = "error"; t.error = "\u53D1\u9001\u5931\u8D25"; }
    retainedDraft = text;
    sending = false;
    setDialogueStatus(contextErrorMessage(error), "error");
    render();
  }
}

async function retryTurn(failedTurn) {
  if (sending) return;
  const sessionId = failedTurn.session_id || activeSessionId;
  failedTurn.status = "pending";
  failedTurn.error = "";
  retainedDraft = "";
  sending = true;
  render();

  try {
    await startChatTurn({
      turnId: failedTurn.turn_id,
      sessionId,
      ...chatSession(failedTurn.scope || "chat"),
      message: failedTurn.message,
      subjectId: failedTurn.subject_id || "",
      subjectTitle: failedTurn.subject_title || "",
      replyToTurnId: failedTurn.reply_to_turn_id || "",
    });
    if (sessionId !== activeSessionId) return;
    pendingTurnId = failedTurn.turn_id;
    pollForResponse();
  } catch (error) {
    if (sessionId !== activeSessionId) return;
    failedTurn.status = "error";
    failedTurn.error = "\u91CD\u8BD5\u5931\u8D25";
    sending = false;
    retainedDraft = failedTurn.message || "";
    setDialogueStatus(contextErrorMessage(error), "error");
    render();
  }
}

function updateDialogueTurn(turn) {
  if (!turn?.turn_id) return;
  if (turn.session_id && turn.session_id !== activeSessionId) return;
  const normalized = normalizeChatTurn(turn);
  const index = turns.findIndex((item) => item?.turn_id === normalized.turn_id);
  if (index >= 0) turns[index] = normalized;
  else turns.push(normalized);
  render();
}

export async function refreshPendingConfirmations({ renderNow = true } = {}) {
  if (!state.online) {
    if (state.pendingConfirmationCount !== 0) patchState({ pendingConfirmationCount: 0 });
    return;
  }
  try {
    const payload = await fetchPendingConfirmations({ session: "popup" });
    const count = Math.max(0, Number(payload?.total ?? payload?.count) || 0);
    pendingConfirmations = {
      ...pendingConfirmations,
      count,
      items: Array.isArray(payload?.items) ? payload.items : [],
    };
    if (state.pendingConfirmationCount !== count) patchState({ pendingConfirmationCount: count });
    if (renderNow) render();
  } catch {
    // Preserve the last successful list while the backend reconnects.
  }
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
      setDialogueStatus(
        reason === "stale_anchor" || reason === "anchor_dependency_failed"
          ? "你正在聊另一条，先结束那条再结算这张卡。"
          : "后端结果暂未同步，可以刷新或直接重试。",
        "error",
      );
      return;
    }
    if (action === "discuss") {
      await selectDialogueContext(turnId, response?.context_preview || null);
    } else if (dialogueContextSelection?.reply_to_turn_id === turnId) {
      storeDialogueContext(clearContextSelection());
    }
    await loadHistory();
    setDialogueStatus(
      action === "discuss"
        ? "好，沿着这条猜测继续聊。"
        : action === "defer"
          ? "先放一放，之后再聊。"
          : response?.state === "revised"
            ? "已按你的修正记下。"
            : action === "confirm"
              ? "已确认这条猜测。"
              : "已记下这条猜测不准。",
      "success",
    );
  } catch (error) {
    setDialogueStatus(contextErrorMessage(error), "error");
    render();
  }
}

async function handlePendingConfirmationOpen(button) {
  const ref = button.dataset.confirmationRef || "";
  if (!ref || button.disabled) return;
  button.disabled = true;
  button.textContent = "打开中…";
  try {
    const turn = await executePendingConfirmationOpen(ref, {
      session: "popup",
      signal: dialogueCardActionAbortController.signal,
      request(_path, body, { signal } = {}) {
        return openPendingConfirmation(ref, { session: body.session, signal });
      },
      onWaiting({ message }) {
        button.textContent = "等待中…";
        setDialogueStatus(`${message}，空闲后会自动打开。`, "info");
      },
    });
    if (turn?.turn_id) {
      updateDialogueTurn(turn);
      await selectDialogueContext(turn.turn_id);
    }
    await loadHistory();
    userScrolledUp = false;
    setDialogueStatus(
      isQuestionTurn(turn) ? "这条疑惑已经放进对话里。" : "这张确认卡已经放进对话里。",
      "success",
    );
    render();
    $root?.querySelector("#chat-input")?.focus({ preventScroll: true });
  } catch (error) {
    button.disabled = false;
    button.textContent = "打开";
    if (Number(error?.status) === 409) {
      await refreshPendingConfirmations();
      setDialogueStatus("另一条疑惑正在聊，待聊列表已经同步。", "error");
    } else if (error?.name !== "AbortError") {
      const detail = String(error?.details?.detail?.message || "").trim();
      setDialogueStatus(detail || "这条待聊内容暂时打不开，请稍后重试。", "error");
    }
  }
}

function pollForResponse() {
  if (!pendingTurnId) return;
  const turnId = pendingTurnId;
  const sessionId = activeSessionId;
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (turnId !== pendingTurnId || sessionId !== activeSessionId) return;
    try {
      const turn = normalizeChatTurn(await fetchChatTurn(turnId));
      if (turnId !== pendingTurnId || sessionId !== activeSessionId) return;
      const idx = turns.findIndex((t) => t.turn_id === turnId);
      if (idx >= 0) turns[idx] = turn;

      if (turn.status === "done" || turn.status === "completed" || turn.response) {
        pendingTurnId = null;
        sending = false;
        userScrolledUp = false;
        setDialogueStatus("这句已经记下了。", "success");
        render();
        void Promise.allSettled([refreshAfterChatTurn(), refreshPendingConfirmations()]);
      } else if (turn.status === "error" || turn.status === "failed") {
        pendingTurnId = null;
        sending = false;
        setDialogueStatus("这句处理失败了，可以重试。", "error");
        render();
      } else {
        render();
        pollForResponse();
      }
    } catch {
      if (turnId === pendingTurnId && sessionId === activeSessionId) pollForResponse();
    }
  }, 1500);
}

// ── Messages Overlay ─────────────────────────────────────────
function renderOverlay() {
  let overlay = document.querySelector(".messages-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "messages-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) toggleMessages();
    });
    document.body.appendChild(overlay);
  }
  overlay.classList.toggle("open", overlayOpen);

  if (!overlayOpen) {
    overlay.innerHTML = "";
    return;
  }

  const panel = document.createElement("div");
  panel.className = "messages-panel";

  // Header
  const header = document.createElement("div");
  header.className = "messages-header";
  header.innerHTML = `<span class="messages-title">\u6D88\u606F</span>`;
  const closeBtn = document.createElement("button");
  closeBtn.className = "messages-close";
  closeBtn.textContent = "\u2715";
  closeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    overlayOpen = false;
    renderOverlay();
  });
  header.appendChild(closeBtn);
  panel.appendChild(header);

  // Probe notifications
  for (const n of notifications) {
    const domain = n.domain || n.title || "";
    const isAvoidance = (n.type || "") === "avoidance.probe";
    const isChallenge = !isAvoidance && isChallengeProbe(n);
    const actions = isAvoidance ? getAvoidanceProbeMessageActions() : getProbeMessageActions();
    const pending = pendingProbeAction(n.type, domain);
    const card = document.createElement("div");
    card.className = `message-card ${isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe"}`;
    card.dataset.probeDomain = domain;
    card.setAttribute("aria-busy", pending ? "true" : "false");
    card.classList.toggle("is-processing", Boolean(pending));
    const prompt = isAvoidance
      ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。"
      : isChallenge
        ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
      : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
    card.innerHTML = `
      <div class="message-card-type"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>${isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣探测"}</div>
      <div class="message-card-prompt">${esc(prompt)}</div>
      <div class="message-card-title">${esc(domain)}</div>
      <div class="message-card-body">${esc(n.description || n.reason || n.message || "")}</div>
      <div class="message-card-actions">
        ${actions.map((item) => `
          <button type="button" class="message-action-btn ${item.primary ? "primary" : "secondary"}" data-probe="${esc(item.action)}" data-probe-kind="${isAvoidance ? "avoidance" : "interest"}" data-domain="${esc(n.domain || "")}">${esc(item.label)}</button>
        `).join("")}
      </div>`;
    for (const button of card.querySelectorAll("button")) {
      button.disabled = Boolean(pending);
    }
    panel.appendChild(card);
  }

  if (notifications.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "messages-empty-state";
    emptyState.innerHTML = `
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>
        <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>
      </svg>
      <span class="messages-empty-title">暂时没有新消息</span>
      <span class="messages-empty-subtitle">兴趣探测会在这里出现</span>`;
    panel.appendChild(emptyState);
  }

  overlay.innerHTML = "";
  overlay.appendChild(panel);

  // Bind probe actions
  for (const btn of panel.querySelectorAll("[data-probe]")) {
    btn.addEventListener("click", async () => {
      const domain = btn.dataset.domain;
      const action = btn.dataset.probe;
      const isAvoidance = btn.dataset.probeKind === "avoidance";
      const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
      const card = btn.closest(".message-card");
      if (action === "chat") {
        expandInlineChatOnCard(card, {
          scope: isAvoidance ? "avoidance_probe" : "probe",
          subjectId: domain,
          subjectTitle: domain,
          placeholder: isAvoidance
            ? `聊聊你为什么想避开「${domain}」…`
            : `聊聊你对「${domain}」的想法…`,
        });
        return;
      }
      const key = probeNotificationKey(probeType, domain);
      if (!key || pendingProbeActions.has(key)) return;
      pendingProbeActions.set(key, { response: action });
      setProbeCardBusy(card, true);
      renderOverlay();
      try {
        await (isAvoidance
          ? respondToAvoidanceProbe(domain, action)
          : respondToProbe(domain, action));
        pendingProbeActions.delete(key);
        rememberHandledProbe(domain, probeType);
        notifications = removeProbeFromNotifications(notifications, domain, probeType);
        updateBadgeCount();
        renderOverlay();
      } catch {
        pendingProbeActions.delete(key);
        setProbeCardBusy(card, false);
        renderOverlay();
      }
    });
  }

  // Bind delight actions
  for (const btn of panel.querySelectorAll("[data-delight]")) {
    btn.addEventListener("click", async () => {
      const bvid = btn.dataset.bvid;
      const action = btn.dataset.delight;
      const title = btn.dataset.title || "";

      if (action === "chat") {
        const card = btn.closest(".message-card");
        expandInlineChatOnCard(card, {
          scope: "delight",
          subjectId: bvid,
          subjectTitle: title,
          placeholder: `聊聊你对「${title}」的想法…`,
        });
        return;
      }

      const { apiResponse, permanent } = getDelightActionState(action);
      btn.disabled = true;

      if (apiResponse) {
        try { await respondToDelight(bvid, apiResponse, title); } catch { /* best-effort */ }
      }
      if (permanent) {
        markDelightSent(bvid).catch(() => {});
        delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
        updateBadgeCount();
        renderOverlay();
      } else {
        delightMsgs = delightMsgs.map((d) =>
          d.bvid === bvid
            ? {
                ...d,
                state: action === "like" ? "liked" : action === "view" ? "viewed" : d.state,
                response_message: action === "like"
                  ? "好，这类多来点。"
                  : action === "view"
                    ? "已打开，阿B 会把这次点击当成强信号。"
                    : d.response_message,
              }
            : d
        );
        updateBadgeCount();
        renderOverlay();
      }

      if (action === "view") {
        const item = normalizeDelightCandidate({ bvid, title });
        const url = buildContentUrl(item);
        if (url) openContentUrl(url);
      }
    });
  }
}

function updateBadgeCount() {
  const msgs = { notifications: [...notifications], delights: [] };
  patchState({ messages: msgs });
  setUnreadCount(notifications.length);
}

// ── Load ─────────────────────────────────────────────────────
async function loadHistory() {
  if (!state.online || historyRefreshInFlight) {
    // Offline before the first snapshot: stop showing the loading indicator
    // instead of spinning forever; the next online sync refetches anyway.
    if (!state.online && !historyLoaded) {
      historyLoaded = true;
      render();
    }
    return;
  }
  historyRefreshInFlight = true;
  const sessionId = activeSessionId;
  const generation = ++historyRefreshGeneration;
  try {
    const [historyResult, pendingResult, approvalsResult] = await Promise.allSettled([
      fetchChatSessionDetail(sessionId, { limit: 100 }).catch(async (error) => {
        // Pre-M5 backends have no session detail endpoint; fall back to the
        // legacy flat history so the tab keeps working against older builds.
        if (Number(error?.status) === 404 || Number(error?.status) === 405) {
          return fetchChatTurns({ session: "popup", limit: 100 });
        }
        throw error;
      }),
      fetchPendingConfirmations({ session: "popup" }),
      refreshApprovals(),
    ]);
    if (generation !== historyRefreshGeneration || sessionId !== activeSessionId) return;
    let changed = false;
    if (historyResult.status === "fulfilled") {
      const data = historyResult.value;
      if (data?.session && applyChatSessionSnapshot(data.session)) {
        changed = true;
        if (skillsSheetOpen) renderAgentOverlays();
      }
      const nextTurns = Array.isArray(data?.items || data?.turns)
        ? (data.items || data.turns).map(normalizeChatTurn)
        : [];
      trackPendingHistoryTurn(nextTurns);
      const signature = chatHistorySignature(nextTurns);
      if (signature !== lastHistorySignature) {
        lastHistorySignature = signature;
        turns = nextTurns;
        // Durable history is now authoritative: drop settled live runs whose
        // turn replay is available from payload.agent_events.
        for (const [turnId, run] of agentRunsByTurnId) {
          if (run.settled && nextTurns.some((item) => item?.turn_id === turnId)) {
            agentRunsByTurnId.delete(turnId);
          }
        }
        changed = true;
      }
    }
    if (approvalsResult.status === "fulfilled" && approvalsResult.value === true) {
      changed = true;
    }
    if (pendingResult.status === "fulfilled") {
      const payload = pendingResult.value;
      const nextPending = {
        count: Math.max(0, Number(payload?.total ?? payload?.count) || 0),
        items: Array.isArray(payload?.items) ? payload.items : [],
      };
      if (
        nextPending.count !== pendingConfirmations.count ||
        JSON.stringify(nextPending.items) !== JSON.stringify(pendingConfirmations.items)
      ) {
        pendingConfirmations = { ...pendingConfirmations, ...nextPending };
        changed = true;
      }
      if (state.pendingConfirmationCount !== nextPending.count) {
        patchState({ pendingConfirmationCount: nextPending.count });
      }
    }
    const contextBefore = dialogueContextSelection?.reply_to_turn_id || "";
    await validateDialogueContext({ announce: true });
    if ((dialogueContextSelection?.reply_to_turn_id || "") !== contextBefore) {
      changed = true;
    }
    const firstLoad = !historyLoaded;
    historyLoaded = true;
    if (!changed && !firstLoad) return;
    // render captures the current reading position. A snapshot taken before
    // awaiting history would undo any scrolling or Send action during fetch.
    render();
  } catch {
    // Keep the last durable snapshot while offline.
  } finally {
    if (generation !== historyRefreshGeneration || sessionId !== activeSessionId) return;
    historyRefreshInFlight = false;
    // Even a failed first fetch must clear the loading indicator; the
    // periodic sync repaints with real data once the backend responds.
    if (!historyLoaded) {
      historyLoaded = true;
      render();
    }
  }
}

function startChatHistorySync() {
  if (historyRefreshTimer !== null) return;
  historyRefreshTimer = window.setInterval(() => {
    if (state.activeTab !== "chat" || document.hidden || !state.online) return;
    void loadHistory();
  }, CHAT_HISTORY_REFRESH_INTERVAL_MS);
}

// iOS suspends JS while locked/backgrounded and the OS may kill the SSE
// connection without ever settling reader.read(); on resume, immediately
// re-check history so a turn stuck in ``streamingTurnIds`` is re-driven by
// the durable polling path instead of waiting for the next interval tick.
function bindVisibilityResume() {
  if (visibilityResumeBound) return;
  visibilityResumeBound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (state.activeTab !== "chat" || !state.online) return;
    void loadHistory();
  });
}

function syncChatViewport() {
  const viewport = window.visualViewport;
  const height = viewport?.height || window.innerHeight;
  const focused = Boolean(document.activeElement?.matches(
    ".chat-input, .agent-overlay-host input, .agent-overlay-host textarea",
  ));
  // A 400px viewport left only 14px for history in the mobile acceptance run.
  // Compact focused small windows too; a >100px visual/layout gap also covers
  // Safari keyboards that resize visualViewport without resizing the layout.
  const occluded = document.documentElement.clientHeight - height > 100;
  const compact = state.activeTab === "chat" && (viewport?.scale || 1) === 1
    && (occluded || (focused && height <= 500));
  document.body.classList.toggle("chat-keyboard-active", compact);
  document.body.style.setProperty("--chat-visible-height", `${height}px`);
  document.body.style.setProperty("--chat-visible-top", `${viewport?.offsetTop || 0}px`);
}

function bindChatViewport() {
  if (chatViewportBound) return;
  chatViewportBound = true;
  window.visualViewport?.addEventListener("resize", syncChatViewport);
  window.visualViewport?.addEventListener("scroll", syncChatViewport);
  window.addEventListener("resize", syncChatViewport);
  document.addEventListener("focusin", syncChatViewport);
  document.addEventListener("focusout", () => requestAnimationFrame(syncChatViewport));
  subscribe((_state, changed) => {
    if ("activeTab" in changed) syncChatViewport();
  });
}

async function refreshAfterChatTurn() {
  try {
    const [profileResult, activityResult] = await Promise.allSettled([
      fetchProfileSummary({ limit: 5 }),
      fetchActivityFeed({ limit: 5 }),
    ]);
    const next = {};
    if (profileResult.status === "fulfilled") {
      next.profile = normalizeProfileSummary(profileResult.value);
    }
    if (activityResult.status === "fulfilled") {
      next.activityFeed = normalizeActivityFeed(activityResult.value);
    }
    if (Object.keys(next).length > 0) patchState(next);
  } catch { /* best-effort */ }
}

export async function loadNotifications({ includeDelights = false } = {}) {
  try {
    const [notifData, probeData, avoidanceProbeData, delightData] = await Promise.all([
      fetchPendingNotifications().catch(() => ({})),
      fetchPendingProbes().catch(() => []),
      fetchPendingAvoidanceProbes().catch(() => []),
      includeDelights ? fetchDelightBatch().catch(() => []) : Promise.resolve(delightMsgs),
    ]);
    // Start with persisted probes from backend
    const probes = [
      ...(Array.isArray(probeData) ? probeData.map((p) => ({ ...p, type: "interest.probe" })) : []),
      ...(Array.isArray(avoidanceProbeData)
        ? avoidanceProbeData.map((p) => ({ ...p, type: "avoidance.probe" }))
        : []),
    ];
    notifications = mergeProbeNotifications(probes, notifications);
    if (includeDelights) {
      delightMsgs = delightData;
    }
    updateBadgeCount();
  } catch { /* ignore */ }
  // Re-render if overlay is visible so first-click shows real data
  if (overlayOpen) renderOverlay();
}

// ── Public API ───────────────────────────────────────────────
export function initChatView(root) {
  $root = root;
  startChatHistorySync();
  bindVisibilityResume();
  bindChatViewport();
  syncChatViewport();
  if (!loaded) {
    loaded = true;
    loadNotifications();
    void refreshSkills().then(render);
    void refreshPersonas().then(render);
    void refreshSessions().then(render);
    void refreshAgentTasks().then(render);
  }
  // Paint immediately so the first entry shows the history loading indicator
  // instead of an empty message list while the fetch is in flight.
  render();
  loadHistory();
}

export async function toggleMessages() {
  overlayOpen = !overlayOpen;
  if (overlayOpen) {
    renderOverlay();          // show panel immediately (loading state)
    await loadNotifications({ includeDelights: true });
    renderOverlay();          // re-render with actual data
  } else {
    renderOverlay();
  }
}

export function updateBadge() {
  updateBadgeCount();
}

export function onStreamEvent(payload) {
  if (pendingConfirmationRefreshTimer !== null) {
    window.clearTimeout(pendingConfirmationRefreshTimer);
  }
  pendingConfirmationRefreshTimer = window.setTimeout(() => {
    pendingConfirmationRefreshTimer = null;
    void refreshPendingConfirmations();
  }, 300);
  const type = payload?.type || payload?.event;
  if (type === "interest.probe" || type === "avoidance.probe") {
    const item = payload.data || payload;
    if (shouldDisplayProbeFromWebSocket(item, type)) {
      notifications = mergeProbeNotifications(notifications, [{ ...item, type }]);
      updateBadgeCount();
    }
  } else if (type === "delight.liked") {
    const data = payload.data || payload;
    const bvid = data?.bvid || data?.domain;
    if (bvid) {
      delightMsgs = delightMsgs.map((d) =>
        d.bvid === bvid
          ? { ...d, state: "liked", response_message: data?.message || "好，这类多来点。" }
          : d
      );
      if (overlayOpen) renderOverlay();
    }
  } else if (type === "delight.disliked") {
    // Negative feedback from another client removes the message locally.
    const bvid = (payload.data || payload)?.bvid || (payload.data || payload)?.domain;
    if (bvid) {
      const before = delightMsgs.length;
      delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
      if (delightMsgs.length !== before) {
        if (overlayOpen) renderOverlay();
      }
    }
  }
}

/**
 * Start a contextual chat from delight "聊一聊" or probe "多聊聊".
 * Legacy: navigates to chat tab. Prefer expandInlineChatOnCard for overlay cards.
 */
export async function startContextualChat({ scope, subjectId, subjectTitle, message }) {
  patchState({ pendingChatContext: { scope, subjectId, subjectTitle } });
  navigateToTab("chat");

  if (!message) return; // render() will pre-fill composer text

  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  turns.push({
    turn_id: turnId, message, response: null, status: "pending",
    scope, subject_id: subjectId, subject_title: subjectTitle,
  });
  sending = true;
  userScrolledUp = false;
  render();

  try {
    await startChatTurn({ turnId, ...chatSession(scope), subjectId, subjectTitle, message });
    pendingTurnId = turnId;
    pollForResponse();
  } catch {
    const t = turns.find((t) => t.turn_id === turnId);
    if (t) { t.status = "error"; t.error = "\u53D1\u9001\u5931\u8D25"; }
    sending = false;
    render();
  }
}

/**
 * Expand inline chat within a message card (probe or delight).
 * Replaces action buttons with a textarea + send button, sends the turn
 * to the backend, shows the reply inline, then removes the card.
 */
function expandInlineChatOnCard(card, { scope, subjectId, subjectTitle, placeholder }) {
  if (!card || card.querySelector(".inline-chat-area")) return;

  // Hide action buttons
  const actions = card.querySelector(".message-card-actions");
  if (actions) actions.style.display = "none";

  const chatArea = document.createElement("div");
  chatArea.className = "inline-chat-area";

  const input = document.createElement("textarea");
  input.className = "inline-chat-input";
  input.rows = 2;
  input.placeholder = placeholder || "聊聊你的想法…";

  const sendBtn = document.createElement("button");
  sendBtn.className = "inline-chat-send";
  sendBtn.textContent = "发送";

  async function doSend() {
    const message = input.value.trim();
    if (!message) return;
    sendBtn.disabled = true;
    input.disabled = true;

    // Show thinking indicator
    const thinking = document.createElement("div");
    thinking.className = "inline-chat-thinking";
    thinking.textContent = "阿B 正在思考…";
    chatArea.appendChild(thinking);

    const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const isProbeScope = scope === "probe" || scope === "avoidance_probe";
    const probeType = scope === "avoidance_probe" ? "avoidance.probe" : "interest.probe";
    if (isProbeScope) {
      rememberHandledProbe(subjectId, probeType);
    }
    try {
      const turn = await startChatTurn({
        turnId,
        ...chatSession(scope),
        subjectId,
        subjectTitle,
        message,
      });

      // Only a completed turn consumes a probe notification. Failed turns
      // keep the composer/card available so the user can retry.
      const showReply = (t) => {
        thinking.remove();
        input.remove();
        sendBtn.remove();
        const replyEl = document.createElement("div");
        replyEl.className = "inline-chat-reply chat-markdown";
        replyEl.innerHTML = renderMarkdown(t.reply || t.response || "收到了，我会结合这个方向继续观察。");
        chatArea.appendChild(replyEl);
        setTimeout(() => {
          const domain = subjectId;
          if (isProbeScope) {
            notifications = removeProbeFromNotifications(notifications, domain, probeType);
          }
          updateBadgeCount();
          renderOverlay();
        }, 3500);
      };

      const settleTurn = (t) => {
        if (t.status === "failed") {
          thinking.remove();
          sendBtn.disabled = false;
          input.disabled = false;
          if (isProbeScope) {
            forgetHandledProbe(subjectId, probeType);
          }
          const errEl = document.createElement("div");
          errEl.className = "inline-chat-error";
          errEl.textContent = t.error || "刚刚没发出去，换个说法再试试。";
          chatArea.appendChild(errEl);
          return;
        }
        if (t.status === "completed") showReply(t);
      };

      if (turn.status === "completed" || turn.status === "failed") {
        settleTurn(turn);
      } else {
        // Poll until settled
        const poll = async () => {
          try {
            const t = await fetchChatTurn(turnId);
            if (t.status === "completed" || t.status === "failed") {
              settleTurn(t);
            } else {
              setTimeout(poll, 1500);
            }
          } catch {
            setTimeout(poll, 2000);
          }
        };
        setTimeout(poll, 1500);
      }
    } catch {
      thinking.remove();
      sendBtn.disabled = false;
      input.disabled = false;
      if (isProbeScope) {
        forgetHandledProbe(subjectId, probeType);
      }
      const errEl = document.createElement("div");
      errEl.className = "inline-chat-error";
      errEl.textContent = "后台正忙，等一下再聊。";
      chatArea.appendChild(errEl);
      setTimeout(() => errEl.remove(), 3000);
    }
  }

  sendBtn.addEventListener("click", doSend);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  });

  chatArea.append(input, sendBtn);
  card.appendChild(chatArea);
  input.focus();
}
