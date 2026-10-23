import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const html = readFileSync(new URL("../../src/openbiliclaw/web/desktop/source-login.html", import.meta.url), "utf8");
const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.handlers = {}; this.dataset = {}; this.value = ""; this.textContent = ""; }
  append(node) { this.children.push(node); if (this.tagName === "select" && !this.value) this.value = node.value; }
  replaceChildren() { this.children = []; if (this.tagName === "select") this.value = ""; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  get options() { return this.children; }
}

async function setup(handler = () => null, search = "?draft_id=fixture/draft", pathname = "/web/source-login.html") {
  const elements = Object.fromEntries(["status", "screen", "fields", "login-origins", "value", "fill", "refresh", "resume", "reopen", "return-to-app"]
    .map((id) => [id, new Element(id === "fields" ? "select" : "div")]));
  const calls = [], intervals = [];
  const snapshot = { blocked_navigation_origins: ["https://login.synthetic.example"], elements: [
    { tag: "input", type: "password", selector: "#password", text: "登录密码" },
    { tag: "input", type: "hidden", selector: "#token" },
  ] };
  const context = vm.createContext({
    document: {
      querySelector: (selector) => elements[selector.slice(1)], querySelectorAll: () => [],
      createElement: (tag) => new Element(tag),
    },
    location: { search, pathname }, URLSearchParams,
    URL: { createObjectURL: () => "blob:fixture", revokeObjectURL() {} },
    setInterval(fn) { intervals.push(fn); },
    async fetch(path, options) {
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ path, options, body });
      const custom = await handler(path, body, elements);
      if (custom) return custom;
      const data = path.endsWith("/takeover") || path.endsWith("/action") ? snapshot : {};
      return { ok: true, json: async () => data, blob: async () => ({}) };
    },
  });
  await vm.runInContext(`(async () => {${script}\n})()`, context);
  return { elements, calls, intervals };
}

async function settle() { for (let i = 0; i < 4; i++) await new Promise(setImmediate); }

test("observed login origin stays opt-in and authorizes only the clicked origin", async () => {
  const h = await setup();
  assert.deepEqual(h.calls.find((call) => call.path.endsWith("/takeover")).body, {});
  assert.equal(h.elements["login-origins"].children.length, 1);
  const button = h.elements["login-origins"].children[0];
  assert.match(button.textContent, /https:\/\/login.synthetic.example/);
  button.handlers.click();
  await settle();
  assert.deepEqual(h.calls.filter((call) => call.path.endsWith("/takeover")).at(-1).body,
    { login_origin: "https://login.synthetic.example" });
  assert.ok(h.calls.every((call) => call.path.startsWith("/api/custom-sources/drafts/fixture%2Fdraft")));
  assert.ok(h.calls.every((call) => call.options.credentials === "same-origin"));
});

test("manual fill clears the local secret before the request settles, including failure", async () => {
  let finish;
  const h = await setup((path) => path.endsWith("/action") ? new Promise((resolve) => { finish = resolve; }) : null);
  assert.equal(h.elements.fields.options.length, 1, "hidden fields are never offered");
  h.elements.value.value = "synthetic-test-value";
  h.elements.fill.handlers.click();
  assert.equal(h.elements.value.value, "");
  const call = h.calls.find((entry) => entry.path.endsWith("/action"));
  assert.deepEqual(call.body, { action: "fill", selector: "#password", value: "synthetic-test-value" });
  finish({ ok: false, status: 422, json: async () => ({ detail: "Fixture rejected field" }) });
  await settle();
  assert.equal(h.elements.value.value, "");
  assert.equal(h.elements.status.textContent, "Fixture rejected field");
  assert.doesNotMatch(h.elements.status.textContent, /synthetic-test-value/);
});

test("failed resume keeps refresh usable so a temporary error does not freeze takeover", async () => {
  const h = await setup((path) => path.endsWith("/resume")
    ? { ok: false, status: 503, json: async () => ({ detail: "Temporary trial failure" }) } : null);
  h.elements.resume.handlers.click();
  await settle();
  assert.equal(h.elements.status.textContent, "Temporary trial failure");
  assert.equal(h.calls.filter((call) => call.path.endsWith("/takeover")).length, 2,
    "resume closes the backend session, so recovery must open it again");
  const before = h.calls.filter((call) => call.path.endsWith("/screenshot")).length;
  h.elements.refresh.handlers.click();
  await settle();
  assert.equal(h.calls.filter((call) => call.path.endsWith("/screenshot")).length, before + 1);
});

test("successful trial stops screenshot polling and points new sources back to confirmation", async () => {
  const h = await setup((path) => path.endsWith("/resume")
    ? { ok: true, json: async () => ({ status: "ready" }) } : null);
  h.elements.resume.handlers.click();
  await settle();
  assert.match(h.elements.status.textContent, /试跑成功.*新来源请返回聊天确认启用/);
  const before = h.calls.length;
  h.intervals[0]();
  await settle();
  assert.equal(h.calls.length, before);
});

test("successful workflow update trial requires chat approval without claiming the source resumed", async () => {
  const h = await setup((path) => path.endsWith("/resume")
    ? { ok: true, json: async () => ({ status: "ready", requires_activation: true }) } : null);
  h.elements.resume.handlers.click();
  await settle();
  assert.equal(h.elements.status.textContent, "试跑成功，请返回聊天确认更新。");
  assert.doesNotMatch(h.elements.status.textContent, /已恢复/);
  assert.ok(h.calls.every((call) => !call.path.endsWith("/activate")));
  const before = h.calls.length;
  h.intervals[0](); await settle();
  assert.equal(h.calls.length, before);
});

test("initial load with missing draft id reports the problem without sending a request", async () => {
  const h = await setup(undefined, "");
  assert.equal(h.elements.status.textContent, "缺少来源草稿 ID");
  h.intervals[0](); await settle();
  assert.equal(h.calls.length, 0);
});

test("a user fill waits for screenshot polling instead of being dropped, and clears its local secret immediately", async () => {
  let finish, hold = false;
  const h = await setup((path) => path.endsWith("/screenshot") && hold
    ? new Promise((resolve) => { finish = resolve; }) : null);
  hold = true;
  h.intervals[0](); await settle();
  h.elements.value.value = "queued-secret";
  h.elements.fill.handlers.click();
  assert.equal(h.elements.value.value, "");
  hold = false;
  finish({ ok: true, blob: async () => ({}) }); await settle();
  const actions = h.calls.filter((call) => call.path.endsWith("/action"));
  assert.equal(actions.length, 1);
  assert.deepEqual(actions[0].body, { action: "fill", selector: "#password", value: "queued-secret" });
});

test("expired screenshots expose a reopen action that starts a new takeover", async () => {
  let expired = false;
  const h = await setup((path) => path.endsWith("/screenshot") && expired
    ? { ok: false, status: 409, json: async () => ({ detail: "登录窗口已过期" }) } : null);
  expired = true;
  h.intervals[0](); await settle();
  assert.equal(h.elements.reopen.hidden, false);
  assert.match(h.elements.status.textContent, /重新打开/);
  const before = h.calls.length;
  h.intervals[0](); await settle();
  assert.equal(h.calls.length, before, "expired sessions stop background polling");
  expired = false;
  h.elements.reopen.handlers.click(); await settle();
  assert.equal(h.calls.filter((call) => call.path.endsWith("/takeover")).length, 2);
  assert.equal(h.elements.reopen.hidden, true);
});

test("mobile takeover returns to mobile chat even with an untrusted return URL", async () => {
  const h = await setup(undefined, "?draft_id=fixture&return_to=https://evil.example", "/m/desktop/source-login.html");
  assert.equal(h.elements["return-to-app"].href, "/m/#/chat");
  assert.equal(h.elements["return-to-app"].textContent, "返回手机对话");
});
test("desktop takeover keeps its desktop return destination", async () => {
  const h = await setup();
  assert.equal(h.elements["return-to-app"].href, "/web/");
});
