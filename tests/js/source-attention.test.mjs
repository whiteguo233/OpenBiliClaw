import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const desktop = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");
const shared = readFileSync(new URL("../../src/openbiliclaw/web/shared/custom-sources.js", import.meta.url), "utf8");
function functionSource(name, async = false) {
  const start = desktop.indexOf(`    ${async ? "async " : ""}function ${name}(`);
  assert.ok(start >= 0, name);
  return desktop.slice(start, desktop.indexOf("\n    }\n", start) + 6);
}
function setup() {
  let result = { items: [] }, fail = false;
  const opened = [], calls = [], toasts = [], settings = [];
  const notification = { type: "notification", bvid: "x", title: "Keep me" };
  const context = vm.createContext({ URL, encodeURIComponent,
    state: { messages: [notification], messageListSnapshot: [notification], handledProbeKeys: new Set() },
    requestJson: async () => { if (fail) throw Error("offline"); return result; },
    requestJsonStrict: async (path, options) => { calls.push({ path, options }); return result; },
    window: { location: { href: "https://local.test/web/", assign: (url) => opened.push(url) } },
    getApiBase: () => "https://local.test/api", showToast: (s) => toasts.push(s), renderMessages() {},
    openSourceSettings: async (id) => settings.push(id),
  });
  vm.runInContext(shared, context);
  context.window.OpenBiliClawCustomSources = context.OpenBiliClawCustomSources;
  vm.runInContext("let sourceAttentionLoading = false;", context);
  for (const name of ["messageType", "probeKey", "messageKey", "normalizeMessageItem", "getRenderableMessages"]) {
    vm.runInContext(functionSource(name), context);
  }
  for (const name of ["refreshSourceAttention", "openSourceAttention"]) {
    vm.runInContext(functionSource(name, true), context);
  }
  return { context, opened, calls, toasts, settings, setResult: (x) => { result = x; }, setFail: (x) => { fail = x; } };
}
const notice = { id: "source:custom/a", source_id: "custom/a", action: "source_login", name: "Rust", message: "需要验证" };

test("persistent collection failure opens the source settings without creating a login draft", async () => {
  const h = setup(), item = { ...notice, action: "source_settings", code: "source_timeout" };
  h.setResult({ items: [item] });
  await h.context.refreshSourceAttention();
  assert.equal(h.context.getRenderableMessages().length, 2);
  const button = { disabled: false };
  await h.context.openSourceAttention(item, button);
  assert.deepEqual(h.settings, ["custom/a"]);
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.opened, []);
  assert.equal(button.disabled, false);
});

test("attention survives a failed poll, refreshes an open inbox and disappears after recovery", async () => {
  const h = setup();
  h.setResult({ items: [notice, notice, { id: "bad", action: "unknown" }] });
  await h.context.refreshSourceAttention();
  assert.equal(h.context.getRenderableMessages().length, 2);
  assert.equal(h.context.getRenderableMessages(h.context.state.messageListSnapshot).length, 2);
  h.setFail(true);
  await h.context.refreshSourceAttention();
  assert.equal(h.context.getRenderableMessages().length, 2);
  h.setFail(false);
  h.setResult({ items: [] });
  await h.context.refreshSourceAttention();
  assert.equal(h.context.state.messages.length, 1);
  assert.equal(h.context.state.messageListSnapshot.length, 1);
  assert.equal(h.context.state.messages[0].bvid, "x");
});

test("inbox recovery uses the precise source and rejects foreign takeover links", async () => {
  const h = setup(), button = { disabled: false };
  h.setResult({ status: "needs_login", takeover_url: "/web/source-login.html?draft_id=d" });
  await h.context.openSourceAttention(notice, button);
  assert.equal(h.calls[0].path, "/custom-sources/custom%2Fa/login");
  assert.equal(h.calls[0].options.method, "POST");
  assert.deepEqual(h.opened, ["https://local.test/web/source-login.html?draft_id=d"]);
  h.setResult({ status: "needs_login", takeover_url: "https://foreign.test/web/source-login.html?draft_id=d" });
  await h.context.openSourceAttention({ action: "draft_login", draft_id: "d/a" }, button);
  assert.equal(h.calls.at(-1).path, "/custom-sources/drafts/d%2Fa");
  assert.equal(h.opened.length, 1);
  assert.equal(h.toasts.length, 1);
  assert.equal(button.disabled, false);
});
