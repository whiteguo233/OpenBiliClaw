import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("settings page exposes advanced config fields from backend schema", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const expectedIds = [
    "cfgBackendPort",
    "cfgDataDir",
    "cfgOpenaiAuthMode",
    "cfgDeepseekReasoning",
    "cfgOpenrouterReferer",
    "cfgOpenrouterTitle",
    "cfgModuleSoulProvider",
    "cfgModuleSoulModel",
    "cfgModuleDiscoveryProvider",
    "cfgModuleDiscoveryModel",
    "cfgModuleRecommendationProvider",
    "cfgModuleRecommendationModel",
    "cfgModuleEvaluationProvider",
    "cfgModuleEvaluationModel",
    "cfgBiliBrowserExecutable",
    "cfgBiliBrowserHeaded",
    "cfgSourcesBrowserCdp",
    "cfgSourcesBrowserHeaded",
    "cfgXhsEnabled",
    "cfgXhsDailySearchBudget",
    "cfgXhsDailyCreatorBudget",
    "cfgXhsTaskInterval",
    "cfgDouyinEnabled",
    "cfgDouyinCookieEnv",
    "cfgDouyinDailySearchBudget",
    "cfgDouyinDailyHotBudget",
    "cfgDouyinDailyFeedBudget",
    "cfgDouyinRequestInterval",
    "cfgYoutubeEnabled",
    "cfgAccountSyncInterval",
    "cfgAutoUpdateInterval",
    "cfgPoolShareBilibili",
    "cfgPoolShareXhs",
    "cfgPoolShareDouyin",
    "cfgPoolShareYoutube",
    "cfgSuggestPoolShares",
    "cfgSpeculationInterval",
    "cfgSpeculationTtl",
    "cfgSpeculationCooldown",
    "cfgSpeculationThreshold",
    "cfgSpeculationMaxActive",
    "cfgSpeculationMaxPrimary",
    "cfgSpeculationMaxSecondary",
    "cfgStorageDbPath",
    "cfgLogFileLevel",
    "cfgLogDirectory",
    "cfgLogFilename",
    "cfgLogMaxFileSize",
    "cfgLogBackupCount",
    "cfgLogAggregateBudget",
    "cfgLogUnmanagedTruncate",
    "cfgLogUnmanagedMaxAge",
  ];

  for (const id of expectedIds) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }
});

test("settings page round-trips OpenAI auth mode", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgOpenaiAuthMode"/);
  assert.match(popupHtml, /<option value="api_key">API Key<\/option>/);
  assert.match(popupHtml, /<option value="codex_oauth">Codex OAuth<\/option>/);
  assert.match(
    popupJs,
    /setVal\("cfgOpenaiAuthMode", cfg\.llm\?\.openai\?\.auth_mode \|\| "api_key"\)/,
  );
  assert.match(popupJs, /auth_mode: getVal\("cfgOpenaiAuthMode"\) \|\| "api_key"/);
});

test("settings page placeholders match config example defaults", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const expectedDefaults = [
    ["cfgOpenaiModel", "gpt-5-nano"],
    ["cfgClaudeModel", "claude-sonnet-4-6"],
    ["cfgOllamaModel", "qwen2.5:7b"],
    ["cfgOllamaBaseUrl", "http://localhost:11434/v1"],
    ["cfgOpenrouterModel", "openai/gpt-5-nano"],
    ["cfgDiscoveryCron", "0 */8 * * *"],
  ];

  for (const [id, placeholder] of expectedDefaults) {
    assert.match(
      popupHtml,
      new RegExp(`id="${id}"[^>]*placeholder="${placeholder.replaceAll("*", "\\*")}"`),
      `${id} placeholder should match config.example.toml default`,
    );
  }
});

test("source-share suggestion button uses settings-scope helpers and form switches", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const bindSettingsBlock =
    popupJs.match(/function bindSettings\(\) \{[\s\S]*?\nasync function initializePopup/)?.[0] ?? "";
  const populateFormIndex = bindSettingsBlock.indexOf("function populateForm");
  const collectFormIndex = bindSettingsBlock.indexOf("function collectForm");
  const populateFormBlock = bindSettingsBlock.slice(populateFormIndex, collectFormIndex);
  const beforePopulate = bindSettingsBlock.slice(0, populateFormIndex);
  const suggestionBlock =
    bindSettingsBlock.match(/suggestBtn\.addEventListener\("click"[\s\S]*?\n  \}\n\n  saveBtn/)?.[0] ?? "";

  assert.match(beforePopulate, /const setVal = \(id, val\) => \{/);
  assert.doesNotMatch(populateFormBlock, /const setVal = \(id, val\) => \{/);
  assert.match(suggestionBlock, /fetchSourceShareSuggestion\(\{/);
  assert.match(suggestionBlock, /enabled_sources:\s*\{/);
  assert.match(suggestionBlock, /xiaohongshu:\s*checked\("cfgXhsEnabled", true\)/);
  assert.match(suggestionBlock, /youtube:\s*checked\("cfgYoutubeEnabled"\)/);
  assert.match(suggestionBlock, /configured_shares:\s*\{/);
});

test("settings save renders structured config validation errors inline", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const bindSettingsBlock =
    popupJs.match(/function bindSettings\(\) \{[\s\S]*?\nasync function initializePopup/)?.[0] ?? "";
  const saveBlock =
    popupJs.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0] ?? "";
  const structuredErrorBlock =
    bindSettingsBlock.match(/function renderStructuredConfigError[\s\S]*?\n  \}/)?.[0] ?? "";

  assert.match(structuredErrorBlock, /err\.details\?\.config\?\.issues/);
  assert.match(structuredErrorBlock, /applyRuntimeConfig\(err\.details\.config\)/);
  assert.match(structuredErrorBlock, /renderIssues\(err\.details\.config\.issues\)/);
  assert.match(structuredErrorBlock, /配置未保存，请先修正高亮问题。/);
  assert.match(structuredErrorBlock, /showToast\([^)]*,\s*"error"\)/);
  assert.match(saveBlock, /renderStructuredConfigError\(err\)/);
});

test("settings save renders timeout warning before structured or generic errors", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const saveBlock =
    popupJs.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0] ?? "";

  const abortIndex = saveBlock.indexOf('err?.name === "AbortError"');
  const structuredIndex = saveBlock.indexOf("renderStructuredConfigError(err)");
  const genericIndex = saveBlock.indexOf("保存失败");
  const successIndex = saveBlock.indexOf("applyRuntimeConfig(result.config)");

  assert.notEqual(abortIndex, -1, "save handler should special-case AbortError");
  assert.match(saveBlock, /后端处理超时[\s\S]*保存请求可能已写入[\s\S]*后台/);
  assert.match(saveBlock, /showToast\([\s\S]*"warning"[\s\S]*\)/);
  assert.ok(abortIndex < structuredIndex, "AbortError should be handled before structured errors");
  assert.ok(abortIndex < genericIndex, "AbortError should not fall through to generic error toast");
  assert.ok(abortIndex > successIndex, "AbortError branch should wrap the updateConfig call");
  assert.match(saveBlock, /return;/);
  assert.match(saveBlock, /finally[\s\S]*saveBtn\.disabled = false/);
  assert.match(saveBlock, /finally[\s\S]*setSaveButtonMode/);
});

test("settings page wires offline cache and degraded-mode banners", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of ["cfgBannerOffline", "cfgBannerDegraded", "cfgBannerNoCache"]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /readCachedConfigSnapshot/);
  assert.match(popupJs, /cached_at/);
  assert.match(popupJs, /后端不可达且没有缓存配置/);
  assert.match(popupJs, /renderDegradedBanner\(cfg\)/);
  assert.match(popupJs, /restart_required/);
  assert.match(popupJs, /保存并提示重启/);
});
