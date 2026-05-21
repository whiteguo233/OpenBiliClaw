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
    "cfgLlmFallbackProvider",
    "cfgEmbeddingFallbackProvider",
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
    "cfgBilibiliEnabled",
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
    "cfgYoutubeDailySearchBudget",
    "cfgYoutubeDailyTrendingBudget",
    "cfgYoutubeDailyChannelBudget",
    "cfgYoutubeRequestInterval",
    "cfgYoutubeMinInterval",
    "cfgExtensionDisconnectGrace",
    "cfgRefreshCheckInterval",
    "cfgSignalEventThreshold",
    "cfgTrendingRefreshHours",
    "cfgExploreRefreshHours",
    "cfgDiscoveryLimit",
    "cfgProactivePushInterval",
    "cfgSpeculatorIdleInterval",
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
    "cfgLogPath",
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
  assert.doesNotMatch(popupHtml, /id="cfgDiscoveryCron"/);
  assert.doesNotMatch(popupJs, /discovery_cron:\s*getVal\("cfgDiscoveryCron"\)/);
  assert.match(
    popupJs,
    /setVal\("cfgRefreshCheckInterval", cfg\.scheduler\?\.refresh_check_interval_seconds\)/,
  );
  assert.match(
    popupJs,
    /refresh_check_interval_seconds: getInt\("cfgRefreshCheckInterval", 60\)/,
  );
});

test("settings source tab separates every platform into its own block", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const sourcesPanel =
    popupHtml.match(/<div id="settingsPanelSources"[\s\S]*?<div id="settingsPanelGeneral"/)?.[0] ??
    "";

  for (const sourceKey of ["bilibili", "xiaohongshu", "douyin", "youtube", "browser", "pool"]) {
    assert.match(
      sourcesPanel,
      new RegExp(`data-source-card="${sourceKey}"`),
      `${sourceKey} source card should exist`,
    );
  }
  assert.match(sourcesPanel, /id="cfgBilibiliEnabled"/);
  assert.match(sourcesPanel, />启用 Bilibili discovery</);
  assert.match(sourcesPanel, />调试：B 站登录时显示浏览器窗口</);
  assert.match(popupJs, /bilibiliEnabled\.checked = cfg\.sources\?\.bilibili\?\.enabled !== false/);
  assert.match(popupJs, /xhsEnabled\.checked = cfg\.sources\?\.xiaohongshu\?\.enabled === true/);
  assert.match(popupJs, /bilibili:\s*\{\s*enabled: checked\("cfgBilibiliEnabled", true\)/);
  assert.match(popupJs, /xiaohongshu:\s*\{\s*enabled: checked\("cfgXhsEnabled"\)/);
});

test("settings logging tab edits a single full log path", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const loggingPanel =
    popupHtml.match(/<div id="settingsPanelLogging"[\s\S]*?<p class="settings-note">/)?.[0] ?? "";

  assert.match(loggingPanel, /<label for="cfgLogPath">完整日志路径<\/label>/);
  assert.match(loggingPanel, /id="cfgLogPath"[^>]*placeholder="logs\/openbiliclaw\.log"/);
  assert.doesNotMatch(loggingPanel, /for="cfgLogDirectory"/);
  assert.doesNotMatch(loggingPanel, /for="cfgLogFilename"/);
  assert.match(popupJs, /setVal\("cfgLogPath", resolveLogPathFromConfig\(cfg\.logging\)\)/);
  assert.match(
    popupJs,
    /const logPath = splitLogPath\(getVal\("cfgLogPath"\), state\.runtimeConfig\?\.logging\)/,
  );
  assert.match(popupJs, /directory: logPath\.directory/);
  assert.match(popupJs, /filename: logPath\.filename/);
});

test("settings page organizes backend config into tabs", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const tabsMarkup = popupHtml.match(/<div class="settings-tabs"[\s\S]*?<\/div>/)?.[0] ?? "";
  const panelNames = ["models", "sources", "scheduler", "general", "logging"];

  assert.match(tabsMarkup, /role="tablist"/);
  for (const [id, label] of [
    ["settingsTabModels", "模型"],
    ["settingsTabSources", "平台源"],
    ["settingsTabScheduler", "调度"],
    ["settingsTabGeneral", "通用"],
    ["settingsTabLogging", "日志"],
  ]) {
    assert.match(tabsMarkup, new RegExp(`id="${id}"`));
    assert.match(tabsMarkup, new RegExp(`>${label}<`));
    assert.match(popupJs, new RegExp(`"${id}"`));
  }
  for (const name of panelNames) {
    assert.match(popupHtml, new RegExp(`data-settings-panel="${name}"`));
    assert.match(popupJs, new RegExp(`"${name}"`));
  }
});

test("settings page round-trips YouTube source budgets", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailySearchBudget", cfg\.sources\?\.youtube\?\.daily_search_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailyTrendingBudget", cfg\.sources\?\.youtube\?\.daily_trending_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeDailyChannelBudget", cfg\.sources\?\.youtube\?\.daily_channel_budget\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeRequestInterval", cfg\.sources\?\.youtube\?\.request_interval_seconds\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgYoutubeMinInterval", cfg\.sources\?\.youtube\?\.min_interval_minutes\)/,
  );
  assert.match(popupJs, /daily_search_budget: getInt\("cfgYoutubeDailySearchBudget", 6\)/);
  assert.match(popupJs, /daily_trending_budget: getInt\("cfgYoutubeDailyTrendingBudget", 50\)/);
  assert.match(popupJs, /daily_channel_budget: getInt\("cfgYoutubeDailyChannelBudget", 10\)/);
  assert.match(popupJs, /request_interval_seconds: getInt\("cfgYoutubeRequestInterval", 2\)/);
  assert.match(popupJs, /min_interval_minutes: getInt\("cfgYoutubeMinInterval", 60\)/);

  for (const id of [
    "cfgYoutubeDailySearchBudget",
    "cfgYoutubeDailyTrendingBudget",
    "cfgYoutubeDailyChannelBudget",
    "cfgYoutubeRequestInterval",
    "cfgYoutubeMinInterval",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`));
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

test("settings page round-trips explicit LLM and embedding fallback providers", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgLlmFallbackProvider"/);
  assert.match(popupHtml, /id="cfgEmbeddingFallbackProvider"/);
  assert.doesNotMatch(popupHtml, /id="cfgLlmFallbackEnabled"/);
  assert.doesNotMatch(popupHtml, /id="cfgEmbeddingFallbackEnabled"/);
  assert.match(popupJs, /setVal\("cfgLlmFallbackProvider", cfg\.llm\?\.fallback_provider\)/);
  assert.match(
    popupJs,
    /setVal\("cfgEmbeddingFallbackProvider", cfg\.llm\?\.embedding\?\.fallback_provider\)/,
  );
  assert.match(popupJs, /const llmFallbackProvider = getVal\("cfgLlmFallbackProvider"\)/);
  assert.match(popupJs, /fallback_provider: llmFallbackProvider/);
  assert.match(
    popupJs,
    /const embeddingFallbackProvider = getVal\("cfgEmbeddingFallbackProvider"\)/,
  );
  assert.match(
    popupJs,
    /fallback_provider: embeddingFallbackProvider/,
  );
});

test("settings page placeholders match config example defaults", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const expectedDefaults = [
    ["cfgOpenaiModel", "gpt-5-nano"],
    ["cfgClaudeModel", "claude-sonnet-4-6"],
    ["cfgOllamaModel", "qwen2.5:7b"],
    ["cfgOllamaBaseUrl", "http://localhost:11434/v1"],
    ["cfgOpenrouterModel", "openai/gpt-5-nano"],
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
  assert.match(suggestionBlock, /bilibili:\s*checked\("cfgBilibiliEnabled", true\)/);
  assert.match(suggestionBlock, /xiaohongshu:\s*checked\("cfgXhsEnabled"\)/);
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
