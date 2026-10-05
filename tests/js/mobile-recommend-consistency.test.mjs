import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const apiSource = readFileSync("src/openbiliclaw/web/js/api.js", "utf8");
const viewSource = readFileSync("src/openbiliclaw/web/js/views/recommend.js", "utf8");
const modelSource = readFileSync("src/openbiliclaw/web/js/view-models.js", "utf8");
function fn(source, name) {
  const start = source.search(new RegExp(`(?:export )?(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf("\n}\n", start) + 2).replace(/^export /, "");
}

test("request deadline covers a stalled response body", async () => {
  globalThis.location = { protocol: "http:", host: "test.invalid" };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, { signal }) => ({
    ok: true,
    json: () => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }),
  });
  try {
    const api = await import(`data:text/javascript;base64,${Buffer.from(apiSource).toString("base64")}`);
    const result = await Promise.race([
      api.requestJson("/slow-body", { timeoutMs: 20 }).then(() => "resolved", e => e.name),
      new Promise(resolve => setTimeout(() => resolve("deadline lost"), 200)),
    ]);
    assert.equal(result, "AbortError");
    let signal;
    globalThis.fetch = async (_url, options) => {
      signal = options.signal;
      return { ok: true, json: async () => ({ items: [] }) };
    };
    await api.reshuffleRecommendations([]);
    assert.ok(signal instanceof AbortSignal, "reshuffle must also have a deadline");
  } finally { globalThis.fetch = originalFetch; }
});

function harness() {
  let observerCallback;
  let appendCalls = 0;
  const context = vm.createContext({
    state: { activeTab: "recommend", runtimeStatus: {} },
    loading: false, autoAppendExhausted: false, autoAppendWaitingForRefill: false,
    autoAppendVisible: false, autoAppendUserArmed: false, autoAppendTouchY: null,
    autoAppendObserver: null, poolStatusVersion: 0, runtimeStatusGeneration: 0,
    $root: { querySelector: () => ({}) }, document: { getElementById: () => ({}) },
    AUTO_APPEND_ROOT_MARGIN: "0px",
    IntersectionObserver: class {
      constructor(cb) { observerCallback = cb; }
      observe() {} disconnect() {}
    },
    handleAppend: () => { appendCalls += 1; },
    clearRuntimeStatusRecovery() {}, rerenderRuntimeDependentChrome() {},
    mergeRuntimeStatusEvent: (old, update) => ({ ...old, ...update }),
  });
  context.patchState = patch => Object.assign(context.state, patch);
  vm.runInContext([
    fn(modelSource, "shouldAutoAppendRecommendations"),
    ...["disconnectAutoAppendObserver", "maybeAutoAppend", "observeAutoAppendSentinel",
      "armAutoAppendIntent", "applyCommittedPoolStatus"].map(name => fn(viewSource, name)),
  ].join("\n"), context);
  return { context, intersect: visible => observerCallback([{ isIntersecting: visible }]), calls: () => appendCalls };
}

test("scroll intent rechecks an already visible sentinel", () => {
  const h = harness();
  h.context.observeAutoAppendSentinel(); h.intersect(true);
  assert.equal(h.calls(), 0);
  h.context.armAutoAppendIntent();
  assert.equal(h.calls(), 1);
  h.intersect(false); h.context.armAutoAppendIntent();
  assert.equal(h.calls(), 1, "do not load outside viewport");
});

test("refill resumes an exhausted visible feed and ignores stale inventory", () => {
  const h = harness();
  h.context.observeAutoAppendSentinel(); h.intersect(true);
  h.context.autoAppendExhausted = true; h.context.autoAppendWaitingForRefill = true;
  h.context.applyCommittedPoolStatus({ pool_available_count: 10, pool_status_version: 20 });
  assert.equal(h.calls(), 1);
  assert.equal(h.context.autoAppendExhausted, false);
  h.context.applyCommittedPoolStatus({ pool_available_count: 0, pool_status_version: 10 });
  assert.equal(h.context.state.runtimeStatus.pool_available_count, 10);
});

test("reshuffle failures release loading and preserve cards", async () => {
  const h = harness(); const cards = [{ bvid: "current" }];
  Object.assign(h.context, {
    render() {}, resetAutoAppendIntent() {},
    reshuffleRecommendations: async () => { throw new Error("timeout"); },
    recommendationActionMessage: "",
  });
  h.context.state.recommendations = cards;
  vm.runInContext(fn(viewSource, "handleReshuffle"), h.context);
  await h.context.handleReshuffle();
  assert.equal(h.context.loading, false);
  assert.equal(h.context.state.recommendations, cards);
  assert.match(h.context.recommendationActionMessage, /失败/);
});

test("repeated positive inventory is not mistaken for a refill after an empty page", () => {
  const h = harness();
  h.context.observeAutoAppendSentinel(); h.intersect(true);
  h.context.state.runtimeStatus.pool_available_count = 10;
  h.context.autoAppendExhausted = true; h.context.autoAppendWaitingForRefill = true;
  h.context.applyCommittedPoolStatus({ pool_available_count: 10, pool_status_version: 21 });
  assert.equal(h.calls(), 0);
  assert.equal(h.context.autoAppendExhausted, true);
});

test("desktop empty-page gate waits for stock in the selected source", () => {
  const source = readFileSync("src/openbiliclaw/web/desktop/assets/js/app.js", "utf8");
  const start = source.indexOf("    function autoLoadBlockReason(");
  const end = source.indexOf("\n    }\n", start) + 6;
  let available = 5;
  const context = vm.createContext({
    state: { autoLoadOnScroll: true, runtimeStatus: { pool_available_count: 100 } },
    appendMoreInFlight: false, emptyAppendInventory: new Map([["zhihu", 5]]),
    activePlatformAvailableCount: () => available, activePlatformSlug: () => "zhihu",
    $: () => ({ hidden: false }), grid: { querySelector: () => ({}) },
    lastAutoLoadAt: 0, AUTO_LOAD_COOLDOWN_MS: 8000,
  });
  vm.runInContext(source.slice(start, end), context);
  assert.equal(context.autoLoadBlockReason(10000), "awaiting-refill");
  available = 6;
  assert.equal(context.autoLoadBlockReason(10000), "");
  available = 0;
  assert.equal(context.autoLoadBlockReason(10000), "pool-empty");
});

test("desktop committed inventory renders the headline and rejects older replies", () => {
  const source = readFileSync("src/openbiliclaw/web/desktop/assets/js/app.js", "utf8");
  function desktopFn(name) {
    const start = source.indexOf(`    function ${name}(`);
    return source.slice(start, source.indexOf("\n    }\n", start) + 6);
  }
  const nodes = new Map();
  const context = vm.createContext({
    state: { runtimeStatus: {}, platformAvailability: null }, platformPoolStatusVersion: 0,
    normalizePlatformAvailability: value => value,
    normalizeRuntimeStatus: value => value,
    getPoolStatusSummary: () => ({}), getPoolRefreshLabel: () => "",
    renderDesktopRuntimeFailure() {}, renderFilters() {}, maybeAutoLoadAfterPoolRefill() {},
    $: key => { if (!nodes.has(key)) nodes.set(key, {}); return nodes.get(key); },
  });
  vm.runInContext(["applyCommittedPoolStatus", "renderPoolStatus"].map(desktopFn).join("\n"), context);
  context.applyCommittedPoolStatus({ pool_available_count: 18, platform_available_counts: { github: 18 }, pool_status_version: 20 });
  assert.equal(nodes.get("#metricPool").textContent, "18");
  assert.equal(context.state.platformAvailability.by_platform.github, 18);
  context.applyCommittedPoolStatus({ pool_available_count: 22, pool_status_version: 10 });
  assert.equal(nodes.get("#metricPool").textContent, "18");
});

test("mobile append releases the reshuffle header rebuilt by inventory updates", async () => {
  const h = harness();
  const button = {};
  let headerLoading;
  Object.assign(h.context, {
    $root: { querySelector: selector => selector.endsWith("button") ? button : { querySelector: () => button } },
    resetAutoAppendIntent() {},
    appendRecommendations: async () => ({ items: [], has_more: false, pool_status: { pool_available_count: 0, pool_status_version: 30 } }),
    normalizeRecommendation: value => value,
    recommendationActionMessage: "", CARD_EAGER_COVER_COUNT: 2,
    warmRecommendationCovers() {},
    getMobileRecommendationHeaderState: () => ({ secondaryActionLabel: "加载更多" }),
    rerenderHeaderOnly: () => { headerLoading = h.context.loading; },
    rerenderRuntimeDependentChrome: () => { headerLoading = h.context.loading; },
  });
  h.context.state.recommendations = [{ bvid: "keep" }];
  vm.runInContext(fn(viewSource, "handleAppend"), h.context);
  await h.context.handleAppend();
  assert.equal(headerLoading, false);
  assert.equal(button.disabled, false);
  assert.equal(h.context.state.recommendations.length, 1);
});
