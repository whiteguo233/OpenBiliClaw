import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function source(path) { return readFileSync(new URL(path, import.meta.url), "utf8"); }
const desktop = source("../../src/openbiliclaw/web/desktop/assets/js/app.js");
const mobile = source("../../src/openbiliclaw/web/js/views/chat.js");
const popup = source("../../extension/popup/popup.js");

function fn(source, name, indent = "") {
  const start = source.search(new RegExp(`${indent}(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf(`\n${indent}}\n`, start) + indent.length + 2);
}
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

for (const surface of ["desktop", "mobile", "popup"]) {
  test(`${surface} unsent drafts belong to one session and clearing is retained`, async () => {
    const input = { value: "draft A", focus() {} };
    const store = new Map();
    const context = vm.createContext({
      state: { agentChat: { sessionId: "a" } },
      activeSessionId: "a", popupChatSessionId: "a",
      chatSessionDrafts: store, sessionDrafts: store, popupChatDrafts: store,
      retainedChatDraft: "", retainedDraft: "",
      $: () => input, $root: { querySelector: () => input },
      elements: { chatInput: input, chatSendButton: {}, chatMessages: { replaceChildren() {} } },
      localStorage: { setItem() {} }, window: { innerWidth: 1440 },
      AGENT_CHAT_SKILL_KEY: "skills", POPUP_CHAT_SESSION_STORAGE_KEY: "session", CHAT_SESSION_STORAGE_KEY: "session",
      agentChatStorage: () => null, toggleChatPersonaPicker() {}, persistAgentChatPrefs() {},
      lastDialogueChatSignature: null, chatSessionsSignature: "", applyAgentChatChrome() {}, renderChat() {},
      refreshDialogueTurns: async () => {}, renderPopupPersonaPicker() {},
      chatHistoryHydrationGeneration: 0, chatHistoryHydrationInFlight: false,
      lastChatHistorySignature: null, dialogueTurnsById: new Map(), setChatSubtab() {},
      hydrateChatHistory: async () => {}, renderChatSkillSelect() {},
      historyRefreshGeneration: 0, historyRefreshInFlight: false, lastHistorySignature: null,
      streamingTurnIds: new Set(), agentRunsByTurnId: new Map(), agentDeltaBuffers: new Map(), renderAgentOverlays() {}, render() {},
      loadHistory: async () => {},
    });
    const name = surface === "desktop" ? "selectChatSession" : surface === "mobile" ? "switchSession" : "switchPopupChatSession";
    vm.runInContext(fn({ desktop, mobile, popup }[surface], name, surface === "desktop" ? "    " : ""), context);
    await context[name]("b");
    assert.equal(input.value, "", "new conversation must not inherit another draft");
    input.value = "draft B";
    await context[name]("a");
    assert.equal(input.value, "draft A", "returning restores the owning draft");
    input.value = ""; // User cleared it, or submit consumed it.
    await context[name]("b");
    assert.equal(input.value, "draft B");
    await context[name]("a");
    assert.equal(input.value, "", "cleared/sent text must not resurrect");
  });
}

test("desktop history from a departed session cannot overwrite the selected session", async () => {
  const response = deferred();
  const applied = [];
  const context = vm.createContext({
    state: { agentChat: { sessionId: "a" } },
    agentChatEnabled: () => true, requestJsonStrict: () => response.promise,
    ENDPOINTS: { chatSessions: "/sessions" },
    dialogueChatRefreshGeneration: 0,
    applyDialogueChatSnapshot: data => applied.push(data),
  });
  vm.runInContext(fn(desktop, "refreshDialogueTurns", "    "), context);
  const request = context.refreshDialogueTurns();
  context.state.agentChat.sessionId = "b";
  response.resolve({ items: [{ turn_id: "from-a" }] });
  await request;
  assert.deepEqual(applied, []);
});

test("desktop live reply is hidden after switching sessions", () => {
  const context = vm.createContext({
    state: { agentChat: { sessionId: "b", live: {
      sessionId: "a", process: { steps: [] }, replyText: "from-a",
    } } },
    chatAgentCore: {}, renderMarkdown: text => text,
  });
  vm.runInContext(fn(desktop, "liveAgentChatMarkup", "    "), context);
  assert.equal(context.liveAgentChatMarkup(), "");
});

test("desktop done does not overwrite a role chosen during the stream", () => {
  const context = vm.createContext({
    state: { agentChat: { sessionId: "a", skill: "system-steward" } },
    chatAgentCore: {}, renderChat() {}, persistAgentChatPrefs() {},
  });
  vm.runInContext(fn(desktop, "handleAgentStreamEvent", "    "), context);
  context.handleAgentStreamEvent("done", { reply: "好了", skill: "taste-companion" }, {
    sessionId: "a", skill: "taste-companion", process: {},
  });
  assert.equal(context.state.agentChat.skill, "system-steward");
});

test("mobile history from a departed session is ignored", async () => {
  const response = deferred();
  const context = vm.createContext({
    state: { online: true }, historyRefreshInFlight: false, historyRefreshGeneration: 0,
    activeSessionId: "a", historyLoaded: true, turns: [{ turn_id: "from-b" }],
    lastHistorySignature: null, agentRunsByTurnId: new Map(),
    HTMLElement: class {}, document: { getElementById: () => null },
    fetchChatSessionDetail: () => response.promise,
    fetchPendingConfirmations: async () => ({ count: 0, items: [] }),
    refreshApprovals: async () => false,
    normalizeChatTurn: value => value, trackPendingHistoryTurn() {},
    chatHistorySignature: JSON.stringify,
    pendingConfirmations: { count: 0, items: [] },
    patchState() {}, dialogueContextSelection: null, validateDialogueContext: async () => {}, render() {},
  });
  vm.runInContext(fn(mobile, "loadHistory"), context);
  const request = context.loadHistory();
  context.activeSessionId = "b";
  response.resolve({ items: [{ turn_id: "from-a" }] });
  await request;
  assert.equal(context.turns[0].turn_id, "from-b");
});

test("popup history from a departed session is ignored", async () => {
  const response = deferred();
  const rendered = [];
  class Element { replaceChildren() {} querySelectorAll() { return []; } }
  const context = vm.createContext({
    state: { online: true }, HTMLElement: Element,
    elements: { chatMessages: new Element() }, popupChatSessionId: "a",
    chatHistoryHydrationInFlight: false, chatHistoryHydrationGeneration: 0,
    popupPersonaRevision: 0,
    isChatMessagesNearBottom: () => true,
    fetchChatSessionDetail: () => response.promise, selectDialogueTurns: value => value,
    chatHistorySignature: JSON.stringify, lastChatHistorySignature: null,
    openChatEvidenceTurnIds: () => new Set(), suppressChatAutoScroll: false,
    dialogueTurnsById: new Map(), popupAgentRuns: new Map(),
    renderChatTurn: turn => rendered.push(turn), isDialogueReplyTurn: () => false,
    validateDialogueContext: async () => {}, renderDialogueContextBar() {}, scrollChatMessagesToBottom() {},
  });
  vm.runInContext(fn(popup, "hydrateChatHistory"), context);
  const request = context.hydrateChatHistory();
  context.popupChatSessionId = "b";
  response.resolve({ items: [{ turn_id: "from-a" }] });
  await request;
  assert.deepEqual(rendered, []);
});

test("popup late turn updates cannot insert a departed session into the current chat", () => {
  let touched = false;
  class Element {}
  const context = vm.createContext({
    popupChatSessionId: "b", HTMLElement: Element,
    elements: { chatMessages: new Element() },
    dialogueTurnsById: { set() { touched = true; } },
    isCardTurn: () => true, renderStructuredDialogueTurn() {},
  });
  vm.runInContext(fn(popup, "renderChatTurn"), context);
  context.renderChatTurn({ turn_id: "from-a", session_id: "a", scope: "chat" });
  assert.equal(touched, false);
});

test("mobile completed old stream does not unlock a newer active reply", () => {
  const context = vm.createContext({
    activeSessionId: "b", turns: [{ turn_id: "from-b", status: "pending" }], sending: true,
    streamingTurnIds: new Set(["from-a", "from-b"]),
    agentRunsByTurnId: new Map([
      ["from-a", { sessionId: "a" }], ["from-b", { sessionId: "b" }],
    ]),
  });
  vm.runInContext(fn(mobile, "finalizeAgentTurn"), context);
  context.finalizeAgentTurn("from-a", { reply: "好了" });
  assert.equal(context.sending, true);
});

test("popup resumes a turn with its saved session and skill after the user switches", async () => {
  let sent;
  const context = vm.createContext({
    popupChatSessionId: "b", popupSessionSkills: { b: "system-steward" }, CHAT_SESSION: "popup",
    popupAgentRuns: new Map(), popupStreamingTurnIds: new Set(), activeChatPolls: new Map(),
    createAgentRun: () => ({}), refreshChatSessions() {},
    streamAgentChatTurn: async body => { sent = body; return { reply: "好了" }; },
  });
  vm.runInContext(fn(popup, "popupDriveAgentStream"), context);
  await context.popupDriveAgentStream({
    turn_id: "from-a", session_id: "a", message: "继续", payload: { agent_skill: "taste-explorer" },
  });
  assert.equal(sent.sessionId, "a");
  assert.equal(sent.skill, "taste-explorer");
});

test("popup retries a disconnected agent turn without executing a legacy reply", async () => {
  let legacyCalls = 0;
  const context = vm.createContext({
    activeChatPolls: new Map(), popupStreamingTurnIds: new Set(), agentLoopAvailable: true,
    fetchChatTurn: async () => ({ turn_id: "t1", status: "pending", scope: "chat" }),
    popupDriveAgentStream: async () => { throw new Error("连接中断"); },
    streamChatTurn: async () => { legacyCalls += 1; },
    CHAT_POLL_DEADLINE_MS: 180000, CHAT_POLL_INTERVAL_MS: 2000,
    window: { setTimeout: () => 1 },
  });
  vm.runInContext(fn(popup, "pollChatTurnUntilSettled"), context);
  context.pollChatTurnUntilSettled("t1");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(legacyCalls, 0);
  assert.equal(context.activeChatPolls.get("t1"), 1, "the durable turn remains polled");
});

test("desktop late turn creation and stream failure preserve the current session live reply", async () => {
  const creation = deferred();
  const stream = deferred();
  const newerLive = { sessionId: "b", turnId: "from-b" };
  const context = vm.createContext({
    state: { chat: [], agentChat: { sessionId: "a", skill: "", live: null } },
    dialogueContextSelection: null, retainedChatDraft: "draft-b",
    SHARED_CHAT_SESSION: "popup", ENDPOINTS: { chatTurns: "/turns" },
    createClientTurnId: () => "from-a", requestJsonStrict: () => creation.promise,
    chatAgentCore: { createAgentProcess: () => ({}) },
    streamAgentChatTurn: () => stream.promise,
    renderChat() {}, $: () => null, showToast() {},
    refreshDialogueTurns: async () => {}, refreshDesktopPendingConfirmations: async () => {},
    refreshChatSessions: async () => {}, refreshChatApprovals: async () => {},
    refreshUntilDialogueCardsSettle: async () => {}, lastDialogueChatSignature: null,
  });
  vm.runInContext(fn(desktop, "sendAgentChat", "    "), context);
  const request = context.sendAgentChat("from-a");
  context.state.agentChat.sessionId = "b";
  context.state.agentChat.live = newerLive;
  context.state.chat = [];
  creation.resolve({ turn_id: "from-a" });
  await new Promise(resolve => setImmediate(resolve));
  const preservedDuringStream = context.state.agentChat.live === newerLive;
  stream.resolve(Promise.reject(new Error("connection dropped")));
  await request;
  assert.equal(preservedDuringStream, true);
  assert.equal(context.state.agentChat.live, newerLive);
  assert.deepEqual(context.state.chat, []);
  assert.equal(context.retainedChatDraft, "draft-b");
});

test("mobile late turn creation does not lock a different session or change its saved role", async () => {
  const stream = deferred();
  let sent;
  const context = vm.createContext({
    activeSessionId: "b", turns: [], sending: false,
    streamingTurnIds: new Set(), agentRunsByTurnId: new Map(), agentDeltaBuffers: new Map(),
    sessionSkillMap: { a: "system-steward" },
    createAgentRun: () => ({}),
    streamAgentChatTurn: body => { sent = body; return stream.promise; },
    finalizeAgentTurnSuccess: async () => {},
  });
  vm.runInContext(fn(mobile, "driveAgentStream"), context);
  const request = context.driveAgentStream("from-a", "你好", { session_id: "a", payload: {} });
  const sending = context.sending;
  stream.resolve({ reply: "好了" });
  await request;
  assert.equal(sending, false);
  assert.equal(sent.skill, "", "a missing saved skill means default, not the current role selection");
});

test("mobile late poll response cannot replace the newly selected pending turn", async () => {
  const response = deferred();
  let poll;
  const context = vm.createContext({
    activeSessionId: "a", pendingTurnId: "from-a", pollTimer: null, sending: true,
    turns: [], setTimeout: callback => { poll = callback; return 1; }, clearTimeout() {},
    fetchChatTurn: () => response.promise, normalizeChatTurn: value => value,
    userScrolledUp: true, setDialogueStatus() {}, render() {},
    refreshAfterChatTurn: async () => {}, refreshPendingConfirmations: async () => {},
  });
  vm.runInContext(fn(mobile, "pollForResponse"), context);
  context.pollForResponse();
  const request = poll();
  context.activeSessionId = "b";
  context.pendingTurnId = "from-b";
  context.turns = [{ turn_id: "from-b", status: "pending" }];
  response.resolve({ turn_id: "from-a", session_id: "a", status: "completed", reply: "好了" });
  await request;
  assert.equal(context.turns[0].turn_id, "from-b");
  assert.equal(context.pendingTurnId, "from-b");
  assert.equal(context.sending, true);
});
