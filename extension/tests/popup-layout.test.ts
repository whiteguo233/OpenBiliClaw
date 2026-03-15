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

test("recommendation card layout reserves a media cover slot", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const previewBlock = popupHtml.match(/\.recommendation-preview\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const coverBlock = popupHtml.match(/\.recommendation-cover\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const coverImageBlock = popupHtml.match(/\.recommendation-cover img\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(previewBlock, /grid-template-columns:\s*108px\s+minmax\(0,\s*1fr\);/);
  assert.match(coverBlock, /aspect-ratio:\s*16\s*\/\s*10;/);
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

test("profile cognition details stay hidden until a card is expanded", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const hiddenDetailsBlock =
    popupHtml.match(/\.cognition-details\[hidden\]\s*\{[\s\S]*?\}/)?.[0] ?? "";

  assert.match(hiddenDetailsBlock, /display:\s*none;/);
});
