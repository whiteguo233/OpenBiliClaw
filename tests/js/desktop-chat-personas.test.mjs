import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const desktop = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");

function fn(name, indent = "    ") {
  const start = desktop.search(new RegExp(`${indent}(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return desktop.slice(start, desktop.indexOf(`\n${indent}}\n`, start) + indent.length + 2);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function setup(request = async () => ({})) {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      textContent: "", innerHTML: "", disabled: false, hidden: false,
      attributes: {}, classList: { toggle() {} },
      setAttribute(name, value) { this.attributes[name] = value; }, focus() {},
    });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    state: { agentChat: {
      mode: "agent", sessionId: "a", skill: "system-steward", skills: [],
      sessions: [
        { session_id: "a", metadata: { persona: "natural" } },
        { session_id: "b", metadata: { persona: "warm" } },
      ],
      personas: [
        { id: "natural", title: "自然朋友", description: "自然交谈", example: "先休息一会儿。" },
        { id: "warm", title: "温柔倾听", description: "先倾听", example: "辛苦了，慢慢说。" },
        { id: "concise", title: "简洁直接", description: "先说重点", example: "先休息。" },
      ],
      personaDraft: "concise", personaPickerOpen: true,
      personaCatalogLoading: false, personaCatalogError: "", personaExamplePrompt: "我今天有点累。",
      personaSaves: new Map(), personaStatusBySession: new Map(),
    } },
    chatSessionsGeneration: 0, chatSessionsSignature: "",
    ENDPOINTS: { chatSessions: "/chat/sessions", chatPersonas: "/chat/personas" },
    requestJsonStrict: request, $: node, asArray: value => Array.isArray(value) ? value : [],
    escapeHtml: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    renderChatSidebar() {}, renderChatSkillPicker() {}, persistAgentChatPrefs() {},
  });
  for (const name of ["currentChatSession", "currentChatPersonaId", "currentChatPersona", "chatPersonaIsSaving", "refreshChatPersonas", "renderChatPersonaState", "renderChatPersonaPreview", "renderChatPersonaPicker", "toggleChatPersonaPicker", "saveChatPersona", "refreshChatSessions"]) {
    vm.runInContext(fn(name), context);
  }
  context.renderChatSessionBar = context.renderChatPersonaState;
  return { context, node };
}

test("desktop saves persona to the owning session and waits for server confirmation", async () => {
  const response = deferred();
  let request;
  const { context, node } = setup((url, options) => { request = { url, options }; return response.promise; });
  const saving = context.saveChatPersona();
  assert.equal(request.url, "/chat/sessions/a");
  assert.equal(request.options.method, "PATCH");
  assert.deepEqual(JSON.parse(request.options.body), { persona: "concise" });
  assert.equal(context.currentChatPersonaId(), "natural", "do not optimistically claim persistence");
  assert.equal(node("#chatForm button[type='submit']").disabled, true);
  assert.match(node("#chatPersonaStatus").textContent, /正在保存/);
  response.resolve({ session_id: "a", metadata: { persona: "concise", preserved: true } });
  await saving;
  assert.equal(context.currentChatPersonaId(), "concise");
  assert.equal(context.currentChatSession().metadata.preserved, true);
  assert.equal(context.state.agentChat.skill, "system-steward", "persona never changes the tool role");
  assert.equal(node("#chatPersonaName").textContent, "简洁直接");
  assert.equal(node("#chatForm button[type='submit']").disabled, false);
  assert.match(node("#chatPersonaStatus").textContent, /已保存/);
});

test("desktop failed save retains current persona, draft and retry capability", async () => {
  const { context, node } = setup(async () => { throw new Error("network unavailable"); });
  await context.saveChatPersona();
  assert.equal(context.currentChatPersonaId(), "natural");
  assert.equal(context.state.agentChat.personaDraft, "concise");
  assert.equal(node("#chatPersonaSave").disabled, false);
  assert.equal(node("#chatForm button[type='submit']").disabled, false);
  assert.match(node("#chatPersonaStatus").textContent, /保存未确认/);
  assert.equal(context.state.agentChat.personaSaves.size, 0);
});

for (const outcome of ["success", "failure"]) {
  test(`desktop late ${outcome} cannot change another session's persona or status`, async () => {
    const response = deferred();
    const { context, node } = setup(() => response.promise);
    const saving = context.saveChatPersona();
    context.state.agentChat.sessionId = "b";
    context.toggleChatPersonaPicker(false);
    context.toggleChatPersonaPicker(true);
    const status = node("#chatPersonaStatus").textContent;
    if (outcome === "success") response.resolve({ session_id: "a", metadata: { persona: "concise" } });
    else response.reject(new Error("request failed"));
    await saving;
    assert.equal(context.currentChatPersonaId(), "warm");
    assert.equal(context.state.agentChat.personaDraft, "warm");
    assert.equal(node("#chatPersonaName").textContent, "温柔倾听");
    assert.equal(node("#chatPersonaStatus").textContent, status);
    assert.equal(node("#chatForm button[type='submit']").disabled, false);
    assert.equal(context.state.agentChat.sessions[0].metadata.persona, outcome === "success" ? "concise" : "natural");
  });
}

test("desktop late session GET cannot revert a confirmed PATCH; a later poll observes another device", async () => {
  const stale = deferred();
  const patch = deferred();
  let reads = 0;
  const { context, node } = setup((_url, options) => {
    if (options.method === "PATCH") return patch.promise;
    return ++reads === 1 ? stale.promise : Promise.resolve({ items: [{ session_id: "a", metadata: { persona: "warm" } }] });
  });
  const oldRead = context.refreshChatSessions();
  const saving = context.saveChatPersona();
  patch.resolve({ session_id: "a", metadata: { persona: "concise" } });
  await saving;
  stale.resolve({ items: [{ session_id: "a", metadata: { persona: "natural" } }] });
  await oldRead;
  assert.equal(context.currentChatPersonaId(), "concise");
  await context.refreshChatSessions();
  assert.equal(context.currentChatPersonaId(), "warm");
  assert.equal(node("#chatPersonaName").textContent, "温柔倾听");
  assert.doesNotMatch(node("#chatPersonaStatus").textContent, /简洁直接/);
});

test("desktop returning to a session during its save restores that operation's selected template", async () => {
  const response = deferred();
  const { context, node } = setup(() => response.promise);
  const saving = context.saveChatPersona();
  context.state.agentChat.sessionId = "b";
  context.toggleChatPersonaPicker(false);
  context.toggleChatPersonaPicker(true);
  context.state.agentChat.sessionId = "a";
  context.toggleChatPersonaPicker(false);
  context.toggleChatPersonaPicker(true);
  assert.equal(context.state.agentChat.personaDraft, "concise");
  assert.match(node("#chatPersonaName").textContent, /保存中/);
  response.resolve({ session_id: "a", metadata: { persona: "concise" } });
  await saving;
  assert.equal(context.state.agentChat.personaDraft, context.currentChatPersonaId());
  assert.equal(node("#chatPersonaSave").disabled, true);
});

test("desktop saves are single-flight per session and invalid responses never claim success", async () => {
  const response = deferred();
  let calls = 0;
  const { context, node } = setup(() => { calls += 1; return response.promise; });
  const saving = context.saveChatPersona();
  await context.saveChatPersona();
  assert.equal(calls, 1);
  response.resolve({ session_id: "b", metadata: { persona: "concise" } });
  await saving;
  assert.equal(context.currentChatPersonaId(), "natural");
  assert.match(node("#chatPersonaStatus").textContent, /保存未确认/);
});

test("desktop catalog failure is isolated from role and chatting; retry recovers", async () => {
  let calls = 0;
  const { context, node } = setup(async () => {
    if (++calls === 1) throw new Error("404");
    return { personas: [{ id: "natural", title: "自然朋友", description: "自然", example: "你好。" }], example_prompt: "你好" };
  });
  context.state.agentChat.personas = [];
  await context.refreshChatPersonas();
  assert.equal(context.state.agentChat.mode, "agent");
  assert.equal(context.state.agentChat.skill, "system-steward");
  assert.equal(node("#chatForm button[type='submit']").disabled, false);
  assert.match(node("#chatPersonaOptions").innerHTML, /data-persona-retry/);
  await context.refreshChatPersonas();
  assert.equal(context.state.agentChat.personaCatalogError, "");
  assert.equal(context.state.agentChat.personas.length, 1);
  assert.equal(context.state.agentChat.personaExamplePrompt, "你好");
});

test("desktop persona catalog and sample text are escaped; metadata absence uses natural", () => {
  const { context, node } = setup();
  context.state.agentChat.sessions[0].metadata = {};
  assert.equal(context.currentChatPersonaId(), "natural");
  context.state.agentChat.personas[2].example = '<img src=x onerror="alert(1)">';
  context.state.agentChat.personas[2].description = "<script>bad()</script>";
  context.renderChatPersonaPicker();
  assert.doesNotMatch(node("#chatPersonaPreview").innerHTML, /<img/);
  assert.match(node("#chatPersonaPreview").innerHTML, /&lt;img/);
  assert.doesNotMatch(node("#chatPersonaOptions").innerHTML, /<script>/);
  assert.match(node("#chatPersonaOptions").innerHTML, /type="radio"/);
  assert.match(node("#chatPersonaPreview").innerHTML, /我今天有点累/);
});

test("desktop slow persona catalog cannot delay current-session history or expose bootstrap history", async () => {
  const catalog = deferred();
  let historyReads = 0;
  const context = vm.createContext({
    chatAgentCore: {}, state: { agentChat: { mode: "unknown" }, chat: [{ turn: { session_id: "other" } }] },
    ENDPOINTS: { chatSkills: "/skills" }, lastDialogueChatSignature: "old",
    requestJsonStrict: async () => ({ skills: [] }),
    loadAgentChatPrefs() {}, applyAgentChatChrome() {}, renderChat() {},
    refreshChatPersonas: () => catalog.promise,
    refreshChatSessions: async () => {}, refreshChatApprovals: async () => {}, refreshChatTasks: async () => {},
    refreshDialogueTurns: async () => { historyReads += 1; },
  });
  vm.runInContext(fn("initDesktopAgentChat"), context);
  const initialization = context.initDesktopAgentChat();
  await new Promise(resolve => setImmediate(resolve));
  const readsBeforeCatalog = historyReads;
  catalog.resolve();
  await initialization;
  assert.equal(readsBeforeCatalog, 1);
  assert.equal(context.state.chat.length, 0);
});

test("desktop late shared bootstrap snapshot cannot replace an agent conversation", () => {
  const applied = [];
  const context = vm.createContext({
    agentChatEnabled: () => true,
    applyDialogueChatSnapshot: value => applied.push(value),
  });
  vm.runInContext(fn("applyChatSnapshot", "      "), context);
  context.applyChatSnapshot({ items: [{ session_id: "other", reply: "foreign history" }] });
  assert.deepEqual(applied, []);
});

test("desktop legacy history request completing after agent activation is ignored", async () => {
  const response = deferred();
  const applied = [];
  const context = vm.createContext({
    enabled: false, agentChatEnabled: () => context.enabled,
    requestJsonStrict: () => response.promise,
    ENDPOINTS: { chatTurns: "/turns" }, SHARED_CHAT_SESSION: "popup",
    applyDialogueChatSnapshot: value => applied.push(value),
  });
  vm.runInContext(fn("refreshDialogueTurns"), context);
  const request = context.refreshDialogueTurns();
  context.enabled = true;
  response.resolve({ items: [{ session_id: "other", reply: "foreign history" }] });
  await request;
  assert.deepEqual(applied, []);
});
