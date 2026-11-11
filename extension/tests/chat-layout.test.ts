import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function cssBlocks(popupHtml: string, selector: string): string[] {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...popupHtml.matchAll(new RegExp(`${escaped}\\s*\\{[\\s\\S]*?\\}`, "g"))].map(
    (match) => match[0],
  );
}

function cssBlockWith(popupHtml: string, selector: string, pattern: RegExp): string {
  return cssBlocks(popupHtml, selector).find((block) => pattern.test(block)) ?? "";
}

test("chat tab layout pins a compact composer below a flexible history pane", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const viewBlock = cssBlockWith(popupHtml, ".view", /flex:\s*1;/);
  const chatShellBlock = cssBlockWith(popupHtml, ".chat-shell", /overflow:\s*hidden;/);
  const chatMessagesBlock = cssBlockWith(popupHtml, ".chat-messages", /overflow-y:\s*auto;/);
  const chatFormBlock = cssBlockWith(popupHtml, ".chat-form", /margin-top:\s*auto;/);

  assert.match(viewBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /flex:\s*1;/);
  assert.match(chatShellBlock, /overflow:\s*hidden;/);
  assert.match(chatMessagesBlock, /flex:\s*1;/);
  assert.match(chatMessagesBlock, /overflow-y:\s*auto;/);
  assert.doesNotMatch(chatMessagesBlock, /max-height:/);
  assert.match(chatFormBlock, /margin-top:\s*auto;/);
  assert.match(chatFormBlock, /flex-shrink:\s*0;/);
  assert.match(popupHtml, /body\.chat-tab-active \.footer \{ display: none; \}/);
});

test("pending confirmations stay bounded and scroll independently from chat history", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const pendingBlock = cssBlockWith(popupHtml, ".chat-pending", /max-height:/);
  const listBlock = cssBlockWith(popupHtml, ".chat-pending-list", /overflow-y:\s*auto;/);
  const messagesBlock = cssBlockWith(popupHtml, ".chat-messages", /overflow-y:\s*auto;/);

  assert.match(pendingBlock, /max-height:\s*min\(32vh,\s*240px\);/);
  assert.match(pendingBlock, /overflow:\s*hidden;/);
  assert.match(listBlock, /grid-auto-rows:\s*max-content;/);
  assert.match(listBlock, /overflow-y:\s*auto;/);
  assert.match(listBlock, /overscroll-behavior:\s*contain;/);
  assert.match(messagesBlock, /min-height:\s*0;/);
  assert.match(messagesBlock, /scrollbar-width:\s*thin;/);
  assert.match(
    popupHtml,
    /id="chatMessages"[^>]*role="region"[^>]*aria-label="口味对话记录"[^>]*tabindex="0"/,
  );
});

test("chat refresh preserves reader position and expanded evidence", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(
    popupJs,
    /function isChatMessagesNearBottom\(messages = elements\.chatMessages\)/,
  );
  assert.match(popupJs, /function openChatEvidenceTurnIds\(\)/);
  assert.match(popupJs, /const previousScrollTop = elements\.chatMessages\.scrollTop;/);
  assert.match(popupJs, /if \(openEvidence\.has\(turnId\)\) details\.open = true;/);
  assert.match(popupJs, /suppressChatAutoScroll = true;/);
  assert.match(popupJs, /else elements\.chatMessages\.scrollTop = previousScrollTop;/);
});

test("chat composer stays compact so short side panels keep room for messages", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatFormBlock = cssBlockWith(popupHtml, ".chat-form", /margin-top:\s*auto;/);
  const chatInputShellBlock = cssBlockWith(popupHtml, ".chat-input-shell", /padding:/);
  const chatComposeRowBlock = cssBlockWith(popupHtml, ".chat-compose-row", /display:\s*flex;/);
  const chatSendBlock = cssBlockWith(popupHtml, ".chat-send-button", /flex:\s*0\s+0\s+auto;/);
  const chatInputBlocks = [...popupHtml.matchAll(/\.chat-input\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const chatInputBlock = chatInputBlocks.at(-1) ?? "";
  const chatStatusEmptyBlock = popupHtml.match(/\.chat-status:empty\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatStatusFilledBlock =
    popupHtml.match(/\.chat-status:not\(:empty\)\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatMarkup = popupHtml.match(/<form id="chatForm"[\s\S]*?<\/form>/)?.[0] ?? "";

  assert.match(chatFormBlock, /gap:\s*6px;/);
  assert.match(chatFormBlock, /padding-top:\s*6px;/);
  assert.match(chatInputShellBlock, /gap:\s*0;/);
  assert.match(chatInputShellBlock, /padding:\s*6px\s+10px;/);
  assert.match(chatInputBlock, /min-height:\s*48px;/);
  assert.match(chatInputBlock, /max-height:\s*84px;/);
  assert.match(chatComposeRowBlock, /align-items:\s*stretch;/);
  assert.match(chatSendBlock, /flex:\s*0\s+0\s+auto;/);
  assert.match(chatStatusEmptyBlock, /display:\s*none;/);
  assert.match(chatStatusFilledBlock, /min-height:\s*16px;/);
  assert.match(chatMarkup, /class="chat-compose-row"/);
  assert.match(chatMarkup, /<textarea id="chatInput" class="chat-input" rows="2"/);
});

test("chat textarea keeps inner spacing and readable line height", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatInputBlocks = [...popupHtml.matchAll(/\.chat-input\s*\{[\s\S]*?\}/g)].map((match) => match[0]);
  const chatInputBlock = chatInputBlocks.at(-1) ?? "";

  assert.match(chatInputBlock, /padding:\s*7px\s+4px;/);
  assert.match(chatInputBlock, /line-height:\s*1\.6;/);
  assert.match(chatInputBlock, /border-radius:\s*14px;/);
});

test("chat tab folds marketing chrome and hides the recommendation footer", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const popupHelpers = readFileSync(resolve("popup", "popup-helpers.js"), "utf8");

  assert.match(popupHtml, /<details class="chat-controls is-chat-mode" id="chatControls">/);
  assert.match(popupHtml, /id="chatControlsMode">对话</);
  assert.match(popupHtml, /id="chatControlsSkill">口味伙伴</);
  assert.match(popupHtml, /id="chatControlsPersona">自然朋友</);
  assert.match(popupHtml, /class="chat-controls-pending"/);
  assert.match(popupHtml, /\.chat-controls > summary \{[\s\S]*?min-height: 34px;/);
  assert.match(popupHtml, /class="chat-agent-bar chat-only"/);
  assert.match(popupHtml, /class="chat-pending chat-only"/);
  assert.match(popupHtml, /\.chat-controls > summary \{/);
  assert.match(popupHtml, /\.chat-controls:not\(\.is-chat-mode\) \.chat-only \{ display: none; \}/);
  assert.match(popupHtml, /body\.chat-tab-active \.hero-sub \{ display: none; \}/);
  assert.match(popupHtml, /body\.chat-tab-active \.hero \{ padding: 6px 10px; \}/);
  assert.match(popupHtml, /body\.chat-tab-active \.brand-copy h1 \{ display: none; \}/);
  assert.match(popupHtml, /body\.chat-tab-active \.brand-mark \{ width: 26px; height: 26px;/);
  assert.match(popupHtml, /body\.chat-tab-active \.tab-button \{ min-height: 38px; padding: 6px 4px; \}/);
  assert.match(popupHtml, /body\.chat-tab-active \.footer \{ display: none; \}/);
  assert.match(popupJs, /classList\.toggle\("chat-tab-active", tabName === "chat"\)/);
  assert.match(popupJs, /classList\.toggle\("is-chat-mode", popupChatSubtab === "chat"\)/);
  assert.match(popupJs, /elements\.chatControlsMode\.textContent/);
  assert.match(popupJs, /elements\.chatControlsPending\.textContent/);
  assert.match(popupJs, /elements\.chatControlsSkill\.textContent/);
  assert.match(popupJs, /elements\.chatControlsPersona\.textContent/);
  assert.doesNotMatch(popupJs, /先跑 init、discover 或 recommend/);
  assert.match(popupJs, /暂时没有新候选，后台会继续补货。/);
  assert.doesNotMatch(popupHelpers, /先运行 init、discover 或 recommend/);
  assert.match(popupHelpers, /message: "暂时没有新候选，后台会继续补货。"/);
});

test("chat input placeholder rotation array and timer are defined", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /CHAT_PLACEHOLDERS\s*=\s*\[/);
  // At least 4 distinct placeholder hints
  const matches = popupJs.match(/比如：/g) ?? [];
  assert.ok(matches.length >= 4, `expected >=4 placeholder hints, got ${matches.length}`);
  assert.match(popupJs, /chatPlaceholderTimer/);
  assert.match(popupJs, /rotatePlaceholder/);
});

test("chat form reserves a dedicated status line for staged progress", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const chatStatusBlock = popupHtml.match(/\.chat-status\s*\{[\s\S]*?\}/)?.[0] ?? "";
  const chatMarkup = popupHtml.match(/<form id="chatForm"[\s\S]*?<\/form>/)?.[0] ?? "";

  assert.match(chatStatusBlock, /font-size:\s*11px;/);
  assert.match(chatMarkup, /id="chatStatus"/);
});

test("chat tab scrolls restored history to the newest message", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const setActiveTabBlock =
    popupJs.match(/function setActiveTab\([^)]*\) \{[\s\S]*?\n\}/)?.[0] ?? "";
  const hydrateBlock =
    popupJs.match(/async function hydrateChatHistory\(\) \{[\s\S]*?\n\}/)?.[0] ?? "";

  assert.match(popupJs, /function scrollChatMessagesToBottom\(\)/);
  assert.match(popupJs, /requestAnimationFrame/);
  assert.match(setActiveTabBlock, /tabName === "chat"[\s\S]*scrollChatMessagesToBottom\(\)/);
  assert.match(hydrateBlock, /replaceChildren\(\)[\s\S]*scrollChatMessagesToBottom\(\)/);
});

test("guess cards stay compact in the popup", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const cardBlock = cssBlockWith(popupHtml, ".dialogue-card", /border-radius:/);
  const titleBlocks = [...popupHtml.matchAll(/\.dialogue-card-title\s*\{[\s\S]*?\}/g)].map(
    (match) => match[0],
  );
  const titleBlock = titleBlocks.at(-1) ?? "";
  const actionsBlock = cssBlockWith(popupHtml, ".dialogue-card-actions", /margin-top:/);

  assert.match(cardBlock, /padding:\s*10px;/);
  assert.match(cardBlock, /border-radius:\s*14px;/);
  assert.match(titleBlock, /font-size:\s*12px;/);
  assert.match(titleBlock, /line-height:\s*1\.45;/);
  assert.match(actionsBlock, /margin-top:\s*8px;/);
});
