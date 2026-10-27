import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../src/openbiliclaw/web/shared/i18n.js", import.meta.url), "utf8");
const identity = { messages: { "设置": "设置", "停用": "停用" }, patterns: { "已修改 {0} 项": "已修改 {0} 项" } };
const english = { messages: { "设置": "Settings", "停用": "Disabled" }, patterns: { "已修改 {0} 项": "Modified {0} items" } };
const traditional = { messages: { "设置": "設定", "停用": "停用" }, patterns: {} };

type Catalog = { messages: Record<string, string>; patterns?: Record<string, string> };

class DomNode {
  nodeType: number;
  tagName: string;
  nodeValue: string;
  parentElement: DomNode | null = null;
  children: DomNode[] = [];
  attrs = new Map<string, string>();
  isConnected = true;
  lang = "zh-Hans";
  dir = "ltr";
  constructor(tagName = "DIV", text?: string) {
    this.nodeType = text === undefined ? 1 : 3;
    this.tagName = tagName;
    this.nodeValue = text ?? "";
  }
  append(...nodes: DomNode[]) {
    for (const node of nodes) { node.parentElement = this; this.children.push(node); }
  }
  getAttribute(key: string) { return this.attrs.get(key) ?? null; }
  setAttribute(key: string, value: string) { this.attrs.set(key, value); }
  hasAttribute(key: string) { return this.attrs.has(key); }
  closest() {
    for (let node: DomNode | null = this; node; node = node.parentElement) {
      if (node.hasAttribute("data-i18n-skip") || node.getAttribute("contenteditable") === "true") return node;
    }
    return null;
  }
  descendants(): DomNode[] { return this.children.flatMap((node) => [node, ...node.descendants()]); }
  querySelectorAll() { return this.descendants().filter((node) => node.nodeType === 1 && node.attrs.size); }
}

function fixture(fetchCatalog?: (locale: string) => Promise<Catalog>) {
  const root = new DomNode("HTML");
  const label = new DomNode("SPAN");
  const text = new DomNode("", "设置");
  label.setAttribute("title", "设置");
  label.append(text);
  root.append(label);
  const metrics = { walks: 0, requests: [] as string[] };
  const frames: Array<() => void> = [];
  let callback: ((records: unknown[]) => void) | undefined;
  let observed = false;
  let observedOptions: { attributes?: boolean; attributeFilter?: string[] } | undefined;
  const context = vm.createContext({
    document: {
      documentElement: root,
      createTreeWalker(node: DomNode, _mask: unknown, filter: { acceptNode: (node: DomNode) => number }) {
        metrics.walks++;
        const nodes = node.descendants().filter((item) => item.nodeType === 3 && filter.acceptNode(item) === 1);
        return { nextNode: () => nodes.shift() ?? null };
      },
    },
    Node: { TEXT_NODE: 3, ELEMENT_NODE: 1, DOCUMENT_NODE: 9 },
    NodeFilter: { SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1 },
    MutationObserver: class {
      constructor(fn: (records: unknown[]) => void) { callback = fn; }
      observe(_root: unknown, options: { attributes?: boolean; attributeFilter?: string[] }) {
        observed = true;
        observedOptions = options;
      }
      disconnect() { observed = false; }
    },
    requestAnimationFrame(fn: () => void) { frames.push(fn); return frames.length; },
    fetch: async (url: string) => {
      const locale = url.split("/").at(-1)!.replace(".json", "");
      metrics.requests.push(locale);
      const payload = await (fetchCatalog ? fetchCatalog(locale) : Promise.resolve(
        locale === "en" ? english : locale === "zh-Hant" ? traditional : identity,
      ));
      return { ok: true, json: async () => payload };
    },
    console: { warn() {} },
  });
  vm.runInContext(source, context);
  const runtime = context.OpenBiliClawI18n;
  function mutate(records: unknown[]) {
    if (observed) callback?.(records.filter((record) => {
      const mutation = record as { type: string; attributeName?: string };
      return mutation.type !== "attributes" || (observedOptions?.attributes &&
        observedOptions.attributeFilter?.includes(mutation.attributeName ?? ""));
    }));
    while (frames.length) frames.shift()!();
  }
  function added(node: DomNode) {
    node.isConnected = true;
    root.append(node);
    mutate([{ type: "childList", addedNodes: [node] }]);
  }
  return { root, label, text, metrics, runtime, mutate, added };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test("built-in source locale starts without a catalog download or DOM traversal", async () => {
  const f = fixture();
  await f.runtime.init({ locale: "zh" });
  f.added(new DomNode("", "已修改 2 项"));
  f.runtime.apply(f.root);
  assert.deepEqual(f.metrics.requests, []);
  assert.equal(f.metrics.walks, 0);
  assert.equal(f.text.nodeValue, "设置");
  assert.equal(f.label.getAttribute("title"), "设置");
  assert.equal(f.runtime.translate("已修改 {0} 项", { 0: 3 }), "已修改 3 项");
  assert.equal(f.runtime.formatCount(12000), "1.2万");
});

test("locale round trip restores text and attributes, including dynamically updated nodes", async () => {
  const f = fixture();
  await f.runtime.init({ locale: "zh-Hans" });
  await f.runtime.setLocale("en");
  assert.equal(f.text.nodeValue, "Settings");
  assert.equal(f.label.getAttribute("title"), "Settings");
  const dynamic = new DomNode("", "已修改 2 项");
  f.added(dynamic);
  assert.equal(dynamic.nodeValue, "Modified 2 items");
  f.text.nodeValue = "停用";
  f.mutate([{ type: "characterData", target: f.text }]);
  assert.equal(f.text.nodeValue, "Disabled");
  await f.runtime.setLocale("zh-Hans");
  assert.equal(f.text.nodeValue, "停用");
  assert.equal(f.label.getAttribute("title"), "设置");
  assert.equal(dynamic.nodeValue, "已修改 2 项");
  await f.runtime.setLocale("zh-Hant");
  assert.equal(f.label.getAttribute("title"), "設定");
  await f.runtime.setLocale("zh-Hans");
  assert.equal(f.label.getAttribute("title"), "设置");
});

test("dynamic attributes translate after identity startup and restore their latest source", async () => {
  const f = fixture();
  await f.runtime.init({ locale: "zh-Hans" });
  await f.runtime.setLocale("en");
  f.label.setAttribute("title", "停用");
  f.mutate([{ type: "attributes", target: f.label, attributeName: "title" }]);
  assert.equal(f.label.getAttribute("title"), "Disabled");
  await f.runtime.setLocale("zh-Hans");
  assert.equal(f.label.getAttribute("title"), "停用");
  assert.deepEqual(f.metrics.requests, ["en"]);
});

test("source locale restores a translated node detached during the language switch", async () => {
  const f = fixture();
  await f.runtime.init({ locale: "en" });
  f.root.children = [];
  f.label.isConnected = false;
  await f.runtime.setLocale("zh-Hans");
  assert.equal(f.text.nodeValue, "Settings");
  f.added(f.label);
  assert.equal(f.text.nodeValue, "设置");
  assert.equal(f.label.getAttribute("title"), "设置");
});

test("protected user content remains untouched across languages and mutations", async () => {
  const f = fixture();
  f.label.setAttribute("data-i18n-skip", "");
  await f.runtime.init({ locale: "en" });
  f.mutate([{ type: "characterData", target: f.text }]);
  assert.equal(f.text.nodeValue, "设置");
  assert.equal(f.label.getAttribute("title"), "设置");
});

test("latest locale selection wins over a slower catalog response", async () => {
  const pending = deferred<Catalog>();
  const f = fixture(async (locale) => locale === "en" ? pending.promise : identity);
  await f.runtime.init({ locale: "zh-Hans" });
  const old = f.runtime.setLocale("en");
  await f.runtime.setLocale("zh-Hans");
  pending.resolve(english);
  await old;
  assert.equal(f.runtime.locale, "zh-Hans");
  assert.equal(f.text.nodeValue, "设置");
});

test("simultaneous callers share a catalog request", async () => {
  const pending = deferred<Catalog>();
  const f = fixture(async (locale) => locale === "en" ? pending.promise : identity);
  await f.runtime.init({ locale: "zh-Hans" });
  const calls = [f.runtime.setLocale("en"), f.runtime.setLocale("en")];
  await Promise.resolve();
  pending.resolve(english);
  await Promise.all(calls);
  assert.equal(f.metrics.requests.filter((locale) => locale === "en").length, 1);
  assert.equal(f.text.nodeValue, "Settings");
});

test("failed and empty catalog responses remain retryable", async () => {
  let attempt = 0;
  const f = fixture(async (locale) => {
    if (locale !== "en") return identity;
    attempt++;
    if (attempt === 1) throw new Error("offline");
    if (attempt === 2) return { messages: {} };
    return english;
  });
  await f.runtime.init({ locale: "en" });
  assert.equal(f.text.nodeValue, "设置");
  await f.runtime.setLocale("en");
  await f.runtime.setLocale("en");
  assert.equal(attempt, 3);
  assert.equal(f.text.nodeValue, "Settings");
});

test("custom source catalog overrides are retained in both custom loader and base path", async () => {
  for (const customLoader of [true, false]) {
    const custom = { messages: { "设置": "自定义设置" }, patterns: {} };
    const f = fixture(async () => custom);
    await f.runtime.init(customLoader
      ? { locale: "zh-Hans", fetchCatalog: async () => custom }
      : { locale: "zh-Hans", basePath: "/custom/" });
    assert.equal(f.text.nodeValue, "自定义设置");
    assert.equal(f.label.getAttribute("title"), "自定义设置");
  }
});

test("changing custom catalog source cannot reuse or publish the earlier source", async () => {
  const pending = deferred<Catalog>();
  const started = deferred<void>();
  const f = fixture();
  const earlier = f.runtime.init({
    locale: "en",
    fetchCatalog: async (locale: string) => {
      if (locale !== "en") return identity;
      started.resolve();
      return pending.promise;
    },
  });
  await started.promise;
  await f.runtime.init({
    locale: "en",
    fetchCatalog: async (locale: string) => locale === "en"
      ? { messages: { "设置": "New settings" } }
      : identity,
  });
  pending.resolve(english);
  await earlier;
  await f.runtime.setLocale("en");
  assert.equal(f.text.nodeValue, "New settings");
  assert.equal(f.label.getAttribute("title"), "New settings");
});
