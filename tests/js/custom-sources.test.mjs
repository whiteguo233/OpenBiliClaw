import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../../src/openbiliclaw/web/shared/custom-sources.js", import.meta.url), "utf8");
const source = {
  source_id: "source/a", name: "Research <blog>", entry_url: "https://example.com/", intent: "研究文章",
  target_share: 4, interval_minutes: 30, enabled: true,
  health: { status: "needs_login", last_error: "登录已过期" },
  workflows: [{ workflow_id: "w1", executor: "playwright", status: "ready", intent: "最新文章" }],
};

class Element {
  constructor(tag, doc) {
    this.tagName = tag; this.ownerDocument = doc; this.children = []; this.dataset = {};
    this.attributes = {}; this.handlers = {}; this.disabled = false; this.value = "";
    this.classList = { add() {} }; this._text = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((node) => node.textContent).join(" "); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.handlers[key] = fn; }
  focus() { this.focused = true; }
  reportValidity() { return true; }
  getClientRects() { return this.visible === false ? [] : [{}]; }
  querySelectorAll(selector) { return descendants(this).filter((node) => selector.split(", ").includes(node.tagName)); }
}
function descendants(root) { return root.children.flatMap((child) => [child, ...descendants(child)]); }
function setup(handler = async () => ({}), initialExecutors = []) {
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({ URL, confirm: () => true,
    setTimeout(fn, delay) { assert.equal(delay, 3000); timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(script, context);
  const doc = { createElement: (tag) => new Element(tag, doc) };
  const root = new Element("div", doc);
  const calls = [], opened = [], changes = [];
  let rows = [{ ...source }];
  let executors = initialExecutors;
  let rejectRead = false;
  const manager = context.OpenBiliClawCustomSources.createManager({
    root, baseUrl: "https://local.example/api", onChat() {},
    openUrl(url) { opened.push(url); }, onChange(items) { changes.push(items); },
    async request(path, options) {
      calls.push({ path, options });
      if (path === "/custom-sources") {
        if (rejectRead) throw new Error("来源列表连接超时");
        return { items: rows, executors };
      }
      return handler(path, options, (items) => { rows = items; });
    },
  });
  return { api: context.OpenBiliClawCustomSources, root, calls, opened, changes, manager,
    rejectReads() { rejectRead = true; },
    timers,
    setExecutors(value) { executors = value; },
    poll() { const entry = timers.entries().next().value; assert.ok(entry); timers.delete(entry[0]); entry[1](); },
    button: (label) => descendants(root).find((node) => node.tagName === "button" && node.textContent === label),
    inputs: () => descendants(root).filter((node) => node.tagName === "input"),
  };
}
async function settle() { for (let i = 0; i < 4; i++) await new Promise(setImmediate); }

test("one source edit persists independent share and cadence without changing content platform or config", async () => {
  const h = setup(async (_path, options, setRows) => {
    setRows([{ ...source, ...JSON.parse(options.body) }]); return {};
  });
  await h.manager.load();
  const inputs = h.inputs();
  inputs[0].value = "新的站点"; inputs[1].value = "7"; inputs[2].value = "60";
  h.button("保存来源设置").handlers.click();
  h.button("保存来源设置").handlers.click();
  await settle();
  const writes = h.calls.filter((call) => call.options.method === "PATCH");
  assert.equal(writes.length, 1, "double clicks do not create overlapping writes");
  assert.equal(writes[0].path, "/custom-sources/source%2Fa");
  assert.deepEqual(JSON.parse(writes[0].options.body), { name: "新的站点", target_share: 7, interval_minutes: 60 });
  assert.match(h.root.textContent, /来源设置已保存/);
  assert.equal(h.changes.at(-1)[0].target_share, 7);
  assert.equal(inputs[0].attributes.form, "custom-source-independent-fields", "never submits global settings");
});

test("failed save retains editable values and real error, without success or optimistic toggle", async () => {
  const h = setup(async () => { throw new Error("执行器暂时离线"); });
  await h.manager.load();
  h.inputs()[1].value = "9";
  h.button("保存来源设置").handlers.click();
  await settle();
  assert.equal(h.inputs()[1].value, "9");
  assert.match(h.root.textContent, /执行器暂时离线/);
  assert.doesNotMatch(h.root.textContent, /来源设置已保存/);
  assert.equal(h.button("保存来源设置").disabled, false);
  assert.equal(h.changes.length, 1);
});

test("login only opens a backend-owned takeover page and leaves an explicit fallback link", async () => {
  const h = setup(async () => ({ takeover_url: "/web/source-login.html?draft_id=draft-1" }));
  await h.manager.load();
  h.button("重新登录").handlers.click();
  await settle();
  assert.equal(h.calls.at(-1).path, "/custom-sources/source%2Fa/login");
  assert.deepEqual(h.opened, ["https://local.example/web/source-login.html?draft_id=draft-1"]);
  assert.ok(descendants(h.root).some((node) => node.tagName === "a" && node.href === h.opened[0]));
  assert.match(h.root.textContent, /返回后点击刷新来源/);
  for (const invalid of ["https://evil.example/web/source-login.html?draft_id=a", "javascript:alert(1)", "/web/source-login.html", "/admin?draft_id=a", "https://user:password@local.example/web/source-login.html?draft_id=a"]) {
    assert.throws(() => h.api.takeoverUrl(invalid, "https://local.example/api"));
  }
});

test("list renders untrusted names as text and source weights stay separate", async () => {
  const h = setup(); await h.manager.load();
  assert.match(h.root.textContent, /Research <blog>/);
  assert.equal(descendants(h.root).some((node) => node.tagName === "blog"), false);
  const entries = h.api.shareEntries([source, { ...source, source_id: "second", name: "Second", target_share: 2 }]);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].key, "source/a");
  assert.equal(entries[1].weight, 2);
  assert.equal(h.api.sourceName({ source_name: "Research", source_platform: "web" }), "Research");
});

test("public HTTP workflows do not offer browser re-login", async () => {
  const h = setup(async (_path, _options, setRows) => {
    setRows([{ ...source, workflows: [{ executor: "http", intent: "公开订阅", status: "ready" }] }]);
    return {};
  });
  await h.manager.load();
  h.button("停用").handlers.click(); await settle();
  assert.equal(h.button("重新登录"), undefined);
  assert.ok(h.button("最近运行"));
});

test("invalid share/cadence never reaches persistence and stale read is discarded", async () => {
  const h = setup(); await h.manager.load();
  for (const values of [{ target_share: 0, interval_minutes: 30 }, { target_share: 4, interval_minutes: 4 }, { target_share: 4.5, interval_minutes: 60 }]) {
    assert.throws(() => h.api.sourcePatch({ name: "A", ...values }));
  }
  h.inputs()[1].value = "21";
  h.button("保存来源设置").handlers.click(); await settle();
  assert.equal(h.calls.length, 1);
  h.manager.destroy(); await h.manager.load();
  assert.equal(h.changes.length, 1);
});

test("archive and pause are different source operations; runs remain read-only", async () => {
  const h = setup(async (path, options, setRows) => {
    if (options.method === "DELETE") setRows([]);
    if (options.method === "PATCH") setRows([{ ...source, enabled: false }]);
    if (path.endsWith("/runs")) return { items: [{ started_at: "2026-10-02", status: "ok", discovered_count: 3, enqueued_count: 2 }] };
    return {};
  });
  await h.manager.load();
  h.button("最近运行").handlers.click(); await settle();
  assert.match(h.root.textContent, /发现 3 \/ 入池 2/);
  h.button("停用").handlers.click(); await settle();
  assert.ok(h.button("启用"));
  h.button("归档").handlers.click(); await settle();
  assert.match(h.root.textContent, /还没有自定义网站/);
  assert.ok(h.calls.some((call) => call.options.method === "DELETE"));
});


test("a successful write followed by failed reload reports both facts", async () => {
  const h = setup(); await h.manager.load(); h.rejectReads();
  h.button("保存来源设置").handlers.click(); await settle();
  assert.match(h.root.textContent, /来源设置已保存。列表刷新失败/);
  assert.match(h.root.textContent, /来源列表连接超时/);
  assert.equal(h.button("保存来源设置").disabled, false);
});


test("browser setup submits once, polls only installation, and preserves source edits", async () => {
  const h = setup(async () => ({ status: "installing" }), [{ id: "playwright", available: false, detail: "需要下载浏览器" }]);
  await h.manager.load();
  h.inputs()[1].value = "8";
  const install = h.button("安装浏览器");
  install.handlers.click(); install.handlers.click(); await settle();
  assert.equal(h.calls.filter((call) => call.options.method === "POST").length, 1);
  assert.equal(h.calls.at(-1).path, "/custom-sources/executors/playwright/install");
  assert.ok(h.button("正在安装…").disabled);
  assert.equal(h.timers.size, 1);
  h.setExecutors([{ id: "playwright", available: false, status: "installing" }]);
  h.poll(); await settle();
  assert.equal(h.inputs()[1].value, "8", "background install polling must not discard an unsaved source edit");
  assert.equal(h.timers.size, 1);
  h.setExecutors([{ id: "playwright", available: true, status: "ready" }]);
  h.poll(); await settle();
  assert.equal(h.timers.size, 0);
  assert.match(h.root.textContent, /浏览器已安装完成/);
  assert.equal(h.button("安装浏览器"), undefined);
});

test("installation polling stops offscreen and on manager disposal", async () => {
  const executor = [{ id: "playwright", available: false, status: "installing" }];
  const h = setup(undefined, executor); await h.manager.load();
  assert.equal(h.timers.size, 1);
  h.root.visible = false;
  const before = h.calls.length; h.poll(); await settle();
  assert.equal(h.calls.length, before);
  assert.equal(h.timers.size, 0);
  h.root.visible = true; await h.manager.load();
  assert.equal(h.timers.size, 1);
  h.manager.destroy(); assert.equal(h.timers.size, 0);
});

test("browser install failure remains retryable and does not poll forever", async () => {
  const h = setup(async () => { throw new Error("下载失败，请检查网络"); }, [{ id: "playwright", available: false }]);
  await h.manager.load(); h.button("安装浏览器").handlers.click(); await settle();
  assert.match(h.root.textContent, /下载失败/);
  assert.equal(h.button("安装浏览器").disabled, false);
  assert.equal(h.timers.size, 0);
});

test("manual refresh during failed install re-enables the current install button", async () => {
  let rejectInstall;
  const h = setup(() => new Promise((_resolve, reject) => { rejectInstall = reject; }), [{ id: "playwright", available: false }]);
  await h.manager.load(); h.button("安装浏览器").handlers.click();
  await h.manager.load();
  assert.equal(h.button("安装浏览器").disabled, true);
  rejectInstall(new Error("安装服务连接失败")); await settle();
  assert.match(h.root.textContent, /安装服务连接失败/);
  assert.equal(h.button("安装浏览器").disabled, false);
});
