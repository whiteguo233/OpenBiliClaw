import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("settings page exposes advanced config fields from backend schema", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const expectedIds = [
    "cfgDataDir",
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
