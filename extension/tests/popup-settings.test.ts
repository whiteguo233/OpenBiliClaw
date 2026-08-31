import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("settings page exposes advanced config fields from backend schema", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const expectedIds = [
    "cfgBackendScheme",
    "cfgBackendPort",
    "cfgExtDeviceKey",
    "cfgDataDir",
    "cfgLlmRoutingSummary",
    "cfgAddLlmInstance",
    "cfgLlmInstanceList",
    "cfgLlmDefaultChain",
    "cfgLlmDefaultChainPicker",
    "cfgAddLlmDefaultChainItem",
    "cfgLlmModuleSummary",
    "cfgLlmInstanceDialog",
    "cfgSaveLlmInstance",
    "cfgLlmConcurrencyV2",
    "cfgLlmTimeoutV2",
    "cfgOpenDesktopModels",
    "cfgProbeLlmChain",
    "cfgEmbeddingFallbackProvider",
    "cfgEmbeddingMultimodalEnabled",
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
    "cfgBiliDatePreset",
    "cfgBiliDateStart",
    "cfgBiliDateEnd",
    "cfgBiliDateWeight",
    "cfgXhsEnabled",
    "cfgXhsDailySearchBudget",
    "cfgXhsDailyCreatorBudget",
    "cfgXhsTaskInterval",
    "cfgXhsMinInterval",
    "cfgDouyinEnabled",
    "cfgDouyinCookie",
    "cfgDouyinCookieEnv",
    "cfgTwitterCookie",
    "cfgDouyinDailySearchBudget",
    "cfgDouyinDailyHotBudget",
    "cfgDouyinDailyFeedBudget",
    "cfgDouyinRequestInterval",
    "cfgWeiboEnabled",
    "cfgWeiboModeSearch",
    "cfgWeiboModeHot",
    "cfgWeiboModeCreator",
    "cfgWeiboDailySearchBudget",
    "cfgWeiboDailyHotBudget",
    "cfgWeiboDailyCreatorBudget",
    "cfgWeiboRequestInterval",
    "cfgWeiboMinInterval",
    "cfgYoutubeEnabled",
    "cfgYoutubeDailySearchBudget",
    "cfgYoutubeDailyTrendingBudget",
    "cfgYoutubeDailyChannelBudget",
    "cfgYoutubeRequestInterval",
    "cfgYoutubeMinInterval",
    "cfgRedditEnabled",
    "cfgRedditBackend",
    "cfgRedditCookie",
    "cfgRedditModeSearch",
    "cfgRedditModeHot",
    "cfgRedditModeSubreddit",
    "cfgRedditModeRelated",
    "cfgRedditDailySearchBudget",
    "cfgRedditDailyHotBudget",
    "cfgRedditDailySubredditBudget",
    "cfgRedditDailyRelatedBudget",
    "cfgRedditRequestInterval",
    "cfgRedditMinInterval",
    "cfgExtensionDisconnectGrace",
    "cfgRefreshCheckInterval",
    "cfgSignalEventThreshold",
    "cfgTrendingRefreshMinutes",
    "cfgExploreRefreshMinutes",
    "cfgDiscoveryLimit",
    "cfgEvalScorer",
    "cfgVisualProfileEnabled",
    "cfgKeyframeEnabled",
    "cfgKeyframeMaxFrames",
    "cfgKeyframeFetchLimit",
    "cfgDanmakuEnabled",
    "cfgDanmakuFetchLimit",
    "cfgDanmakuMaxChars",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
    "cfgProactivePushInterval",
    "cfgSpeculatorIdleInterval",
    "cfgAccountSyncInterval",
    "backendUpdateCurrent",
    "backendUpdateLatest",
    "backendUpdateState",
    "backendUpdateLastCheck",
    "backendUpdateCheck",
    "backendUpdateApply",
    "backendUpdateError",
    "extensionVersionValue",
    "cfgAutoUpdateInterval",
    "cfgPoolShareBilibili",
    "cfgPoolShareXhs",
    "cfgPoolShareDouyin",
    "cfgPoolShareWeibo",
    "cfgPoolShareYoutube",
    "cfgPoolShareReddit",
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
  assert.doesNotMatch(popupHtml, /cfgExtLoginPassword|扩展登录密码/);
  assert.doesNotMatch(popupJs, /obc_auth_password|obc_auth_token/);
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
  assert.match(popupJs, /function formatBackendUpdateError/);
  assert.match(popupJs, /github_rate_limited:\s*"GitHub API 限流，请稍后再试"/);
});

test("settings source tab separates every platform into its own block", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const sourcesPanel =
    popupHtml.match(/<div id="settingsPanelSources"[\s\S]*?<div id="settingsPanelGeneral"/)?.[0] ??
    "";

  for (const sourceKey of [
    "bilibili",
    "xiaohongshu",
    "douyin",
    "weibo",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "browser",
    "pool",
  ]) {
    assert.match(
      sourcesPanel,
      new RegExp(`data-source-card="${sourceKey}"`),
      `${sourceKey} source card should exist`,
    );
  }
  assert.match(sourcesPanel, /id="cfgBilibiliEnabled"/);
  assert.match(sourcesPanel, />启用 Bilibili discovery</);
  assert.match(sourcesPanel, /id="cfgBiliDatePreset"/);
  assert.match(sourcesPanel, />B站发布日期范围</);
  assert.match(popupJs, /recommendation_date_preset: getVal\("cfgBiliDatePreset"\) \|\| "all"/);
  assert.match(popupJs, /recommendation_date_weight: Math\.min\(/);
  assert.match(popupJs, /ensurePopupSourceDateFields/);
  assert.match(popupJs, /popupSourceDateFieldsForUpdate/);
  for (const slug of [
    "xiaohongshu",
    "douyin",
    "weibo",
    "youtube",
    "twitter",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
  ]) {
    assert.ok(
      popupJs.includes(`popupSourceDateFieldsForUpdate("${slug}")`),
      `${slug} date fields should be wired`,
    );
  }
  assert.match(sourcesPanel, />调试：B 站登录时显示浏览器窗口</);

  // Keep the Linux.do card closed before the V2EX card starts. If either
  // wrapper is left open, the browser nests every later settings panel under
  // the hidden sources panel and tabs such as 通用 render as a blank page.
  const linuxdoStart = popupHtml.indexOf('data-source-card="linuxdo"');
  const v2exStart = popupHtml.indexOf(
    '<div class="settings-section settings-source-card" data-source-card="v2ex"',
  );
  assert.ok(linuxdoStart >= 0 && v2exStart > linuxdoStart);
  const linuxdoBlock = popupHtml.slice(linuxdoStart, v2exStart);
  assert.match(
    linuxdoBlock,
    /id="cfgLinuxdoBootstrapLimit"[\s\S]*?\n\s*<\/div>\s*\n\s*<\/div>\s*$/,
    "Linux.do source body and card must close before V2EX starts",
  );
  assert.match(popupJs, /face\.tabIndex = on \? 0 : -1/);
  assert.match(popupJs, /face\.setAttribute\("aria-disabled", on \? "false" : "true"\)/);
  assert.match(popupJs, /bilibiliEnabled\.checked = cfg\.sources\?\.bilibili\?\.enabled !== false/);
  assert.match(popupJs, /xhsEnabled\.checked = cfg\.sources\?\.xiaohongshu\?\.enabled === true/);
  assert.match(popupJs, /bilibili:\s*\{\s*enabled: checked\("cfgBilibiliEnabled", true\)/);
  assert.match(popupJs, /xiaohongshu:\s*\{\s*enabled: checked\("cfgXhsEnabled"\)/);
  assert.match(
    popupJs,
    /daily_search_budget: getInt\("cfgXhsDailySearchBudget", 20\)/,
  );
  assert.match(popupJs, /task_interval_seconds: getInt\("cfgXhsTaskInterval", 1200\)/);
  assert.match(popupJs, /min_interval_minutes: getInt\("cfgXhsMinInterval", 20\)/);
  assert.match(sourcesPanel, /id="cfgXhsDailySearchBudget"[^>]*placeholder="默认 20"/);
  assert.match(sourcesPanel, /id="cfgXhsTaskInterval"[^>]*placeholder="1200"/);
  assert.match(sourcesPanel, /id="cfgXhsMinInterval"[^>]*placeholder="20"/);
  assert.match(sourcesPanel, /id="cfgWeiboEnabled" type="checkbox"/);
  assert.match(sourcesPanel, /无需用户 Cookie/);
  assert.match(sourcesPanel, /guided init/);
  assert.match(sourcesPanel, /并非官方稳定 API/);
  assert.doesNotMatch(sourcesPanel, /id="cfgWeiboCookie"/);
  assert.match(popupJs, /weiboEnabled\.checked = cfg\.sources\?\.weibo\?\.enabled === true/);
  assert.match(popupJs, /weibo:\s*\{\s*enabled: checked\("cfgWeiboEnabled"\)/);
  assert.match(popupJs, /setWeiboSourceModes\(cfg\.sources\?\.weibo\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectWeiboSourceModes\(\)/);
  assert.match(sourcesPanel, /作者（需同时启用搜索或热榜）/);
  assert.match(popupJs, /daily_search_budget: getInt\("cfgWeiboDailySearchBudget", 60\)/);
  assert.match(popupJs, /daily_hot_budget: getInt\("cfgWeiboDailyHotBudget", 10\)/);
  assert.match(popupJs, /daily_creator_budget: getInt\("cfgWeiboDailyCreatorBudget", 30\)/);
  assert.match(popupJs, /request_interval_seconds: getInt\("cfgWeiboRequestInterval", 3\)/);
  assert.match(popupJs, /min_interval_minutes: getInt\("cfgWeiboMinInterval", 10\)/);
});

test("Weibo init bridge is read-only and never syncs cookies or native writes", () => {
  const manifest = JSON.parse(readFileSync(resolve("manifest.json"), "utf8"));
  const manifestText = JSON.stringify(manifest);
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(manifestText, /weibo\.com/);
  assert.match(manifestText, /weibo\.cn/);
  assert.doesNotMatch(popupJs, /weibo_cookie_synced|cfgWeiboCookie|syncWeibo/i);
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
  const panelNames = ["models", "sources", "scheduler", "advanced", "general", "logging"];

  assert.match(tabsMarkup, /role="tablist"/);
  for (const [id, label] of [
    ["settingsTabModels", "模型"],
    ["settingsTabSources", "平台源"],
    ["settingsTabScheduler", "调度"],
    ["settingsTabAdvanced", "高级功能"],
    ["settingsTabGeneral", "通用"],
    ["settingsTabLogging", "日志"],
  ]) {
    assert.match(tabsMarkup, new RegExp(`id="${id}"`));
    assert.match(tabsMarkup, new RegExp(`>${label}<`));
    const panelName = id.replace("settingsTab", "");
    assert.match(
      tabsMarkup,
      new RegExp(`id="${id}"[^>]*role="tab"[^>]*aria-selected="(?:true|false)"[^>]*aria-controls="settingsPanel${panelName}"`),
    );
    assert.match(
      popupHtml,
      new RegExp(`id="settingsPanel${panelName}"[^>]*role="tabpanel"[^>]*aria-labelledby="${id}"`),
    );
    assert.match(popupJs, new RegExp(`"${id}"`));
  }
  assert.match(popupJs, /tab\.setAttribute\("aria-selected", isActive \? "true" : "false"\)/);
  assert.match(popupJs, /tab\.tabIndex = isActive \? 0 : -1/);
  assert.match(popupJs, /tab\.addEventListener\("click"/);
  for (const name of panelNames) {
    assert.match(popupHtml, new RegExp(`data-settings-panel="${name}"`));
    assert.match(popupJs, new RegExp(`"${name}"`));
  }
});

test("settings page exposes backend-only update controls and plugin release fallback", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /版本与更新/);
  assert.match(popupHtml, /id="cfgAutoUpdate"/);
  assert.match(popupHtml, /自动更新后端/);
  assert.match(popupHtml, /此开关不会更新浏览器插件/);
  assert.match(popupHtml, /id="backendUpdateDownload"/);
  assert.match(popupHtml, /href="https:\/\/github\.com\/whiteguo233\/OpenBiliClaw\/releases"/);
  assert.match(popupJs, /fetchUpdateStatus/);
  assert.match(popupJs, /checkBackendUpdate/);
  assert.match(popupJs, /applyBackendUpdate/);
  assert.match(popupJs, /install_mode/);
  assert.match(popupJs, /backendUpdateDownload/);
  assert.match(popupJs, /releases\/tag/);
  assert.doesNotMatch(popupJs, /extension_auto_apply|extension_update_available/);
});

test("settings backend update apply failures show backend reason and refresh status", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /dirty_worktree:\s*"代码目录有未提交改动，更新被阻止"/);
  assert.match(popupJs, /untrusted_remote:\s*"git 远端不在允许列表，更新被阻止（可在后端日志查看实际远端地址）"/);
  assert.match(popupJs, /docker_install_mode:\s*"Docker 安装通过拉取新镜像升级，无法就地自更新"/);
  assert.match(popupJs, /branch_not_fast_forwardable:\s*"本地代码与发布版本分叉，无法快进更新"/);
  assert.match(popupJs, /missing_target_tag:\s*"远端未找到目标版本标签"/);

  assert.match(popupJs, /const details = error\?\.details/);
  assert.match(popupJs, /renderBackendUpdateStatus\(details\)/);
  assert.match(popupJs, /后端更新未能开始：/);
  assert.match(popupJs, /await loadBackendUpdateStatus\(\)/);
});

test("settings backend update actions require explicit install branch", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /const isGitInstall = installMode === "git"/);
  assert.match(popupJs, /const isFrozenInstall = installMode === "frozen"/);
  assert.match(popupJs, /const isDockerInstall = installMode === "docker"/);
  assert.match(popupJs, /docker compose pull && docker compose up -d/);
  assert.match(
    popupJs,
    /const isDesktopInstallerUpdate = String\(backend\.latest_tag \|\| ""\)\.startsWith\("desktop-v"\)/,
  );
  assert.match(popupJs, /isGitInstall &&\s*backend\.state === "update_available"/);
  assert.match(
    popupJs,
    /\(isFrozenInstall \|\| isDesktopInstallerUpdate\) && backend\.state === "update_available"/,
  );
  assert.doesNotMatch(popupJs, /!unsupportedInstall && backend\.state === "update_available"/);
});

test("settings disables backend auto-apply controls for every non-git install", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(
    popupJs,
    /const autoApplyUnsupported = \["frozen", "docker", "unsupported"\]\.includes\(installMode\)/,
  );
  assert.match(popupJs, /autoUpdateToggle\.disabled = autoApplyUnsupported/);
  assert.match(popupJs, /autoUpdateInterval\.disabled = autoApplyUnsupported/);
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
  // Empty-field fallbacks must mirror the backend dataclass defaults
  // (budgets: 0 = uncapped) so popup and web settings write the same values.
  assert.match(popupJs, /daily_search_budget: getInt\("cfgYoutubeDailySearchBudget", 0\)/);
  assert.match(popupJs, /daily_trending_budget: getInt\("cfgYoutubeDailyTrendingBudget", 0\)/);
  assert.match(popupJs, /daily_channel_budget: getInt\("cfgYoutubeDailyChannelBudget", 0\)/);
  assert.match(popupJs, /request_interval_seconds: getInt\("cfgYoutubeRequestInterval", 2\)/);
  assert.match(popupJs, /min_interval_minutes: getInt\("cfgYoutubeMinInterval", 3\)/);

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

test("settings page round-trips Zhihu discovery source modes", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgZhihuModeSearch",
    "cfgZhihuModeHot",
    "cfgZhihuModeFeed",
    "cfgZhihuModeCreator",
    "cfgZhihuModeRelated",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /setZhihuSourceModes\(cfg\.sources\?\.zhihu\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectZhihuSourceModes\(\)/);
});

test("settings page round-trips Reddit discovery config", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgRedditEnabled",
    "cfgRedditBackend",
    "cfgRedditCookie",
    "cfgRedditModeSearch",
    "cfgRedditModeHot",
    "cfgRedditModeSubreddit",
    "cfgRedditModeRelated",
    "cfgRedditDailySearchBudget",
    "cfgRedditDailyHotBudget",
    "cfgRedditDailySubredditBudget",
    "cfgRedditDailyRelatedBudget",
    "cfgRedditRequestInterval",
    "cfgRedditMinInterval",
    "cfgPoolShareReddit",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /setRedditSourceModes\(cfg\.sources\?\.reddit\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectRedditSourceModes\(\)/);
  assert.match(popupJs, /backend: getVal\("cfgRedditBackend"\) \|\| "rdt"/);
  assert.match(popupJs, /daily_search_budget: getInt\("cfgRedditDailySearchBudget", 300\)/);
  assert.match(popupJs, /reddit: getInt\("cfgPoolShareReddit", 1\)/);
  assert.match(popupJs, /reddit: checked\("cfgRedditEnabled"\)/);
  assert.match(popupJs, /if \(shares\.reddit !== undefined\) setVal\("cfgPoolShareReddit", shares\.reddit\)/);
});

test("settings page round-trips Bangumi discovery config", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgBangumiEnabled",
    "cfgBangumiUsername",
    "cfgBangumiModeSearch",
    "cfgBangumiModeRanked",
    "cfgBangumiModeLatest",
    "cfgBangumiTypeAnime",
    "cfgBangumiTypeBook",
    "cfgBangumiTypeGame",
    "cfgBangumiTypeMusic",
    "cfgBangumiTypeReal",
    "cfgBangumiDailySearchBudget",
    "cfgBangumiDailyRankedBudget",
    "cfgBangumiDailyLatestBudget",
    "cfgBangumiRequestInterval",
    "cfgBangumiMinInterval",
    "cfgBangumiBootstrapLimit",
    "cfgPoolShareBangumi",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(popupJs, /cfg\.sources\?\.bangumi\?\.source_modes/);
  assert.match(popupJs, /cfg\.sources\?\.bangumi\?\.subject_types/);
  assert.match(popupJs, /username: getVal\("cfgBangumiUsername"\)/);
  assert.match(popupJs, /daily_search_budget: getInt\("cfgBangumiDailySearchBudget", 300\)/);
  assert.match(popupJs, /bangumi: getInt\("cfgPoolShareBangumi", 1\)/);
  assert.match(popupJs, /bangumi: checked\("cfgBangumiEnabled"\)/);
  assert.match(popupJs, /if \(shares\.bangumi !== undefined\) setVal\("cfgPoolShareBangumi", shares\.bangumi\)/);
});

test("settings page round-trips Linux.do config without a cookie field", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgLinuxdoEnabled",
    "cfgLinuxdoModeSearch",
    "cfgLinuxdoModeHot",
    "cfgLinuxdoModeFeed",
    "cfgLinuxdoModeCreator",
    "cfgLinuxdoModeRelated",
    "cfgLinuxdoDailySearchBudget",
    "cfgLinuxdoDailyHotBudget",
    "cfgLinuxdoDailyFeedBudget",
    "cfgLinuxdoDailyCreatorBudget",
    "cfgLinuxdoDailyRelatedBudget",
    "cfgLinuxdoRequestInterval",
    "cfgLinuxdoMinInterval",
    "cfgLinuxdoBootstrapLimit",
    "cfgPoolShareLinuxdo",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  const card = popupHtml.match(/data-source-card="linuxdo"[\s\S]*?data-source-card="pool"/)?.[0] ?? "";
  assert.doesNotMatch(card, /id="cfgLinuxdoCookie"|<textarea/);
  assert.match(card, /公开发现无需登录/);
  assert.match(card, /浏览器插件后，可增强收藏、点赞和阅读记录/);
  assert.match(popupJs, /setCheckedValues\(LINUXDO_SOURCE_MODE_FIELDS, cfg\.sources\?\.linuxdo\?\.source_modes\)/);
  assert.match(popupJs, /source_modes: collectCheckedValues\(LINUXDO_SOURCE_MODE_FIELDS, \["search"\]\)/);
  assert.match(popupJs, /daily_search_budget: getInt\("cfgLinuxdoDailySearchBudget", 0\)/);
  assert.match(popupJs, /bootstrap_limit: getInt\("cfgLinuxdoBootstrapLimit", 300\)/);
  assert.match(popupJs, /linuxdo: getInt\("cfgPoolShareLinuxdo", 1\)/);
  assert.match(popupJs, /linuxdo: checked\("cfgLinuxdoEnabled"\)/);
  assert.match(popupJs, /if \(shares\.linuxdo !== undefined\) setVal\("cfgPoolShareLinuxdo", shares\.linuxdo\)/);
});

test("settings page exposes Bangumi clear-token control and rejected status", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // C: an explicit "clear token" checkbox that sends access_token:"".
  assert.match(popupHtml, /id="cfgBangumiClearToken"/);
  assert.match(popupJs, /checked\("cfgBangumiClearToken"\)/);
  assert.match(popupJs, /access_token: ""/);
  // A: a rejected personal token renders an actionable warning + red dot.
  // The rule itself moved into the shared module, which the side panel, the
  // desktop page and the setup wizard all load — this panel and the desktop
  // page each keeping their own copy is exactly how the two status tables
  // drifted apart (spec D6). Assert it where it now lives, and assert that this
  // surface renders through the module rather than re-deriving a verdict.
  const sharedJs = readFileSync(
    resolve("..", "src", "openbiliclaw", "web", "shared", "source-status.js"),
    "utf8",
  );
  assert.match(sharedJs, /token_state\) === "rejected"/);
  assert.match(sharedJs, /令牌已失效/);
  assert.match(popupJs, /SourceStatus\.describeAccess\(/);
  // B: config-save maps the live-validation error codes to friendly text.
  assert.match(popupJs, /invalid_bangumi_access_token/);
  assert.match(popupJs, /bangumi_token_check_failed/);
});

test("settings page round-trips multimodal discovery evaluation controls", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  for (const id of [
    "cfgCandidateEvalConcurrency",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(popupJs, new RegExp(`"${id}"`), `${id} should be wired in popup.js`);
  }

  assert.match(
    popupHtml,
    /id="cfgCandidateEvalConcurrency" type="number" min="1" max="3" step="1" placeholder="3"/,
  );

  assert.match(
    popupJs,
    /setVal\("cfgCandidateEvalConcurrency", cfg\.discovery\?\.candidate_eval_concurrency \?\? 3\)/,
  );
  assert.match(
    popupJs,
    /multimodalEvaluation\.checked = cfg\.discovery\?\.multimodal_evaluation_enabled === true/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalBatchSize", cfg\.discovery\?\.multimodal_batch_size \?\? 8\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageMaxPx", cfg\.discovery\?\.multimodal_image_max_px \?\? 384\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageQuality", cfg\.discovery\?\.multimodal_image_quality \?\? 72\)/,
  );
  assert.match(
    popupJs,
    /setVal\("cfgMultimodalImageTimeout", cfg\.discovery\?\.multimodal_image_timeout_seconds \?\? 6\)/,
  );
  assert.match(popupJs, /multimodal_evaluation_enabled: checked\("cfgMultimodalEvaluationEnabled"\)/);
  assert.match(
    popupJs,
    /candidate_eval_concurrency: getInt\("cfgCandidateEvalConcurrency", 3\)/,
  );
  assert.match(popupJs, /setVal\("cfgLlmConcurrencyV2", cfg\.llm\?\.concurrency \?\? 3\)/);
  assert.match(popupJs, /concurrency: getInt\("cfgLlmConcurrencyV2", 3\)/);
  assert.match(popupJs, /multimodal_batch_size: getInt\("cfgMultimodalBatchSize", 8\)/);
  assert.match(popupJs, /multimodal_image_max_px: getInt\("cfgMultimodalImageMaxPx", 384\)/);
  assert.match(popupJs, /multimodal_image_quality: getInt\("cfgMultimodalImageQuality", 72\)/);
  assert.match(
    popupJs,
    /multimodal_image_timeout_seconds: getInt\("cfgMultimodalImageTimeout", 6\)/,
  );
});

test("advanced settings keep recommendation signals together and preserve disabled values", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const advancedPanel =
    popupHtml.match(/<div id="settingsPanelAdvanced"[\s\S]*?<div id="settingsPanelLogging"/)?.[0] ?? "";
  const schedulerPanel =
    popupHtml.match(/<div id="settingsPanelScheduler"[\s\S]*?<div id="settingsPanelAdvanced"/)?.[0] ?? "";
  const modelsPanel =
    popupHtml.match(/<div id="settingsPanelModels"[\s\S]*?<div id="settingsPanelSources"/)?.[0] ?? "";

  assert.equal((advancedPanel.match(/<div class="settings-section">/g) ?? []).length, 5);
  for (const id of [
    "cfgEvalScorer",
    "cfgVisualProfileEnabled",
    "cfgKeyframeEnabled",
    "cfgKeyframeMaxFrames",
    "cfgKeyframeFetchLimit",
    "cfgDanmakuEnabled",
    "cfgDanmakuFetchLimit",
    "cfgDanmakuMaxChars",
    "cfgEmbeddingMultimodalEnabled",
    "cfgCandidateEvalConcurrency",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
    "cfgKeywordGenerationMode",
  ]) {
    assert.equal((advancedPanel.match(new RegExp(`id="${id}"`, "g")) ?? []).length, 1, id);
    assert.equal((popupHtml.match(new RegExp(`id="${id}"`, "g")) ?? []).length, 1, `${id} duplicate`);
  }
  for (const id of [
    "cfgVisualProfileEnabled",
    "cfgKeyframeEnabled",
    "cfgEmbeddingMultimodalEnabled",
    "cfgMultimodalEvaluationEnabled",
  ]) {
    const control = advancedPanel.match(new RegExp(`id="${id}"[^>]*>`))?.[0] ?? "";
    assert.doesNotMatch(control, /checked/, `${id} must default off`);
  }
  for (const id of [
    "cfgEvalScorer",
    "cfgKeywordGenerationMode",
    "cfgCandidateEvalConcurrency",
    "cfgMultimodalEvaluationEnabled",
    "cfgMultimodalBatchSize",
    "cfgMultimodalImageMaxPx",
    "cfgMultimodalImageQuality",
    "cfgMultimodalImageTimeout",
  ]) {
    assert.doesNotMatch(schedulerPanel, new RegExp(`id="${id}"`), `${id} moved out of scheduler`);
  }
  assert.doesNotMatch(modelsPanel, /id="cfgEmbeddingMultimodalEnabled"/);

  assert.match(
    advancedPanel,
    /<select id="cfgEvalScorer" aria-describedby="cfgEvalScorerHint">[\s\S]*?<option value="llm" selected>Agent（默认）<\/option>[\s\S]*?<option value="shadow">Shadow（校准观察）<\/option>[\s\S]*?<option value="learned">Learned（仅相关性，实验性）<\/option>[\s\S]*?<\/select>/,
  );
  assert.match(advancedPanel, /id="cfgEvalScorerHint"/);
  assert.match(advancedPanel, /人工运行质量门禁并确认通过/);
  assert.match(advancedPanel, /切换不会重算已有推荐/);

  for (const [id, min, max, placeholder] of [
    ["cfgKeyframeMaxFrames", "1", "12", "4"],
    ["cfgKeyframeFetchLimit", "1", "200", "50"],
    ["cfgDanmakuFetchLimit", "1", "200", "50"],
    ["cfgDanmakuMaxChars", "100", "2000", "500"],
  ]) {
    const control = advancedPanel.match(new RegExp(`id="${id}"[^>]*>`))?.[0] ?? "";
    assert.match(control, new RegExp(`min="${min}"`));
    assert.match(control, new RegExp(`max="${max}"`));
    assert.match(control, new RegExp(`placeholder="${placeholder}"`));
  }

  for (const field of [
    'setVal("cfgEvalScorer", cfg.discovery?.eval_scorer || "llm")',
    'visualProfile.checked = cfg.discovery?.visual_profile_enabled === true',
    'keyframe.checked = cfg.discovery?.keyframe_enabled === true',
    'setVal("cfgKeyframeMaxFrames", cfg.discovery?.keyframe_max_frames ?? 4)',
    'setVal("cfgKeyframeFetchLimit", cfg.discovery?.keyframe_fetch_limit ?? 50)',
    'danmaku.checked = cfg.discovery?.danmaku_enabled === true',
    'setVal("cfgDanmakuFetchLimit", cfg.discovery?.danmaku_fetch_limit ?? 50)',
    'setVal("cfgDanmakuMaxChars", cfg.discovery?.danmaku_max_chars ?? 500)',
  ]) {
    assert.ok(popupJs.includes(field), field);
  }
  const spread = "...(state.runtimeConfig?.discovery || {})";
  assert.ok(popupJs.indexOf(spread) < popupJs.indexOf("visual_profile_enabled:"));
  for (const field of [
    'eval_scorer: getVal("cfgEvalScorer") || "llm"',
    'visual_profile_enabled: checked("cfgVisualProfileEnabled")',
    'keyframe_enabled: checked("cfgKeyframeEnabled")',
    'keyframe_max_frames: getInt("cfgKeyframeMaxFrames", 4)',
    'keyframe_fetch_limit: getInt("cfgKeyframeFetchLimit", 50)',
    'danmaku_enabled: checked("cfgDanmakuEnabled")',
    'danmaku_fetch_limit: getInt("cfgDanmakuFetchLimit", 50)',
    'danmaku_max_chars: getInt("cfgDanmakuMaxChars", 500)',
  ]) {
    assert.ok(popupJs.includes(field), field);
    assert.ok(popupJs.indexOf(spread) < popupJs.indexOf(field), `${field} must follow spread`);
  }
});

test("settings page round-trips embedding multimodal cover toggle + dashscope provider", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // DashScope must be selectable as an embedding provider (not TOML-only).
  assert.match(
    popupHtml,
    /<select id="cfgEmbeddingProvider">[\s\S]*<option value="dashscope"/,
    "dashscope should be an embedding provider option",
  );
  // The image-only cover embedding toggle must exist and be wired both ways.
  assert.match(popupHtml, /id="cfgEmbeddingMultimodalEnabled" type="checkbox"/);
  assert.match(
    popupJs,
    /embMultimodal\.checked = cfg\.llm\?\.embedding\?\.multimodal_enabled === true/,
  );
  assert.match(
    popupJs,
    /multimodal_enabled: checked\("cfgEmbeddingMultimodalEnabled"\)/,
  );
});

test("settings page round-trips douyin and x cookies like the bilibili card", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Plaintext cookie textareas, same shape as the bilibili card.
  assert.match(popupHtml, /<textarea id="cfgBiliCookie"/);
  assert.match(popupHtml, /<textarea id="cfgDouyinCookie"/);
  assert.match(popupHtml, /<textarea id="cfgTwitterCookie"/);
  assert.match(popupHtml, /<textarea id="cfgRedditCookie"/);

  assert.match(popupJs, /setVal\("cfgDouyinCookie", cfg\.sources\?\.douyin\?\.cookie\)/);
  assert.match(popupJs, /setVal\("cfgTwitterCookie", cfg\.sources\?\.twitter\?\.cookie\)/);

  // An empty textarea is omitted from the payload so saving the form can
  // never wipe a synced cookie (bilibili included).
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgBiliCookie"\) \? \{ cookie: getVal\("cfgBiliCookie"\) \} : \{\}\)/,
  );
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgDouyinCookie"\) \? \{ cookie: getVal\("cfgDouyinCookie"\) \} : \{\}\)/,
  );
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgTwitterCookie"\) \? \{ cookie: getVal\("cfgTwitterCookie"\) \} : \{\}\)/,
  );
  // Reddit has no config-side cookie echo (GET /api/config carries no
  // sources.reddit.cookie) — paste-only, routed to rdt-cli's store.
  assert.match(
    popupJs,
    /\.\.\.\(getVal\("cfgRedditCookie"\) \? \{ cookie: getVal\("cfgRedditCookie"\) \} : \{\}\)/,
  );
});

test("settings page keeps the legacy OpenAI editor read-only under instance routing", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const collectFormSource = popupJs.slice(
    popupJs.indexOf("function collectForm()"),
    popupJs.indexOf("function showToast(", popupJs.indexOf("function collectForm()")),
  );

  assert.match(popupHtml, /id="cfgOpenaiAuthMode"/);
  assert.match(popupHtml, /<option value="api_key">API Key<\/option>/);
  assert.match(popupHtml, /<option value="codex_oauth">Codex OAuth<\/option>/);
  assert.match(
    popupJs,
    /setVal\("cfgOpenaiAuthMode", cfg\.llm\?\.openai\?\.auth_mode \|\| "api_key"\)/,
  );
  assert.doesNotMatch(collectFormSource, /cfgOpenaiAuthMode|auth_mode:/);
});

test("settings page edits native LLM instances and round-trips every route", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const collectFormSource = popupJs.slice(
    popupJs.indexOf("function collectForm()"),
    popupJs.indexOf("function showToast(", popupJs.indexOf("function collectForm()")),
  );

  assert.match(popupHtml, /id="cfgLlmRoutingSummary"/);
  assert.match(popupHtml, /id="cfgAddLlmInstance"/);
  assert.match(popupHtml, /id="cfgLlmInstanceList"/);
  assert.match(popupHtml, /id="cfgLlmDefaultChain"/);
  assert.match(popupHtml, /id="cfgLlmDefaultChainPicker"/);
  assert.match(popupHtml, /id="cfgLlmInstanceDialog"/);
  assert.match(popupHtml, /id="cfgLlmInstanceApiKey" type="password"/);
  assert.match(popupHtml, /id="cfgLlmInstanceModel" list="cfgLlmInstanceModelOptions"/);
  assert.match(popupHtml, /id="cfgRefreshLlmInstanceModels"/);
  assert.match(popupHtml, /id="cfgLlmInstanceReasoning" list="cfgLlmInstanceReasoningOptions"/);
  assert.match(popupHtml, /id="cfgOpenDesktopModels"/);
  assert.match(popupHtml, /id="cfgProbeLlmChain"/);
  assert.match(popupHtml, /id="cfgEmbeddingFallbackProvider"/);
  assert.doesNotMatch(popupHtml, /id="cfgEmbeddingFallbackEnabled"/);
  assert.match(popupJs, /function normalizeLlmDraft\(llm\)/);
  assert.match(popupJs, /function renderLlmRoutingSummary\(llm = null\)/);
  assert.match(popupJs, /Array\.isArray\(llm\?\.default_chain\)/);
  assert.match(popupJs, /const rawRoute = llm\?\.routes\?\.\[moduleName\]/);
  assert.match(popupJs, /function saveLlmInstanceDraft\(\)/);
  assert.match(popupJs, /function discoverLlmInstanceModels\(\)/);
  assert.match(popupJs, /discoverConfigModels\(request\.config, request\.instanceId\)/);
  assert.match(popupJs, /当前输入未改动，仍可手填/);
  assert.match(popupJs, /function deleteLlmInstance\(instanceId\)/);
  assert.match(popupJs, /openMobileWebUrl\(`\$\{origin\}\/web\?settings=models`\)/);
  assert.doesNotMatch(
    collectFormSource,
    /cfgLlmFallbackProvider|llmFallbackProvider|default_provider:|fallback_provider: llmFallbackProvider/,
  );
  assert.match(collectFormSource, /routing_version: 2/);
  assert.match(collectFormSource, /instances: clonePlain\(llmDraft\.instances\)/);
  assert.match(collectFormSource, /default_chain: \[\.\.\.llmDraft\.default_chain\]/);
  assert.match(collectFormSource, /routes: Object\.fromEntries\(/);
  assert.match(collectFormSource, /chain: route\.inherit !== false \? \[\] : \[\.\.\.route\.chain\]/);
  assert.match(
    popupJs,
    /setVal\("cfgEmbeddingFallbackProvider", cfg\.llm\?\.embedding\?\.fallback_provider\)/,
  );
  assert.match(
    popupJs,
    /const embeddingFallbackProvider = getVal\("cfgEmbeddingFallbackProvider"\)/,
  );
  assert.match(
    popupJs,
    /fallback_provider: embeddingFallbackProvider/,
  );
});

test("settings page exposes and wires routed LLM and embedding probe buttons", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupHtml, /id="cfgProbeLlmChain"/);
  assert.match(popupHtml, /id="cfgProbeEmbedding"/);
  assert.match(popupHtml, /id="cfgProbeLlmChainStatus"/);
  assert.match(popupHtml, /id="cfgProbeEmbeddingStatus"/);
  assert.match(popupJs, /probeConfigService\("llm_instance", collectForm\(\), instanceId\)/);
  assert.match(popupJs, /probeConfigService\("llm_chain", collectForm\(\)\)/);
  assert.match(popupJs, /probeConfigService\("embedding", collectForm\(\)\)/);
  assert.match(popupJs, /function renderProbeResult/);
});

test("settings general tab exposes and wires the network proxy field (aligned with desktop web)", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Field + probe control + copy stating CN requests stay direct.
  assert.match(popupHtml, /id="cfgNetworkProxyMode"/);
  assert.match(popupHtml, /id="cfgNetworkProxy"/);
  assert.match(popupHtml, /id="cfgProbeNetworkProxy"/);
  assert.match(popupHtml, /id="cfgProbeNetworkProxyStatus"/);
  assert.match(popupHtml, /海外/);
  assert.match(popupHtml, /国内请求始终直连/);

  // Restore mode + proxy, collect both into payload.network, probe wired.
  // The fallback literal must track the backend [network].mode default
  // (system since v0.3.175), else an omitted field renders the wrong mode.
  assert.match(popupJs, /setVal\("cfgNetworkProxyMode", cfg\.network\?\.mode \|\| "system"\)/);
  assert.match(popupJs, /setVal\("cfgNetworkProxy", cfg\.network\?\.proxy \|\| ""\)/);
  assert.match(popupJs, /network:\s*\{\s*mode: getVal\("cfgNetworkProxyMode"\),\s*proxy: getVal\("cfgNetworkProxy"\),/);
  assert.match(popupJs, /probeConfigService\("network_proxy", \{ network: \{ mode, proxy \} \}\)/);
  assert.match(popupJs, /function runNetworkProxyConfigProbe/);
});

test("settings page renders editable instance cards, ordered default chain, and module summary", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /function renderLlmInstances\(\)/);
  assert.match(popupJs, /button\.dataset\.llmInstanceAction = action/);
  assert.match(popupJs, /function renderLlmDefaultChain\(\)/);
  assert.match(popupJs, /createLlmChainAction\("up"/);
  assert.match(popupJs, /createLlmChainAction\("down"/);
  assert.match(popupJs, /createLlmChainAction\("remove"/);
  assert.match(popupJs, /function renderLlmModuleSummary\(\)/);
  assert.match(popupJs, /detail\.textContent = "继承默认调用链"/);
  assert.match(popupJs, /chainNames\.join\(" → "\)/);
  assert.match(popupJs, /if \(enabled && !state\.llmDraft\.default_chain\.length\)/);
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
  assert.match(saveBlock, /finally[\s\S]*settingsSaveInFlight = false/);
  assert.match(saveBlock, /finally[\s\S]*setSaveButtonMode/);
  assert.match(saveBlock, /finally[\s\S]*renderSettingsDirty/);
});

test("settings save understands queued config apply and background failure events", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const saveBlock =
    popupJs.match(/saveBtn\.addEventListener\("click"[\s\S]*?\n  \}\);/)?.[0] ?? "";

  assert.match(saveBlock, /result\.apply_state === "queued"/);
  assert.match(saveBlock, /queued[\s\S]*"warning"/);
  assert.match(popupJs, /event\.type === "config_reload_failed"/);
  assert.match(popupJs, /后台应用配置失败，已恢复上一次生效配置/);
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
  assert.match(popupJs, /保存并恢复/);
  assert.match(popupJs, /保存有效配置后会原地恢复，无需重启/);
  assert.doesNotMatch(popupJs, /保存修复后需要重启后端/);
});

test("settings page shows the budget-semantics hint for every per-source budget group", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  // The hint must match the desktop web wording so users learn budget is a
  // per-day cap, not an on/off toggle.
  const baseNote =
    "预算 = 每日任务次数上限，不是开关；填 1 表示每天只允许 1 次。0 或留空 = 不限。";
  const redditNote =
    "预算 = 每日任务次数上限，不是开关；填 1 表示每天只允许 1 次。0 或留空 = 不限（Reddit 各分支默认 300）。";
  const xhsNote =
    "预算 = 每日任务次数上限；搜索默认每天 20 次，显式填 0 = 不限；创作者预算 0 或留空 = 不限。";

  // Every source card that has a daily budget input must carry a note.
  const budgetCards = ["xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit", "linuxdo"];
  for (const card of budgetCards) {
    const start = popupHtml.indexOf(`data-source-card="${card}"`);
    assert.ok(start >= 0, `source card ${card} should exist`);
    const rest = popupHtml.slice(start);
    const end = rest.indexOf("settings-source-card", 1);
    const cardHtml = end >= 0 ? rest.slice(0, end) : rest;
    assert.match(
      cardHtml,
      /class="settings-hint" data-budget-note>预算 = 每日任务次数上限/,
      `${card} card should carry the budget-semantics hint`,
    );
  }

  // Reddit keeps its 300-default clarification.
  assert.ok(popupHtml.includes(redditNote), "reddit note should mention the 300 default");
  // XHS has a conservative search default while the other four use base wording.
  assert.ok(popupHtml.includes(xhsNote), "xhs note should mention the 20/day default");
  const baseCount = popupHtml.split(baseNote).length - 1;
  assert.ok(baseCount >= 4, `expected >=4 base budget notes, got ${baseCount}`);
});

test("settings page wires the keyword generation mode selector (matches desktop web)", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // Select + the three options — values/labels byte-identical to desktop web.
  assert.match(popupHtml, /id="cfgKeywordGenerationMode"/);
  assert.match(popupHtml, /<option value="legacy">经典<\/option>/);
  assert.match(popupHtml, /<option value="hybrid" selected>混合<\/option>/);
  assert.match(popupHtml, /<option value="inspiration">灵感<\/option>/);
  // Cost hint conveys 混合最贵.
  assert.match(popupHtml, /混合最贵/);

  // Load fills the select from the derived discovery field.
  assert.match(
    popupJs,
    /setVal\("cfgKeywordGenerationMode", cfg\.discovery\?\.keyword_generation_mode \|\| "hybrid"\)/,
  );

  // Save collects it into the discovery payload AFTER the snapshot spread, so a
  // loaded value never clobbers the user's live selection (R2 spread-order).
  const saveKey = 'keyword_generation_mode: getVal("cfgKeywordGenerationMode")';
  assert.ok(popupJs.includes(saveKey), "save key should be present");
  const spread = "...(state.runtimeConfig?.discovery || {})";
  assert.ok(
    popupJs.indexOf(spread) !== -1 && popupJs.indexOf(spread) < popupJs.indexOf(saveKey),
    "keyword_generation_mode must be written after the discovery spread",
  );
});

test("settings source status labels distinguish local readiness", () => {
  // The labels moved into the shared module (src/openbiliclaw/web/shared/
  // source-status.js) that the desktop page and the setup wizard also load.
  // They used to be pinned here as popup.js source text, which is precisely how
  // the two surfaces drifted apart unnoticed: this file could stay green while
  // the desktop page's copy said something else (spec D6).
  const shared = readFileSync(
    resolve("..", "src/openbiliclaw/web/shared/source-status.js"),
    "utf8",
  );

  assert.match(shared, /ready: { tone: "ready", label: "凭据已就绪" }/);
  assert.match(shared, /unverified: { tone: "pending", label: "状态待验证" }/);
  assert.match(shared, /login_required: { tone: "warning", label: "需要登录" }/);
  assert.match(shared, /error: { tone: "danger", label: "检查失败" }/);
});

test("the side panel keeps no second copy of the source status table", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // A local re-declaration is the regression this whole refactor removes: the
  // panel painted no_auth and unverified the same grey while the desktop page
  // told them apart, and nothing failed.
  assert.doesNotMatch(popupJs, /const SOURCE_STATUS_DOT\s*=/);
  assert.doesNotMatch(popupJs, /const SOURCE_STATUS_LABEL\s*=/);
  assert.doesNotMatch(popupJs, /const VERIFY_OUTCOME_TONE\s*=/);
  assert.match(popupJs, /globalThis\.OpenBiliClawSourceStatus/);
});

test("settings save only enables for dirty state and stays locked while saving", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(
    popupHtml,
    /id="settingsSave" class="settings-save" type="button" disabled>保存配置<\/button>/,
  );
  assert.match(popupJs, /let settingsSaveInFlight = false;/);
  assert.match(popupJs, /saveBtn\.disabled = settingsSaveInFlight \|\| count === 0;/);
  assert.match(popupJs, /if \(settingsSaveInFlight \|\| settingsDirtyFields\.size === 0\)/);
  assert.match(popupJs, /settingsSaveInFlight = true;/);
  assert.match(popupJs, /settingsSaveInFlight = false;/);
  assert.doesNotMatch(popupJs, /saveBtn\.disabled = false;/);
  assert.match(popupJs, /if \(result\.config\)[\s\S]*?else \{\s*clearSettingsDirty\(\);/);
  // Programmatic draft mutations do not emit input/change events themselves.
  assert.ok((popupJs.match(/markSettingsDirty\(\);/g) ?? []).length >= 4);
  assert.match(popupJs, /markSettingsDirty\(suggestBtn\);/);
});

test("settings save bar stays pinned above scrolling content", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const overlayCss = popupHtml.match(/\.settings-overlay \{[\s\S]*?\n    \}/)?.[0] ?? "";
  const savebarCss = popupHtml.match(/\.settings-savebar \{[\s\S]*?\n    \}/)?.[0] ?? "";

  assert.match(
    overlayCss,
    /padding: 16px 16px calc\(88px \+ env\(safe-area-inset-bottom, 0px\)\);/,
  );
  assert.match(savebarCss, /position: fixed;/);
  assert.match(savebarCss, /left: 16px;/);
  assert.match(savebarCss, /right: 16px;/);
  assert.match(savebarCss, /bottom: 0;/);
  assert.match(savebarCss, /box-shadow: 0 -12px 28px/);
  assert.doesNotMatch(savebarCss, /position: sticky;/);
});
