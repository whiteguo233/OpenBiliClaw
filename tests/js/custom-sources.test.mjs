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
  let invalidRead;
  const manager = context.OpenBiliClawCustomSources.createManager({
    root, baseUrl: "https://local.example/api", onChat() {},
    openUrl(url) { opened.push(url); }, onChange(items) { changes.push(items); },
    async request(path, options) {
      calls.push({ path, options });
      if (path === "/custom-sources") {
        if (rejectRead) throw new Error("来源列表连接超时");
        if (invalidRead !== undefined) return invalidRead;
        return { items: rows, executors };
      }
      return handler(path, options, (items) => { rows = items; });
    },
  });
  return { api: context.OpenBiliClawCustomSources, root, calls, opened, changes, manager,
    setRows(value) { rows = value; },
    rejectReads() { rejectRead = true; },
    setReadResponse(value) { invalidRead = value; },
    timers,
    setExecutors(value) { executors = value; },
    poll() { const entry = timers.entries().next().value; assert.ok(entry); timers.delete(entry[0]); entry[1](); },
    button: (label) => descendants(root).find((node) => node.tagName === "button" && node.textContent === label),
    inputs: () => descendants(root).filter((node) => node.tagName === "input"),
  };
}
async function settle() { for (let i = 0; i < 4; i++) await new Promise(setImmediate); }

test("a malformed post-save refresh preserves source cards, edits, shares and executor readiness", async () => {
  for (const invalid of [null, "<html>error</html>", {}, { items: null, executors: [] }, { items: [] }]) {
    const h = setup(async (_path, options, setRows) => {
      const saved = { ...source, ...JSON.parse(options.body) };
      setRows([saved]);
      h.setReadResponse(invalid);
      return { source: saved };
    }, [{ id: "ego", available: true }]);
    await h.manager.load();
    h.inputs()[1].value = "7";
    h.button("保存来源设置").handlers.click();
    await settle();
    assert.equal(h.inputs().length, 3, "invalid refresh must not mean an empty source list");
    assert.equal(h.inputs()[1].value, "7");
    assert.match(h.root.textContent, /Ego Lite 已就绪/);
    assert.match(h.root.textContent, /来源设置已保存。列表刷新失败/);
    assert.equal(h.changes.length, 1, "keep the last confirmed share overview");
    assert.equal(h.button("保存来源设置").disabled, false);
    h.setReadResponse(undefined);
    assert.equal(await h.manager.load(), true);
    assert.equal(h.changes.at(-1)[0].target_share, 7);
  }
});

test("saving one source keeps another source's unsaved edits while showing the saved server values", async () => {
  const second = { ...source, source_id: "second", name: "Second" };
  const h = setup(async (_path, options, setRows) => {
    const saved = { ...source, ...JSON.parse(options.body), name: "Saved name" };
    setRows([saved, second]);
    return { source: saved };
  });
  h.setRows([source, second]);
  await h.manager.load();
  h.inputs()[0].value = "submitted name";
  h.inputs()[3].value = "Unsaved second";
  h.inputs()[4].value = "11";
  h.inputs()[5].value = "90";
  h.button("保存来源设置").handlers.click();
  await settle();
  assert.deepEqual(h.inputs().map((input) => input.value), ["Saved name", "4", "30", "Unsaved second", "11", "90"]);
});

test("refresh, pause, added sources and archive preserve edits on surviving cards", async () => {
  const second = { ...source, source_id: "second", name: "Second" };
  let rows = [source, second];
  const h = setup(async (_path, options, setRows) => {
    if (options.method === "PATCH") rows = [{ ...rows[0], ...JSON.parse(options.body) }, ...rows.slice(1)];
    if (options.method === "DELETE") rows = rows.slice(1);
    setRows(rows); return {};
  });
  h.setRows(rows); await h.manager.load();
  h.inputs()[0].value = "Unsaved first";
  h.inputs()[4].value = "12";
  h.button("停用").handlers.click(); await settle();
  assert.equal(h.inputs()[0].value, "Unsaved first");
  assert.equal(h.inputs()[4].value, "12");
  rows.push({ ...source, source_id: "third", name: "New source" });
  h.setRows(rows); await h.manager.load();
  assert.equal(h.inputs()[4].value, "12");
  assert.equal(h.inputs()[6].value, "New source");
  h.button("归档").handlers.click(); await settle();
  assert.deepEqual(h.inputs().map((input) => input.value), ["Second", "12", "30", "New source", "4", "30"]);
  h.rejectReads(); await h.manager.load();
  assert.equal(h.inputs()[1].value, "12");
});

test("refresh during a pending save keeps the card disabled and prevents duplicate writes", async () => {
  let finish;
  const h = setup(() => new Promise((resolve) => { finish = resolve; }));
  await h.manager.load();
  h.inputs()[0].value = "Pending name";
  h.button("保存来源设置").handlers.click();
  await h.manager.load();
  assert.equal(h.button("保存来源设置").disabled, true);
  h.button("保存来源设置").handlers.click();
  assert.equal(h.calls.filter((call) => call.options.method === "PATCH").length, 1);
  h.setRows([{ ...source, name: "Pending name" }]);
  finish({ source: { ...source, name: "Pending name" } }); await settle();
  assert.equal(h.inputs()[0].value, "Pending name");
  assert.equal(h.button("保存来源设置").disabled, false);
});

test("failed writes remain visible and editable after a concurrent list refresh", async () => {
  let fail;
  const h = setup(() => new Promise((_resolve, reject) => { fail = reject; }));
  await h.manager.load();
  h.inputs()[0].value = "Keep this edit";
  h.button("保存来源设置").handlers.click();
  await h.manager.load();
  fail(new Error("保存失败，请重试")); await settle();
  assert.match(h.root.textContent, /保存失败，请重试/);
  assert.equal(h.inputs()[0].value, "Keep this edit");
  assert.equal(h.button("保存来源设置").disabled, false);
});

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

test("source manager offers website and feed links through chat and renders friendly discovery kinds", async () => {
  const h = setup();
  h.setRows([{ ...source, workflows: [
    { kind: "feed", executor: "http" }, { kind: "sitemap", executor: "http" },
    { kind: "listing", executor: "http" }, { kind: "browser", executor: "playwright" },
    { executor: "http" }, { executor: "playwright" },
  ] }]);
  await h.manager.load();
  assert.ok(h.button("去聊一聊添加来源"));
  assert.match(h.root.textContent, /网址或 RSS、Atom、JSON Feed 订阅链接/);
  for (const label of ["订阅源", "站点地图", "网页列表", "网页（旧版 Playwright）", "网页或订阅"]) assert.ok(h.root.textContent.includes(label));
  const workflows = descendants(h.root).find((node) => node.tagName === "details");
  assert.doesNotMatch(workflows.textContent, /http|playwright/);
  assert.match(workflows.textContent, /旧版 Playwright/);
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
  assert.match(h.root.textContent, /发现 3 \/ 入队待评估 2/);
  assert.doesNotMatch(h.root.textContent, /入池 2/);
  h.button("停用").handlers.click(); await settle();
  assert.ok(h.button("启用"));
  h.button("归档").handlers.click(); await settle();
  assert.match(h.root.textContent, /还没有自定义来源/);
  assert.ok(h.calls.some((call) => call.options.method === "DELETE"));
});


test("a successful write followed by failed reload reports both facts", async () => {
  const h = setup(); await h.manager.load(); h.rejectReads();
  h.button("保存来源设置").handlers.click(); await settle();
  assert.match(h.root.textContent, /来源设置已保存。列表刷新失败/);
  assert.match(h.root.textContent, /来源列表连接超时/);
  assert.equal(h.button("保存来源设置").disabled, false);
});


test("missing Ego is visible even when Playwright is ready, without offering a replacement", async () => {
  const h = setup(undefined, [{ id: "playwright", available: true }, { id: "ego", installed: false, available: false, detail: "Ego Lite is not installed" }]);
  await h.manager.load();
  assert.match(h.root.textContent, /Ego Lite 未安装/);
  assert.match(h.root.textContent, /公开 RSS、Atom、JSON Feed 订阅无需 Ego Lite/);
  const download = descendants(h.root).find(node => node.tagName === "a" && node.href === "https://lite.ego.app/download");
  assert.ok(download);
  assert.equal(download.target, "_blank");
  assert.equal(download.rel, "noopener noreferrer");
  assert.equal(h.button("安装浏览器"), undefined);
  assert.ok(h.button("去聊一聊添加来源"));
  assert.ok(h.calls.every(call => call.options.method === "GET"));
  assert.equal(h.timers.size, 0);
});

test("installed Ego preserves its real connection error and refreshes status without discarding edits", async () => {
  const h = setup(undefined, [{ id: "ego", installed: true, available: false, detail: "Application bridge is disconnected" }]);
  await h.manager.load();
  h.inputs()[1].value = "8";
  assert.match(h.root.textContent, /Ego Lite 尚未就绪/);
  assert.match(h.root.textContent, /Application bridge is disconnected/);
  assert.doesNotMatch(h.root.textContent, /Ego Lite 已就绪/);
  h.setExecutors([{ id: "ego", installed: true, available: true, detail: "Connected to Ego Lite" }]);
  h.button("刷新 Ego Lite 状态").handlers.click(); await settle();
  assert.match(h.root.textContent, /Ego Lite 已就绪/);
  assert.equal(h.inputs()[1].value, "8");
  assert.equal(h.changes.length, 1, "connection refresh must not replace source cards");
  assert.ok(h.calls.every(call => call.options.method === "GET"));
});

test("ready Ego is shown even when Playwright is missing and never starts browser installation", async () => {
  const h = setup(undefined, [{ id: "ego", installed: true, available: true }, { id: "playwright", available: false }]);
  await h.manager.load();
  assert.match(h.root.textContent, /Ego Lite 已就绪/);
  assert.equal(h.button("安装浏览器"), undefined);
  assert.equal(descendants(h.root).some(node => node.tagName === "a" && node.href === "https://lite.ego.app/download"), false);
  assert.equal(h.timers.size, 0);
});

test("installed but unverified Ego invites a chat trial instead of a refresh loop", async () => {
  const h = setup(undefined, [{ id: "ego", installed: true, available: false, connection_state: "unverified", detail: "Connection is verified during a website trial" }]);
  await h.manager.load();
  assert.match(h.root.textContent, /Ego Lite 已安装，待试跑验证/);
  assert.match(h.root.textContent, /到聊一聊提供网站地址，试跑时会验证 Ego Lite 连接/);
  assert.match(h.root.textContent, /Connection is verified during a website trial/);
  assert.doesNotMatch(h.root.textContent, /已打开 Ego Lite 并完成连接|Ego Lite 尚未就绪/);
  assert.equal(h.button("去聊一聊添加来源").disabled, false);
  assert.ok(h.calls.every(call => call.options.method === "GET"));
});

test("missing Ego status is unknown rather than inferred from the client browser or Playwright", async () => {
  const h = setup(undefined, [{ id: "playwright", available: true }]);
  await h.manager.load();
  assert.match(h.root.textContent, /尚未获取 Ego Lite 状态/);
  assert.doesNotMatch(h.root.textContent, /Ego Lite 已就绪|Ego Lite 未安装/);
  assert.equal(descendants(h.root).some(node => node.tagName === "a"), false);
  h.manager.destroy();
  await h.manager.load();
  assert.equal(h.calls.length, 1);
});

test("Ego refresh failure preserves current source edits and reports the real error", async () => {
  const h = setup(undefined, [{ id: "ego", installed: true, available: false }]);
  await h.manager.load();
  h.inputs()[1].value = "9";
  h.rejectReads(); h.button("刷新 Ego Lite 状态").handlers.click(); await settle();
  assert.match(h.root.textContent, /来源列表连接超时/);
  assert.equal(h.inputs()[1].value, "9");
  assert.equal(h.timers.size, 0);
});

test("an unsupported backend host does not offer local installation or suggest Playwright", async () => {
  const h = setup(undefined, [
    { id: "ego", installed: false, available: false, platform_supported: false, detail: "This backend requires a supported Ego host" },
    { id: "playwright", available: true },
  ]);
  await h.manager.load();
  assert.match(h.root.textContent, /此运行环境暂不支持 Ego Lite/);
  assert.match(h.root.textContent, /This backend requires a supported Ego host/);
  assert.equal(descendants(h.root).some(node => node.tagName === "a"), false);
  assert.doesNotMatch(h.root.textContent, /请在运行 OpenBiliClaw 的电脑上安装/);
  assert.equal(h.button("安装浏览器"), undefined);
  assert.ok(h.button("去聊一聊添加来源"), "HTTP subscriptions remain usable");
});

test("new Ego and existing Playwright workflows retain their real executor identity", async () => {
  const h = setup();
  h.setRows([{ ...source, workflows: [
    { executor: "ego", kind: "browser" }, { executor: "playwright", kind: "browser" },
  ] }]);
  await h.manager.load();
  assert.match(h.root.textContent, /网页（Ego Lite）/);
  assert.match(h.root.textContent, /网页（旧版 Playwright）/);
  assert.ok(h.button("重新登录"));
});

test("managed Ego preparation is explicit, deduplicated, and preserves source edits", async () => {
  let finish;
  const h = setup(() => new Promise(resolve => { finish = resolve; }), [{ id: "ego", managed: true, setup_state: "idle", installed: false, available: false, platform_supported: true }]);
  await h.manager.load();
  h.inputs()[1].value = "8";
  assert.equal(h.calls.length, 1, "reading settings must not start preparation");
  const prepare = h.button("准备 Ego Lite");
  assert.ok(prepare);
  prepare.handlers.click(); prepare.handlers.click(); await settle();
  assert.equal(h.calls.filter(call => call.options.method === "POST").length, 1);
  assert.equal(h.calls.at(-1).path, "/custom-sources/executors/ego/prepare");
  finish({ setup_state: "preparing", phase: "download", detail: "Downloading the official app" });
  await settle();
  assert.ok(h.button("正在准备 Ego Lite…").disabled);
  assert.equal(h.timers.size, 1);
  assert.equal(h.inputs()[1].value, "8");
  h.setExecutors([{ id: "ego", managed: true, setup_state: "ready", installed: true, available: true }]);
  h.poll(); await settle();
  assert.equal(h.timers.size, 0);
  assert.match(h.root.textContent, /Ego Lite 已就绪/);
  assert.equal(h.inputs()[1].value, "8");
  assert.equal(h.changes.length, 1);
});

test("Ego preparation polling stops when settings leave the screen or are disposed", async () => {
  const h = setup(undefined, [{ id: "ego", managed: true, setup_state: "preparing", available: false }]);
  await h.manager.load();
  assert.equal(h.timers.size, 1);
  h.root.visible = false;
  const before = h.calls.length;
  h.poll(); await settle();
  assert.equal(h.calls.length, before);
  assert.equal(h.timers.size, 0);
  h.root.visible = true;
  await h.manager.load();
  assert.equal(h.timers.size, 1);
  h.manager.destroy();
  assert.equal(h.timers.size, 0);
});

test("Ego preparation failure is retryable without losing unsaved source values", async () => {
  const h = setup(async () => { throw new Error("Official download unavailable"); }, [{ id: "ego", managed: true, setup_state: "idle", installed: false, available: false }]);
  await h.manager.load();
  h.inputs()[1].value = "9";
  h.button("准备 Ego Lite").handlers.click(); await settle();
  assert.match(h.root.textContent, /Official download unavailable/);
  assert.equal(h.button("准备 Ego Lite").disabled, false);
  assert.equal(h.inputs()[1].value, "9");
  assert.equal(h.timers.size, 0);
  assert.ok(h.button("去聊一聊添加来源"));
});

test("external and unsupported Ego hosts never offer managed preparation", async () => {
  for (const state of [
    { managed: false, installed: true, available: false, setup_state: "idle" },
    { managed: true, installed: false, available: false, platform_supported: false, setup_state: "unsupported" },
  ]) {
    const h = setup(undefined, [{ id: "ego", ...state }]);
    await h.manager.load();
    assert.equal(h.button("准备 Ego Lite"), undefined);
    assert.ok(h.button("去聊一聊添加来源"));
    assert.ok(h.calls.every(call => call.options.method === "GET"));
  }
});
