import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("popup header keeps compact status inline with brand row", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const heroTopBlock = popupHtml.match(/\.hero-top\s*\{[^}]+\}/)?.[0] ?? "";
  const statusBadgeBlock = popupHtml.match(/\.status-badge\s*\{[^}]+\}/)?.[0] ?? "";
  const popupMarkup = popupHtml.match(/<header class="hero">[\s\S]*?<\/header>/)?.[0] ?? "";

  assert.match(heroTopBlock, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto;/);
  assert.match(statusBadgeBlock, /padding:\s*6px\s+10px;/);
  assert.doesNotMatch(popupMarkup, /id="statusText"/);
});

test("popup header exposes a local mobile web QR entry", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const popupMarkup = popupHtml.match(/<header class="hero">[\s\S]*?<\/header>/)?.[0] ?? "";
  const overlayMarkup =
    popupHtml.match(/<div id="mobileQrOverlay"[\s\S]*?<!-- ── Messages overlay ── -->/)?.[0] ?? "";

  assert.match(popupMarkup, /id="mobileQrButton"/);
  assert.match(popupMarkup, /aria-label="显示移动端二维码"/);
  assert.match(popupMarkup, /id="mobileQrButton"[\s\S]*id="messagesButton"[\s\S]*id="settingsGear"/);
  assert.match(overlayMarkup, /id="mobileQrCode"/);
  assert.match(overlayMarkup, /id="mobileQrCopy"/);
  assert.match(overlayMarkup, /id="mobileQrOpen"/);
  assert.match(popupJs, /createQrSvgMarkup/);
  assert.doesNotMatch(popupHtml, /api\.qrserver|chart\.googleapis/);
});

test("recommendation header uses a compact top row with status chips", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const headerCardBlock =
    popupHtml.match(/\.recommendation-header-card\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const topBlock = popupHtml.match(/\.recommendation-header-top\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const introBlock =
    popupHtml.match(/\.recommendation-header-intro\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const titleBlock =
    popupHtml.match(/\.recommendation-header-title\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const summaryRowBlock =
    popupHtml.match(/\.recommendation-summary-row\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const statusRowBlock =
    popupHtml.match(/\.recommendation-status-row\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const statusChipBlock =
    popupHtml.match(/\.recommendation-status-chip\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const recommendMarkup =
    popupHtml.match(/<section id="viewRecommend"[\s\S]*?<div id="emptyState"/)?.[0] ?? "";

  assert.match(headerCardBlock, /border-radius:\s*20px;/);
  assert.match(headerCardBlock, /padding:\s*12px;/);
  assert.match(topBlock, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto;/);
  assert.match(introBlock, /display:\s*flex;/);
  assert.match(introBlock, /flex-direction:\s*column;/);
  assert.match(titleBlock, /align-items:\s*center;/);
  assert.match(summaryRowBlock, /display:\s*flex;/);
  assert.match(statusRowBlock, /display:\s*grid;/);
  assert.match(statusRowBlock, /grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(statusChipBlock, /border-radius:\s*16px;/);
  assert.match(statusChipBlock, /align-items:\s*flex-start;/);
  assert.match(recommendMarkup, /class="recommendation-header-card"/);
  assert.match(recommendMarkup, /class="recommendation-header-top"/);
  assert.match(recommendMarkup, /class="recommendation-header-intro"/);
  assert.match(recommendMarkup, /class="recommendation-header-title"/);
  assert.match(recommendMarkup, /class="recommendation-summary-row"/);
  assert.match(recommendMarkup, /class="recommendation-status-row"/);
  assert.match(recommendMarkup, /id="poolAvailable"/);
  assert.match(recommendMarkup, /id="poolReplenished"/);
  assert.match(recommendMarkup, /id="poolTopics"/);
  assert.doesNotMatch(recommendMarkup, /class="recommendation-status-grid"/);
  assert.doesNotMatch(recommendMarkup, /class="recommendation-header-note"/);
});

test("recommendation header keeps its compact inline layout until very narrow widths", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const narrowHeaderQuery =
    popupHtml.match(/@media \(max-width: 360px\)\s*\{[\s\S]*?\.recommendation-status-chip[\s\S]*?\}/)?.[0] ?? "";
  const mediumHeaderQuery =
    popupHtml.match(/@media \(max-width: 520px\)\s*\{[\s\S]*?\.recommendation-header-top[\s\S]*?\}/)?.[0] ?? "";

  assert.match(narrowHeaderQuery, /\.recommendation-header-top\s*\{/);
  assert.match(narrowHeaderQuery, /\.recommendation-status-chip\s*\{/);
  assert.doesNotMatch(mediumHeaderQuery, /\.recommendation-header-top\s*\{/);
  assert.doesNotMatch(mediumHeaderQuery, /\.recommendation-status-chip\s*\{/);
});

test("recommend tab reserves a dedicated delight slot above the recommendation list", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const delightSlotBlock = popupHtml.match(/#delightSlot[\s\S]*?\{[\s\S]*?\}/)?.[0] ?? "";
  const delightCardBlock = popupHtml.match(/\.delight-card\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const delightActionsBlock = popupHtml.match(/\.delight-actions\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const delightMarkup =
    popupHtml.match(/<section id="viewRecommend"[\s\S]*?<div id="recommendationList" class="recommendation-list"><\/div>/)?.[0] ?? "";

  assert.match(delightSlotBlock, /display:\s*grid;/);
  assert.match(delightCardBlock, /border-radius:\s*20px;/);
  assert.match(delightActionsBlock, /display:\s*flex;/);
  assert.match(delightMarkup, /id="delightSlot"/);
  assert.match(delightMarkup, /id="recommendationList"/);
  assert.match(delightMarkup, /id="delightSlot"[\s\S]*id="emptyState"[\s\S]*id="recommendationList"/);
  assert.match(popupJs, /"看看"/);
  assert.match(popupJs, /"不感兴趣"/);
  assert.match(popupJs, /"聊一聊"/);
  assert.match(popupJs, /"稍后看"/);
});

test("recommendation cards use explicit editorial content sections", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const previewBlock = popupHtml.match(/\.recommendation-preview\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const contentBlock = popupHtml.match(/\.recommendation-content\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const copyBlock = popupHtml.match(/\.recommendation-copy-block\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const metaLineBlock = popupHtml.match(/\.recommendation-meta-line\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const actionsBlock = popupHtml.match(/\.recommendation-actions\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(previewBlock, /display:\s*flex;/);
  assert.match(previewBlock, /flex-direction:\s*column;/);
  assert.match(contentBlock, /justify-content:\s*space-between;/);
  assert.match(copyBlock, /display:\s*flex;/);
  assert.match(copyBlock, /flex-direction:\s*column;/);
  assert.match(metaLineBlock, /font-size:\s*10px;/);
  assert.match(actionsBlock, /justify-content:\s*space-between;/);
  assert.match(popupJs, /copyBlock\.className = "recommendation-copy-block";/);
  assert.match(popupJs, /metaLine\.className = "recommendation-meta-line";/);
  assert.match(popupJs, /content\.append\(top, copyBlock, metaLine\);/);
  assert.doesNotMatch(popupJs, /content\.append\(top,\s*title,\s*expression,\s*meta\);/);
});

test("popup page is structured for side panel browsing", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const htmlBlock = popupHtml.match(/html\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const bodyBlock = popupHtml.match(/body\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const shellBlock = popupHtml.match(/\.shell\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(popupHtml, /class="shell side-panel-shell"/);
  assert.match(htmlBlock, /width:\s*100%;/);
  assert.match(htmlBlock, /height:\s*100%;/);
  assert.match(bodyBlock, /width:\s*100%;/);
  assert.match(bodyBlock, /height:\s*100%;/);
  assert.match(bodyBlock, /display:\s*flex;/);
  assert.match(bodyBlock, /overflow:\s*hidden;/);
  assert.match(shellBlock, /flex:\s*1\s+1\s+auto;/);
  assert.match(shellBlock, /width:\s*100%;/);
  assert.match(shellBlock, /min-width:\s*0;/);
  assert.doesNotMatch(bodyBlock, /width:\s*392px;/);
  assert.doesNotMatch(bodyBlock, /height:\s*560px;/);
});

test("settings tabs use stable compact panels", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const tabsBlock = popupHtml.match(/\.settings-tabs\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const tabBlock = popupHtml.match(/\.settings-tab\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const activeTabBlock = popupHtml.match(/\.settings-tab\.is-active\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const panelBlock = popupHtml.match(/\.settings-panel\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const hiddenPanelBlock =
    popupHtml.match(/\.settings-panel\[hidden\]\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(tabsBlock, /display:\s*grid;/);
  assert.match(tabsBlock, /grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(tabBlock, /min-height:\s*36px;/);
  assert.match(tabBlock, /cursor:\s*pointer;/);
  assert.match(activeTabBlock, /background:/);
  assert.match(panelBlock, /display:\s*flex;/);
  assert.match(panelBlock, /flex-direction:\s*column;/);
  assert.match(hiddenPanelBlock, /display:\s*none;/);
});

test("recommendation card layout reserves a media cover slot", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const previewBlock = popupHtml.match(/\.recommendation-preview\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const coverBlock = popupHtml.match(/\.recommendation-cover\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const coverImageBlock = popupHtml.match(/\.recommendation-cover img\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(previewBlock, /flex-direction:\s*column;/);
  assert.match(coverBlock, /aspect-ratio:\s*16\s*\/\s*9;/);
  assert.match(coverBlock, /width:\s*100%;/);
  assert.match(coverImageBlock, /object-fit:\s*cover;/);
});

test("footer activity card keeps two lines and expandable history area", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const footerBlocks = [...popupHtml.matchAll(/\.footer\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const footerBlock = footerBlocks.find((block) => /margin-top:\s*auto;/.test(block)) ?? "";
  const footerHintBlock = popupHtml.match(/\.footer-hint\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const footerHeadlineBlock = popupHtml.match(/\.footer-headline\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const footerHistoryBlock = popupHtml.match(/\.footer-history\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const footerMarkup = popupHtml.match(/<footer id="footerHintBar"[\s\S]*?<\/footer>/)?.[0] ?? "";
  const successBlock = popupHtml.match(/\.footer\[data-tone="success"\][\s\S]*?\.footer-headline/s)?.[0] ?? "";
  const errorBlock = popupHtml.match(/\.footer\[data-tone="error"\][\s\S]*?\.footer-headline/s)?.[0] ?? "";

  assert.match(footerMarkup, /data-tone="info"/);
  assert.match(footerMarkup, /id="headlineText"/);
  assert.match(footerMarkup, /id="activityToggleButton"/);
  assert.match(footerMarkup, /id="activityHistory"/);
  assert.match(footerBlock, /display:\s*flex;/);
  assert.match(footerHintBlock, /font-weight:\s*700;/);
  assert.match(footerHeadlineBlock, /font-size:\s*11px;/);
  assert.match(footerHistoryBlock, /flex-direction:\s*column;/);
  assert.match(footerHintBlock, /padding-left:\s*22px;/);
  assert.match(successBlock, /background:/);
  assert.match(errorBlock, /background:/);
});

test("profile cognition cards reserve separate rows for context and explicit state", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const cardBlock = popupHtml.match(/\.cognition-card\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const headerBlock = popupHtml.match(/\.cognition-header\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const metaBlock = popupHtml.match(/\.cognition-meta\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const markup = popupHtml.match(/<div id="profileRecentMemory" class="cognition-list"><\/div>[\s\S]*?id="profileRecentMemoryMore"/)?.[0] ?? "";

  assert.match(cardBlock, /border-radius:\s*18px;/);
  assert.match(headerBlock, /gap:\s*8px;/);
  assert.match(metaBlock, /font-size:\s*11px;/);
  assert.match(markup, /id="profileRecentMemory"/);
});

test("profile summary includes an explicit dislike chip group", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const markup = popupHtml.match(/<div id="profileCard"[\s\S]*?<\/div>\s*<\/section>/)?.[0] ?? "";

  assert.match(markup, /<h3>明显会避开<\/h3>/);
  assert.match(markup, /id="profileDislikes"/);
});

test("profile summary reserves dedicated sections for layered cognition", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const markup = popupHtml.match(/<div id="profileCard"[\s\S]*?<\/div>\s*<\/section>/)?.[0] ?? "";

  // Core layer
  assert.match(markup, /id="profileTraits"/);
  assert.match(markup, /id="profileNeeds"/);
  assert.match(markup, /id="profileMBTI"/);
  // Values layer
  assert.match(markup, /id="profileValues"/);
  assert.match(markup, /id="profileMotivationalDrivers"/);
  // Interest layer
  assert.match(markup, /id="profileLikes"/);
  assert.match(markup, /id="profileDislikes"/);
  assert.match(markup, /id="profileFavoriteUps"/);
  // Role layer
  assert.match(markup, /id="profileCurrentPhase"/);
  assert.match(markup, /id="profileLifeStage"/);
  // Surface layer
  assert.match(markup, /id="profileCognitiveStyle"/);
  assert.match(markup, /id="profileStyle"/);
  assert.match(markup, /id="profileContext"/);
  assert.match(markup, /id="profileExplorationOpenness"/);
  // JS references
  assert.match(popupJs, /summary\.cognitive_style/);
  assert.match(popupJs, /summary\.motivational_drivers/);
  assert.match(popupJs, /summary\.current_phase/);
  assert.match(popupJs, /summary\.mbti/);
  assert.match(popupJs, /summary\.likes/);
  assert.match(popupJs, /summary\.style/);
});

test("profile cognition details stay hidden until a card is expanded", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const hiddenDetailsBlock =
    popupHtml.match(/\.cognition-details\[hidden\]\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(hiddenDetailsBlock, /display:\s*none;/);
});
