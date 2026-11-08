import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const desktop = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");

function functionSource(name) {
  const start = desktop.indexOf(`    function ${name}(`);
  assert.ok(start >= 0, name);
  return desktop.slice(start, desktop.indexOf("\n    }\n", start) + 6);
}

function setup() {
  const errors = [];
  const toasts = [];
  const sourceFilterDefinitions = [
    { key: "bilibili", label: "B 站" },
    { key: "web", label: "Web" },
  ];
  const context = vm.createContext({
    state: {
      query: "", filter: "全部",
      config: { sources: { bilibili: { enabled: true }, web: { enabled: true } } },
      videos: [
        { id: 1, platform: "web", source_id: "custom_books", source_name: "哔哩轻小说", title: "书目一" },
        { id: 2, platform: "bilibili", title: "视频" },
        { id: 3, platform: "web", source_id: "custom_atom", source_name: "技术 Atom", title: "文章" },
        { id: 4, platform: "web", source_id: "", source_name: "", title: "旧版订阅" },
      ],
      platformAvailability: null,
    },
    sourceFilterDefinitions,
    sourceFilterOrder: sourceFilterDefinitions.map(source => source.label),
    platformLabel: { bilibili: "B 站", web: "Web" },
    platformAliases: { bili: "bilibili" },
    recommendationKey: item => String(item.id),
    console: { error: (...args) => errors.push(args) },
    showToast: value => toasts.push(value),
  });
  for (const name of [
    "platformName", "recommendationSourceName", "canonicalPlatformSlug", "recommendationPlatformSlug",
    "normalizePlatformAvailability", "platformSlugForFilterLabel", "activePlatformSlug", "platformAvailableCount",
    "activePlatformAvailableCount", "availablePlatformSlugs", "configuredSourceFilterLabels", "buildFilters",
    "filteredVideos", "replacePlatformCards", "reportPlatformScopeLeak",
  ]) vm.runInContext(functionSource(name), context);
  return { context, errors, toasts };
}

test("custom-source inventory remains one canonical Web tab while cards retain source names", () => {
  const { context } = setup();
  // Public availability is grouped by content platform; independent source IDs
  // remain quota/provenance keys and must never become source_platform requests.
  context.state.platformAvailability = context.normalizePlatformAvailability({
    total_available: 9, by_platform: { web: 7, bilibili: 2 }, pool_status_version: 1,
  });
  assert.deepEqual(Array.from(context.buildFilters()), ["全部", "B 站", "Web"]);
  assert.equal(context.platformAvailableCount("web"), 7);
  assert.equal(context.platformAvailableCount(""), 9);
  assert.equal(context.recommendationSourceName(context.state.videos[0]), "哔哩轻小说");
  assert.equal(context.recommendationSourceName(context.state.videos[2]), "技术 Atom");
  assert.equal(context.recommendationSourceName(context.state.videos[3]), "Web");
});

test("Web filtering includes multiple custom sources and legacy recipes without sending a custom ID", () => {
  const { context, errors } = setup();
  context.state.filter = "Web";
  assert.equal(context.activePlatformSlug(), "web");
  const visible = context.filteredVideos();
  assert.deepEqual(Array.from(visible, item => item.id), [1, 3, 4]);
  assert.equal(context.reportPlatformScopeLeak("加载更多", context.activePlatformSlug(), visible), false);
  assert.equal(errors.length, 0);
});

test("a Web replacement updates both custom sources and legacy cards but preserves other platforms", () => {
  const { context } = setup();
  const fresh = [{ id: 5, platform: "web", source_id: "custom_books", source_name: "哔哩轻小说" }];
  const next = context.replacePlatformCards(context.state.videos, "web", fresh);
  assert.deepEqual(Array.from(next, item => item.id), [5, 2]);
  assert.equal(next[1], context.state.videos[1]);
});

test("canonical Web scope still rejects a response leaking another platform", () => {
  const { context, errors, toasts } = setup();
  assert.equal(context.reportPlatformScopeLeak("加载更多", "web", context.state.videos), true);
  assert.equal(errors.length, 1);
  assert.match(toasts[0], /1 条非「Web」/);
});
