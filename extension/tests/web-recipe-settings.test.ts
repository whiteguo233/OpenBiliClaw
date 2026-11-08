import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  createWebSourceRecipe,
  deleteWebSourceRecipe,
  fetchSourceRecipes,
  probeWebSource,
  updateWebSourceRecipe,
} from "../popup/popup-api.js";

test("popup source settings expose an accessible anonymous Web recipe manager", () => {
  const html = readFileSync(resolve("popup", "popup.html"), "utf8");
  const source = readFileSync(resolve("popup", "popup.js"), "utf8");
  const webCard = html.match(/data-source-card="web"[\s\S]*?data-source-card="pool"/)?.[0] ?? "";

  assert.match(webCard, /id="cfgWebEnabled" type="checkbox"/);
  assert.match(webCard, /data-source-status="web"/);
  assert.match(webCard, /id="webRecipeForm"/);
  assert.match(webCard, /id="webRecipeName"/);
  assert.match(webCard, /id="webRecipeStrategy"/);
  assert.match(webCard, /<option value="feed">/);
  assert.match(webCard, /<option value="sitemap">/);
  assert.match(webCard, /<option value="listing">/);
  assert.match(webCard, /id="webRecipeEntryUrl"[^>]*type="url"/);
  assert.match(webCard, /id="webRecipeItemSelector"/);
  assert.match(webCard, /id="webRecipeLinkSelector"/);
  assert.match(webCard, /id="webProbeForm"/);
  assert.match(webCard, /id="webProbeUrl"[^>]*type="url"[^>]*data-settings-ignore-dirty/);
  assert.match(webCard, /id="webProbeRun"[^>]*type="submit"[^>]*>自动探测</);
  assert.match(webCard, /id="webProbeResult"[^>]*aria-live="polite"[^>]*aria-busy="false"/);
  assert.match(webCard, /id="webRecipeForm"[^>]*tabindex="-1"/);
  assert.match(webCard, /探测不会保存网站/);
  assert.match(webCard, /id="webRecipeList"[^>]*aria-live="polite"/);
  assert.match(webCard, /id="webRecipeMessage"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(webCard, /robots\.txt/);
  assert.match(webCard, /不发送 Cookie/);
  assert.match(webCard, /不执行 JavaScript/);
  assert.match(webCard, /公开/);

  assert.match(source, /webEnabled\.checked = cfg\.sources\?\.web\?\.enabled !== false/);
  assert.match(source, /web:\s*\{\s*\.\.\.\(state\.runtimeConfig\?\.sources\?\.web/);
  assert.match(source, /enabled: checked\("cfgWebEnabled", true\)/);
  assert.match(source, /setVal\("cfgPoolShareWeb", cfg\.scheduler\?\.pool_source_shares\?\.web\)/);
  assert.match(source, /web: getInt\("cfgPoolShareWeb", 1\)/);
  assert.match(source, /function renderWebRecipes/);
  assert.match(source, /\["feed", "sitemap", "listing"\]\.includes/);
  assert.match(source, /config\?\.plan_version/);
  assert.match(source, /function formatWebRecipeError/);
  assert.match(source, /error\?\.details\?\.detail/);
  assert.match(source, /data-web-recipe-action/);
  assert.match(source, /createWebSourceRecipe/);
  assert.match(source, /updateWebSourceRecipe/);
  assert.match(source, /deleteWebSourceRecipe/);
  assert.match(source, /probeWebSource\(requestedUrl\)/);
  assert.match(source, /payload\?\.candidates/);
  assert.match(source, /sample_items/);
  assert.match(source, /candidate\.confidence === "high" \? "高置信度" : "中置信度"/);
  assert.match(source, /WEB_PROBE_DISCOVERY_METHOD_LABELS\[candidate\.discovery_method\]/);
  assert.match(source, /useButton\.textContent = "采用此方案"/);
});

test("Web probe API posts only the trimmed URL to the fixed endpoint", async () => {
  const apiSource = readFileSync(resolve("popup", "popup-api.js"), "utf8");
  const calls: Array<{ url: string; options: RequestInit }> = [];
  const originalSetTimeout = globalThis.setTimeout;
  const timeoutDelays: number[] = [];
  globalThis.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: any[]) => {
    timeoutDelays.push(Number(timeout));
    return originalSetTimeout(handler, timeout, ...args);
  }) as typeof setTimeout;
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          status: "no_match",
          detail: "No public discovery surface found",
          requested_url: "https://example.com/",
          final_url: "https://example.com/",
          page_title: "Example",
          candidates: [],
        };
      },
    } as Response;
  }) as typeof fetch;

  let payload;
  try {
    payload = await probeWebSource("  https://example.com/  ");
  } finally {
    globalThis.setTimeout = originalSetTimeout;
  }

  assert.equal(payload.status, "no_match");
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/api\/sources\/web\/probe$/);
  assert.equal(calls[0].options.method, "POST");
  assert.match(apiSource, /probeWebSource[\s\S]*?timeoutMs: 65_000/);
  assert.ok(timeoutDelays.includes(65_000));
  assert.deepEqual(JSON.parse(String(calls[0].options.body)), {
    url: "https://example.com/",
  });
});

test("Web probe API preserves backend validation detail", async () => {
  globalThis.fetch = (async () => ({
    ok: false,
    status: 422,
    async json() {
      return { detail: "URL host resolves to a private address" };
    },
  })) as typeof fetch;

  await assert.rejects(
    () => probeWebSource("http://127.0.0.1/private"),
    (error: any) => {
      assert.equal(error.status, 422);
      assert.equal(error.details.detail, "URL host resolves to a private address");
      return true;
    },
  );
});

test("Web probe UI fences concurrent responses and adoption never saves", () => {
  const source = readFileSync(resolve("popup", "popup.js"), "utf8");
  const renderStart = source.indexOf("function renderWebProbeResult");
  const renderEnd = source.indexOf("function applyWebProbeCandidate", renderStart);
  const renderSource = source.slice(renderStart, renderEnd);
  const submitStart = source.indexOf("async function submitWebProbe");
  const submitEnd = source.indexOf("async function submitWebRecipe", submitStart);
  const submitSource = source.slice(submitStart, submitEnd);
  const applyStart = source.indexOf("function applyWebProbeCandidate");
  const applyEnd = source.indexOf("async function submitWebProbe", applyStart);
  const applySource = source.slice(applyStart, applyEnd);
  const initStart = source.indexOf("function initWebRecipeManager");
  const initEnd = source.indexOf("// ---- 设置页吸底保存栏", initStart);
  const initSource = source.slice(initStart, initEnd);

  assert.ok(submitStart >= 0 && submitEnd > submitStart);
  assert.match(submitSource, /webProbeInFlight/);
  assert.match(submitSource, /requestSerial !== webProbeRequestSerial/);
  assert.match(submitSource, /input\.value\.trim\(\) !== requestedUrl/);
  assert.match(submitSource, /setWebProbeBusy\(true\)/);
  assert.match(submitSource, /setWebProbeBusy\(false\)/);
  assert.match(submitSource, /formatWebRecipeError\(error/);

  assert.ok(renderStart >= 0 && renderEnd > renderStart);
  assert.match(renderSource, /payload\?\.status/);
  assert.match(renderSource, /payload\?\.candidates/);
  assert.match(renderSource, /candidate\.detail/);
  assert.match(renderSource, /candidate\.sample_items/);
  assert.match(renderSource, /\.textContent =/);
  assert.doesNotMatch(renderSource, /\.innerHTML\s*=/);

  assert.ok(applyStart >= 0 && applyEnd > applyStart);
  assert.match(applySource, /setVal\("webRecipeName", recipe\.name\)/);
  assert.match(applySource, /setVal\("webRecipeStrategy", recipe\.strategy\)/);
  assert.match(applySource, /setVal\("webRecipeEntryUrl", config\.entry_url \|\| candidate\.entry_url\)/);
  assert.match(applySource, /document\.getElementById\("webRecipeName"\)/);
  assert.match(applySource, /firstField\?\.focus\?\./);
  assert.match(applySource, /点击“添加网站”才会保存/);
  assert.doesNotMatch(applySource, /createWebSourceRecipe|submitWebRecipe/);

  assert.match(applySource, /function invalidateWebProbeResult/);
  assert.match(applySource, /if \(webProbeInFlight \|\| !webProbeCandidates\.length\) return/);
  assert.match(applySource, /webProbeCandidates = \[\]/);
  assert.match(applySource, /URL 已更改，请重新探测/);
  assert.match(initSource, /getElementById\("webProbeUrl"\)/);
  assert.match(initSource, /addEventListener\("input", invalidateWebProbeResult\)/);
});

test("Web recipe API helpers use typed source CRUD endpoints", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: options.method === "POST" ? 201 : 200,
      async json() {
        if (options.method === "GET") return { items: [] };
        return { ok: true, recipe: { id: "recipe-1" } };
      },
    } as Response;
  }) as typeof fetch;

  await fetchSourceRecipes();
  await createWebSourceRecipe({
    name: "Example feed",
    strategy: "feed",
    config: { entry_url: "https://example.com/feed.xml" },
  });
  await updateWebSourceRecipe("recipe / 1", { enabled: false });
  await deleteWebSourceRecipe("recipe / 1");

  assert.match(calls[0].url, /\/api\/sources$/);
  assert.equal(calls[0].options.method, "GET");
  assert.match(calls[1].url, /\/api\/sources$/);
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[1].options.body)), {
    name: "Example feed",
    strategy: "feed",
    config: { entry_url: "https://example.com/feed.xml" },
    source_type: "web",
  });
  assert.match(calls[2].url, /\/api\/sources\/recipe%20%2F%201$/);
  assert.equal(calls[2].options.method, "PUT");
  assert.deepEqual(JSON.parse(String(calls[2].options.body)), { enabled: false });
  assert.match(calls[3].url, /\/api\/sources\/recipe%20%2F%201$/);
  assert.equal(calls[3].options.method, "DELETE");
});

test("Web recipe API keeps backend 422 validation details", async () => {
  globalThis.fetch = (async () => ({
    ok: false,
    status: 422,
    async json() {
      return { detail: "listing plan is invalid: item_selector is required" };
    },
  })) as typeof fetch;

  await assert.rejects(
    () => createWebSourceRecipe({
      name: "Broken listing",
      strategy: "listing",
      config: { entry_url: "https://example.com/news" },
    }),
    (error: any) => {
      assert.equal(error.status, 422);
      assert.equal(error.details.detail, "listing plan is invalid: item_selector is required");
      return true;
    },
  );
});
