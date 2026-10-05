import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const mobile = readFileSync(new URL("../../src/openbiliclaw/web/js/views/chat.js", import.meta.url), "utf8");
const api = readFileSync(new URL("../../src/openbiliclaw/web/js/api.js", import.meta.url), "utf8");

function fn(source, name) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf("\n}\n", start) + 2);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function session(id, persona = "natural") {
  return { session_id: id, title: id, metadata: { persona, preserved: "metadata" } };
}

function harness(overrides = {}) {
  const notices = [];
  const context = vm.createContext({
    $root: null, state: { online: true }, activeSessionId: "a", chatSessions: [session("a", "warm"), session("b", "concise")],
    chatPersonas: [
      { id: "natural", title: "自然朋友", description: "像朋友一样自然聊天", example: "今天想聊什么？", default: true },
      { id: "warm", title: "温柔倾听", description: "先听你说", example: "我在，慢慢说。" },
      { id: "concise", title: "简洁直接", description: "先说重点", example: "可以，先做这一步。" },
      { id: "analytical", title: "理性分析", description: "拆解问题", example: "我们先看事实。" },
    ],
    personaRevision: 0, personaSaveRequests: new Map(), personaSaveErrors: new Map(),
    chatPersonaExamplePrompt: "共同示例问题来自服务器",
    personaCatalogLoading: false, personaCatalogError: "", historyRefreshGeneration: 0, historyRefreshInFlight: false,
    skillsSheetOpen: true, sessionsDrawerOpen: false, chatPreferenceTab: "persona", tasksOverlayOpen: false,
    contextErrorMessage: error => error.message, renderAgentOverlays() {}, render() {},
    setDialogueStatus: (...args) => notices.push(args),
    ...overrides,
  });
  for (const name of ["currentPersonaId", "currentPersonaTitle", "personaRequestError", "mergeChatSessionSnapshot", "applyChatSessionSnapshot", "refreshPersonas", "invalidatePersonaReads", "saveChatPersona", "refreshSessions", "closeChatPreferences"]) {
    vm.runInContext(fn(mobile, name), context);
  }
  return { context, notices };
}

test("persona API returns the server catalog and PATCH only updates persona", async () => {
  const calls = [];
  const context = vm.createContext({
    QUICK_READ_TIMEOUT_MS: 5000, DEFAULT_READ_TIMEOUT_MS: 12000,
    requestJson: async (path, options) => {
      calls.push({ path, options });
      return { personas: [{ id: "warm", title: "温柔倾听", example: "我在。" }], example_prompt: "共同问题" };
    },
  });
  vm.runInContext(fn(api, "fetchChatPersonas") + "\n" + fn(api, "updateChatSession"), context);
  const catalog = await context.fetchChatPersonas();
  assert.equal(catalog.personas[0].example, "我在。");
  assert.equal(catalog.examplePrompt, "共同问题");
  await context.updateChatSession("chat a", { persona: "warm" });
  assert.equal(calls[0].path, "/chat/personas");
  assert.equal(calls[1].path, "/chat/sessions/chat%20a");
  assert.equal(calls[1].options.method, "PATCH");
  assert.equal(calls[1].options.timeoutMs, 12000);
  assert.deepEqual(JSON.parse(calls[1].options.body), { persona: "warm" });
});

test("invalid or unavailable catalog cannot look like a working empty picker", async () => {
  const context = vm.createContext({ QUICK_READ_TIMEOUT_MS: 5000, requestJson: async () => ({ personas: [{}] }) });
  vm.runInContext(fn(api, "fetchChatPersonas"), context);
  await assert.rejects(context.fetchChatPersonas(), /暂不可用/);
  const { context: chat } = harness({ fetchChatPersonas: async () => { throw new Error("网络中断"); } });
  await chat.refreshPersonas();
  assert.equal(chat.personaCatalogError, "网络中断");
  assert.equal(chat.personaCatalogLoading, false);
  assert.equal(chat.currentPersonaId(), "warm");
});

test("network errors describe uncertain preference persistence, not an unsent chat draft", async () => {
  const { context, notices } = harness({ updateChatSession: async () => { throw new TypeError("Failed to fetch"); } });
  await context.saveChatPersona("concise");
  assert.equal(context.personaSaveErrors.get("a"), "连接中断或服务器未响应");
  assert.match(notices[0][0], /未能确认/);
  assert.doesNotMatch(notices[0][0], /草稿|没有保存成功/);
});

test("saving waits for the server, blocks repeat saves, and preserves the functional role", async () => {
  const response = deferred();
  const calls = [];
  const { context, notices } = harness({
    sessionSkillMap: { a: "system-steward" },
    updateChatSession: (...args) => { calls.push(args); return response.promise; },
  });
  const saving = context.saveChatPersona("concise");
  assert.equal(context.currentPersonaId(), "warm");
  assert.equal(context.personaSaveRequests.get("a"), "concise");
  assert.equal(notices.length, 0);
  await context.saveChatPersona("natural");
  assert.equal(calls.length, 1);
  response.resolve(session("a", "concise"));
  await saving;
  assert.equal(context.currentPersonaId(), "concise");
  assert.equal(context.sessionSkillMap.a, "system-steward");
  assert.equal(context.skillsSheetOpen, false);
  assert.match(notices[0][0], /本会话.*简洁直接.*下一句/);
});

test("a failed save preserves the server choice and allows retry", async () => {
  const { context, notices } = harness({ updateChatSession: async () => { throw new Error("保存超时"); } });
  await context.saveChatPersona("concise");
  assert.equal(context.currentPersonaId(), "warm");
  assert.equal(context.personaSaveRequests.size, 0);
  assert.equal(context.personaSaveErrors.get("a"), "保存超时");
  assert.equal(context.skillsSheetOpen, true);
  assert.ok(notices.every(([, tone]) => tone !== "success"));
  context.updateChatSession = async () => session("a", "concise");
  await context.saveChatPersona("concise");
  assert.equal(context.currentPersonaId(), "concise");
  assert.equal(context.personaSaveErrors.size, 0);
});

test("an older backend ignoring persona is reported as a failed save", async () => {
  const { context, notices } = harness({ updateChatSession: async () => session("a", "warm") });
  await context.saveChatPersona("concise");
  assert.equal(context.currentPersonaId(), "warm");
  assert.match(context.personaSaveErrors.get("a"), /未确认/);
  assert.ok(notices.every(([, tone]) => tone !== "success"));
});

for (const succeeds of [true, false]) {
  test(`a late ${succeeds ? "successful" : "failed"} save never changes the new session UI`, async () => {
    const response = deferred();
    let renders = 0;
    const { context, notices } = harness({
      updateChatSession: () => response.promise,
      renderAgentOverlays: () => { renders += 1; }, render: () => { renders += 1; },
    });
    const saving = context.saveChatPersona("natural");
    context.activeSessionId = "b";
    const before = renders;
    if (succeeds) response.resolve(session("a", "natural"));
    else response.reject(new Error("旧会话保存失败"));
    await saving;
    assert.equal(context.currentPersonaId(), "concise");
    assert.equal(context.skillsSheetOpen, true);
    assert.equal(renders, before);
    assert.equal(notices.length, 0);
    assert.equal(context.personaSaveErrors.has("b"), false);
  });
}

test("a session list started before save cannot roll back the confirmed style", async () => {
  const list = deferred();
  const { context } = harness({
    fetchChatSessions: () => list.promise, updateChatSession: async () => session("a", "concise"),
  });
  const reading = context.refreshSessions();
  await context.saveChatPersona("concise");
  list.resolve([session("a", "warm"), session("b", "natural")]);
  await reading;
  assert.equal(context.currentPersonaId(), "concise");
  assert.equal(context.chatSessions[0].metadata.preserved, "metadata");
});

test("history sync restores a style changed on another device without localStorage", async () => {
  let rendered = 0;
  const { context } = harness({
    historyLoaded: true, lastHistorySignature: "[]", agentRunsByTurnId: new Map(),
    fetchChatSessionDetail: async () => ({ session: session("a", "analytical"), items: [] }),
    fetchPendingConfirmations: async () => ({ count: 0, items: [] }), refreshApprovals: async () => false,
    pendingConfirmations: { count: 0, items: [] }, normalizeChatTurn: value => value,
    trackPendingHistoryTurn() {}, chatHistorySignature: JSON.stringify, patchState() {},
    dialogueContextSelection: null, validateDialogueContext: async () => {}, render: () => { rendered += 1; },
    localStorage: { setItem() { throw new Error("persona must not write localStorage"); } },
  });
  vm.runInContext(fn(mobile, "loadHistory"), context);
  context.personaSaveErrors.set("a", "上次请求超时");
  await context.loadHistory();
  assert.equal(context.currentPersonaId(), "analytical");
  assert.equal(context.currentPersonaTitle(), "理性分析");
  assert.equal(context.personaSaveErrors.has("a"), false);
  assert.equal(rendered, 1);
});

test("picker explains scope, shows examples, and keeps save failure actionable", () => {
  class Element { addEventListener() {} querySelector() { return null; } }
  const children = [];
  const { context } = harness({
    HTMLElement: Element, Element, document: { createElement: () => new Element() },
    ensureAgentOverlayHost: () => ({ querySelector: () => null, appendChild: child => children.push(child) }),
    currentSkillName: () => "", chatSkills: [], esc: value => String(value),
  });
  context.personaSaveErrors.set("a", "保存超时");
  vm.runInContext(fn(mobile, "renderAgentOverlays"), context);
  context.renderAgentOverlays();
  const markup = children[0].innerHTML;
  assert.match(markup, /仅对本会话的下一句起生效/);
  assert.match(markup, /例如：我在，慢慢说。/);
  assert.match(markup, /共同示例问题来自服务器/);
  assert.match(markup, /尚未确认保存：保存超时/);
  assert.match(markup, /data-persona-pick="warm" aria-pressed="true"/);
  assert.match(markup, /功能角色/);
});
