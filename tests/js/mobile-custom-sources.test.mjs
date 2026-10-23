import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import {
  getRecommendationSourceLabel,
  normalizeDelightCandidate,
  normalizeRecommendation,
  buildContentUrl,
  buildRecommendationClickPayload,
  recommendationStats,
} from "../../src/openbiliclaw/web/js/view-models.js";

const appSource = readFileSync(new URL("../../src/openbiliclaw/web/js/app.js", import.meta.url), "utf8");

function fn(name) {
  const start = appSource.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return appSource.slice(start, appSource.indexOf("\n}\n", start) + 2);
}

function harness({ fetchConfig = async () => ({ saved_sync: {} }), managerAvailable = true } = {}) {
  const managers = [];
  const calls = [];
  const navigation = [];
  const nodes = [];
  const focus = [];
  const createNode = () => {
    const node = {
      attributes: {}, listeners: {}, selectors: new Map(), children: [], removed: false,
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      addEventListener(event, listener) { this.listeners[event] = listener; },
      append(child) { this.children.push(child); },
      remove() { this.removed = true; },
      focus() { focus.push(this); },
      querySelector(selector) {
        if (!this.selectors.has(selector)) this.selectors.set(selector, createNode());
        return this.selectors.get(selector);
      },
    };
    return node;
  };
  const body = createNode();
  const context = vm.createContext({
    URL,
    document: { body, createElement() { const node = createNode(); nodes.push(node); return node; } },
    window: { location: { origin: "https://claw.example", assign: url => navigation.push(url) }, confirm: () => true },
    fetchConfig,
    updateConfig: async data => calls.push({ path: "/config", options: data }),
    requestJson: async (path, options) => { calls.push({ path, options }); return { items: [] }; },
    navigateToTab: tab => navigation.push(tab),
    createDialogFocusController: () => ({ activate() {}, deactivate() { focus.push("released"); } }),
    OpenBiliClawCustomSources: managerAvailable ? {
      createManager(options) {
        const manager = { options, loads: 0, destroys: 0,
          async load() { this.loads += 1; }, destroy() { this.destroys += 1; } };
        managers.push(manager);
        return manager;
      },
    } : undefined,
  });
  vm.runInContext(`let closeMobileSettings = null;\n${fn("openMobileSourceLogin")}\n${fn("handleMobileSourceLoginClick")}\n${fn("openMobileSettings")}`, context);
  return { context, calls, navigation, managers, nodes, body, focus };
}

test("mobile settings mounts source management independently of saved-sync configuration", async () => {
  const h = harness({ fetchConfig: async () => { throw new Error("配置暂不可用"); } });
  await h.context.openMobileSettings({});
  assert.equal(h.managers.length, 1);
  assert.equal(h.managers[0].loads, 1);
  assert.match(h.nodes[1].innerHTML, /自定义来源/);
  assert.match(h.nodes[1].innerHTML, /网址或 RSS、Atom、JSON Feed 订阅链接/);
  const { options } = h.managers[0];
  assert.equal(options.baseUrl, "https://claw.example");
  assert.equal(options.root, h.nodes[1].querySelector("#mobile-custom-sources"));
  await options.request("/custom-sources/site%2Fone", {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_share: 7 }),
  });
  assert.equal(h.calls[0].path, "/custom-sources/site%2Fone");
  assert.equal(h.calls[0].options.timeoutMs, 30_000);
  assert.equal(h.calls[0].options.method, "PATCH");
  assert.deepEqual(JSON.parse(h.calls[0].options.body), { target_share: 7 });
  assert.equal(h.nodes[1].querySelector(".mobile-settings-save").disabled, true);
  assert.equal(h.nodes[1].querySelector(".mobile-settings-retry").hidden, false);
  options.onChat();
  assert.deepEqual(h.navigation, ["chat"]);
  assert.equal(h.managers[0].destroys, 1);
  assert.equal(h.body.children[0].removed, true);
  assert.equal(h.focus.at(-1), "released");
});

test("reopening or closing mobile settings disposes the old source manager", async () => {
  const h = harness();
  await h.context.openMobileSettings({});
  await h.context.openMobileSettings({});
  assert.equal(h.managers[0].destroys, 1);
  assert.equal(h.managers[1].destroys, 0);
  assert.equal(h.body.children[0].removed, true);
  h.nodes[3].querySelector(".mobile-settings-close").listeners.click();
  assert.equal(h.managers[1].destroys, 1);
  assert.equal(h.body.children[1].removed, true);
});

test("missing source assets report a failure without blocking saved-sync settings", async () => {
  const h = harness({ managerAvailable: false });
  await h.context.openMobileSettings({});
  const root = h.nodes[1].querySelector("#mobile-custom-sources");
  assert.equal(root.attributes.role, "alert");
  assert.match(root.textContent, /刷新/);
  assert.equal(h.nodes[1].querySelector(".mobile-settings-save").disabled, false);
});

test("mobile login takeover stays on the backend origin and needs no popup permission", async () => {
  const h = harness();
  await h.context.openMobileSettings({});
  h.managers[0].options.openUrl("/web/source-login.html?draft_id=abc");
  assert.equal(h.navigation[0], "https://claw.example/m/desktop/source-login.html?draft_id=abc");
  for (const value of ["", "javascript:alert(1)", "https://other.example/login", "//other.example/login", "https://name:secret@claw.example/login"]) {
    assert.throws(() => h.managers[0].options.openUrl(value));
  }
  assert.equal(h.navigation.length, 1);
});

test("custom names survive recommendation and delight normalization without changing web identity", () => {
  const dto = {
    id: 9, bvid: "web:item-1", content_id: "item-1", item_key: "web:item-1",
    source_platform: "web", source_id: " source-123 ", source_name: "  我的科技阅读  ",
    content_url: "https://example.com/post/1", view_count: 20,
  };
  for (const normalize of [normalizeRecommendation, normalizeDelightCandidate]) {
    const item = normalize(normalize(dto));
    assert.equal(item.source_id, "source-123");
    assert.equal(item.source_name, "我的科技阅读");
    assert.equal(item.source_platform, "web");
    assert.equal(getRecommendationSourceLabel(item), "我的科技阅读");
    assert.equal(buildContentUrl(item), dto.content_url);
    assert.equal(buildRecommendationClickPayload(item).source_platform, "web");
    assert.equal(recommendationStats(item), "");
  }
  assert.equal(getRecommendationSourceLabel({ source_platform: "bilibili" }), "Bilibili");
  assert.equal(getRecommendationSourceLabel({ source_platform: "web", source_name: " " }), "Web");
});

test("saved card normalization preserves the custom source identity and label", () => {
  const saved = readFileSync(new URL("../../src/openbiliclaw/web/js/views/saved.js", import.meta.url), "utf8");
  const start = saved.indexOf("export function normalizeSavedListItem(");
  const normalize = saved.slice(start, saved.indexOf("\n}\n", start) + 2).replace("export ", "");
  const helperStart = saved.indexOf("function safeText(");
  const helper = saved.slice(helperStart, saved.indexOf("\n}\n", helperStart) + 2);
  const context = vm.createContext({ PRESENTATION: { unsupported: true } });
  vm.runInContext(`${helper}\n${normalize}`, context);
  const item = context.normalizeSavedListItem({
    source_platform: "web", source_id: " fixture-tech ", source_name: " 我的科技阅读 ",
    content_id: "fixture-post", sync_status: "unsupported", error_code: "local_only_source",
  });
  assert.equal(item.source_id, "fixture-tech");
  assert.equal(item.source_name, "我的科技阅读");
  assert.equal(item.source_platform, "web");
  assert.equal(item.error_code, "local_only_source");
  assert.match(saved, /it\.source_name \|\| PLATFORM_NAMES\[it\.source_platform\]/);
});

test("mobile recommendation and delight cards use custom source labels and load management assets", () => {
  const recommend = readFileSync(new URL("../../src/openbiliclaw/web/js/views/recommend.js", import.meta.url), "utf8");
  assert.equal((recommend.match(/esc\(getRecommendationSourceLabel\(/g) || []).length, 3);
  assert.doesNotMatch(recommend, /getSourceLabel\((?:d|item)\.source_platform\)/);
  const html = readFileSync(new URL("../../src/openbiliclaw/web/index.html", import.meta.url), "utf8");
  assert.match(html, /href="\/shared\/custom-sources\.css"/);
  assert.ok(html.indexOf('src="/shared/custom-sources.js"') < html.indexOf('src="js/app.js"'));
});

test("chat login links use the mobile takeover, ordinary and external links stay untouched", () => {
  const h = harness();
  function click(href, overrides = {}) {
    let prevented = false;
    h.context.handleMobileSourceLoginClick({
      target: { closest: () => ({ href }) }, button: 0,
      preventDefault() { prevented = true; }, ...overrides,
    });
    return prevented;
  }
  assert.ok(click("https://claw.example/web/source-login.html?draft_id=chat-1"));
  assert.deepEqual(h.navigation, ["https://claw.example/m/desktop/source-login.html?draft_id=chat-1"]);
  for (const href of ["https://outside.example/web/source-login.html?draft_id=1", "/web/", "/web/source-login.html", "https://user:pass@claw.example/web/source-login.html?draft_id=1"]) {
    assert.equal(click(href), false);
  }
  assert.equal(click("/web/source-login.html?draft_id=1", { ctrlKey: true }), false);
  assert.equal(h.navigation.length, 1);
  assert.match(appSource, /document\.addEventListener\("click", handleMobileSourceLoginClick\)/);
});
