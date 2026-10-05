import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { fetchChatPersonas, updateChatSession } from "../popup/popup-api.js";

const source = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
function fn(name: string) {
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf("\n}\n", start) + 2);
}
function deferred() {
  let resolve: (value: unknown) => void = () => {};
  let reject: (value: unknown) => void = () => {};
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function session(id: string, persona: string) {
  return { session_id: id, metadata: { persona } };
}
function harness(extra = {}) {
  const statuses: string[] = [];
  let closed = 0;
  const context = vm.createContext({
    state: { online: true }, popupChatSessionId: "a",
    popupChatSessions: [session("a", "natural"), session("b", "warm")],
    popupChatPersonas: [{ id: "natural" }, { id: "concise" }, { id: "warm" }],
    popupPersonaSaving: new Set(), popupPersonaRevision: 0,
    popupPersonaError: "", popupSessionsRequestGeneration: 0,
    popupChatSubtab: "chat", popupSessionSkills: { a: "system-steward" },
    elements: { chatPersonaDialog: { close() { closed += 1; } } },
    renderPopupPersonaPicker() {}, renderChatSessionsPanel() {},
    setChatStatus(message: string) { statuses.push(message); }, ...extra,
  });
  for (const name of ["currentPopupPersona", "rememberPopupChatSession", "selectPopupPersona", "refreshChatSessions", "refreshChatPersonas"]) {
    vm.runInContext(fn(name), context);
  }
  return { context, statuses, closed: () => closed };
}

test("persona catalog and session preference use the server API", async () => {
  const previous = globalThis.fetch;
  const requests: { url: string; options: RequestInit }[] = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options: options || {} });
    return new Response(JSON.stringify(String(url).endsWith("/chat/personas")
      ? { personas: [{ id: "concise", title: "简洁直接" }] }
      : session("a", "concise")), { status: 200 });
  };
  try {
    assert.equal((await fetchChatPersonas()).personas[0].id, "concise");
    await updateChatSession("a", { persona: "concise" });
    const write = requests.find((item) => item.options.method === "PATCH");
    assert.ok(write);
    assert.match(write.url, /\/chat\/sessions\/a$/);
    assert.deepEqual(JSON.parse(String(write.options.body)), { persona: "concise" });
  } finally { globalThis.fetch = previous; }
});

test("selection changes only after server persistence, without changing the functional role", async () => {
  const response = deferred();
  const { context, statuses, closed } = harness({ updateChatSession: () => response.promise });
  const save = context.selectPopupPersona("concise");
  assert.equal(context.currentPopupPersona(), "natural");
  assert.equal(context.popupPersonaSaving.has("a"), true);
  response.resolve(session("a", "concise"));
  await save;
  assert.equal(context.currentPopupPersona(), "concise");
  assert.equal(context.popupSessionSkills.a, "system-steward");
  assert.equal(context.popupPersonaSaving.size, 0);
  assert.equal(closed(), 1);
  assert.equal(statuses.length, 1);
});

test("failed save preserves the previous style and allows retry", async () => {
  const { context, statuses } = harness({ updateChatSession: async () => { throw new Error("offline"); } });
  await context.selectPopupPersona("concise");
  assert.equal(context.currentPopupPersona(), "natural");
  assert.equal(context.popupPersonaSaving.size, 0);
  assert.match(statuses[0], /尚未确认/);
});

test("late save updates its own session without closing or announcing in the new session", async () => {
  const response = deferred();
  const { context, statuses, closed } = harness({ updateChatSession: () => response.promise });
  const save = context.selectPopupPersona("concise");
  context.popupChatSessionId = "b";
  response.resolve(session("a", "concise"));
  await save;
  assert.equal(context.currentPopupPersona(), "warm");
  assert.equal(context.popupChatSessions.find((item) => item.session_id === "a").metadata.persona, "concise");
  assert.equal(closed(), 0);
  assert.equal(statuses.length, 0);
});

test("a sessions snapshot started before a successful save cannot revert the new style", async () => {
  const oldRead = deferred();
  const { context } = harness({
    fetchChatSessions: () => oldRead.promise,
    updateChatSession: async () => session("a", "concise"),
  });
  const read = context.refreshChatSessions();
  await context.selectPopupPersona("concise");
  oldRead.resolve([session("a", "natural")]);
  await read;
  assert.equal(context.currentPopupPersona(), "concise");
});

test("new server snapshots restore another device's choice without local storage", async () => {
  const { context } = harness({ fetchChatSessions: async () => [session("a", "warm")] });
  await context.refreshChatSessions();
  assert.equal(context.currentPopupPersona(), "warm");
});

test("an unavailable catalog leaves the existing chat and preference intact", async () => {
  const { context } = harness({ fetchChatPersonas: async () => { throw new Error("404"); } });
  await context.refreshChatPersonas();
  assert.equal(context.state.online, true);
  assert.equal(context.currentPopupPersona(), "natural");
  assert.match(context.popupPersonaError, /无法加载/);
});

test("an old backend that ignores the new field cannot be reported as a successful save", async () => {
  const { context, statuses, closed } = harness({ updateChatSession: async () => session("a", "natural") });
  await context.selectPopupPersona("concise");
  assert.equal(context.currentPopupPersona(), "natural");
  assert.equal(closed(), 0);
  assert.match(statuses[0], /尚未确认/);
});

test("reconnecting in an already open chat loads its role and persona controls", () => {
  const calls: string[] = [];
  const context = vm.createContext({
    state: { online: false, activeTab: "chat" },
    offlineBackendPoller: { start() {}, stop() {} },
    checkBackendStatus() {}, setStatus() {},
    refreshChatSkills() { calls.push("skills"); },
    refreshChatPersonas() { calls.push("personas"); },
    refreshChatSessions() { calls.push("sessions"); },
    createBackendConnectionCoordinator: (options: unknown) => options,
  });
  const start = source.indexOf("const backendConnectionCoordinator =");
  const end = source.indexOf("\nofflineBackendPoller =", start);
  vm.runInContext(source.slice(start, end), context);
  vm.runInContext('backendConnectionCoordinator.onStatusChange("online")', context);
  assert.deepEqual(calls, ["skills", "personas", "sessions"]);
  vm.runInContext('backendConnectionCoordinator.onStatusChange("degraded")', context);
  assert.equal(calls.length, 3, "reachable status changes should not repeat catalog reads");
});
